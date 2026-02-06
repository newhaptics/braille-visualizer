# === backend/main.py ===
import os
import sys
import uvicorn
import asyncio
from contextlib import asynccontextmanager
from queue import Queue, Empty

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from nexus_client import NexusClient
from nexus_signals import DoubleTap, Touch, PrintDisplay, Keystroke
from braille_conversion import braille_string_to_matrix

# When running as a PyInstaller bundle, sys._MEIPASS points to the temp
# directory where bundled data files are extracted.  In normal (unfrozen)
# mode we keep the original behaviour.
if getattr(sys, 'frozen', False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_PATH = os.path.join(BASE_PATH, 'frontend')

# ============================================================================
# SHARED STATE (kept simple and clear)
# ============================================================================
events = Queue(maxsize=1000)  # Bounded queue prevents memory leaks
clients = []
clients_lock = asyncio.Lock()
nexus = None


# ============================================================================
# NEXUS CALLBACKS - Convert events and put in queue
# ============================================================================
async def on_printdisplay(payload):
    try:
        pd = PrintDisplay.from_payload(payload)

        # Convert braille string to 20×96 matrix
        matrix = braille_string_to_matrix(pd.string)

        # Send matrix to frontend
        event = {
            'type': 'matrix',
            'mat': matrix
        }
        try:
            events.put_nowait(event)
        except:
            try:
                events.get_nowait()
                events.put_nowait(event)
            except:
                pass
    except Exception as e:
        print(f"[ERROR] PrintDisplay callback: {e}")


async def on_touch(payload):
    try:
        touch = Touch.from_payload(payload)
        event = {
            'type': 'touch',
            'action': touch.action,
            'id': touch.id,
            'x': touch.x,
            'y': touch.y
        }
        # Non-blocking put - if queue is full, drop oldest
        try:
            events.put_nowait(event)
        except:
            # Queue full, discard oldest and add new
            try:
                events.get_nowait()
                events.put_nowait(event)
            except:
                pass
    except Exception as e:
        print(f"[ERROR] Touch callback: {e}")


async def on_doubletap(payload):
    try:
        dt = DoubleTap.from_payload(payload)
        print(f"Received DoubleTap at {dt.row}, {dt.column}")
        event = {
            'type': 'double tap',
            'row': dt.row,
            'column': dt.column
        }
        try:
            events.put_nowait(event)
        except:
            try:
                events.get_nowait()
                events.put_nowait(event)
            except:
                pass
    except Exception as e:
        print(f"[ERROR] DoubleTap callback: {e}")


async def on_keystroke(payload):
    try:
        ks = Keystroke.from_payload(payload)
        print(f"[Keystroke] {ks.value}")
    except Exception as e:
        print(f"[ERROR] Keystroke callback: {e}")


# ============================================================================
# APP LIFECYCLE
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global nexus

    print("=" * 60)
    print("Starting application...")
    print("=" * 60)

    # Create and start NexusClient
    nexus = NexusClient(
        on_printdisplay=on_printdisplay,
        on_keystroke=on_keystroke,
        on_doubletap=on_doubletap,
        on_touch=on_touch
    )

    print("Starting NexusClient connection...")
    nexus.start_background()

    # Give it a moment to connect
    await asyncio.sleep(2)
    print("NexusClient started")
    print("Application ready!")
    print("=" * 60)

    yield

    # Shutdown
    print("=" * 60)
    print("Shutting down application...")
    print("=" * 60)

    if nexus:
        print("Stopping NexusClient...")
        nexus.stop_background()
        print("NexusClient stopped")

    print("Shutdown complete")
    print("=" * 60)


# ============================================================================
# FASTAPI APP
# ============================================================================
app = FastAPI(lifespan=lifespan)

# Serve static files
app.mount("/static", StaticFiles(directory=FRONTEND_PATH, html=True), name="static")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Lock this down in production!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# ROUTES
# ============================================================================
@app.get("/")
async def get_index():
    """Serve the frontend"""
    return FileResponse(os.path.join(FRONTEND_PATH, "index.html"))


@app.get("/health")
async def health_check():
    """Check if the app is healthy"""
    return {
        "status": "ok",
        "nexus_connected": nexus is not None,
        "clients_connected": len(clients),
        "events_queued": events.qsize()
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint - streams events to frontend"""
    await websocket.accept()

    async with clients_lock:
        clients.append(websocket)

    print(f"[WebSocket] Client connected (total: {len(clients)})")

    async def _receive_loop():
        """Wait for client disconnect (we don't expect messages, just detect close)."""
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    async def _send_loop():
        """Poll the event queue and broadcast to all clients."""
        while True:
            try:
                event = events.get_nowait()
            except Empty:
                await asyncio.sleep(0.01)
                continue

            async with clients_lock:
                dead_clients = []
                for client in clients:
                    try:
                        await client.send_json(event)
                    except Exception:
                        dead_clients.append(client)
                for dead in dead_clients:
                    try:
                        await dead.close()
                    except Exception:
                        pass
                    if dead in clients:
                        clients.remove(dead)

    recv_task = asyncio.create_task(_receive_loop())
    send_task = asyncio.create_task(_send_loop())

    try:
        # Wait for EITHER task to finish (receive exits on disconnect)
        done, pending = await asyncio.wait(
            [recv_task, send_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        # Cancel whichever is still running
        for task in pending:
            task.cancel()
    except Exception as e:
        print(f"[WebSocket] Error: {e}")
    finally:
        async with clients_lock:
            if websocket in clients:
                clients.remove(websocket)
        print(f"[WebSocket] Client disconnected (remaining: {len(clients)})")


# ============================================================================
# RUN
# ============================================================================
if __name__ == "__main__":
    frozen = getattr(sys, 'frozen', False)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=not frozen)
