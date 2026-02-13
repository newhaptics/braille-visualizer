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
from pydantic import BaseModel

from nexus_client import NexusClient
from nexus_signals import DoubleTap, Touch, PrintDisplay, Keystroke, SetDotMatrix
from braille_conversion import braille_string_to_matrix
from i2c_controller import I2CController
from profiles import (
    get_builtin_presets, list_profiles, load_profile,
    save_profile, delete_profile, TouchProfile,
)
from serial_proxy import SerialProxy

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
i2c = I2CController()
proxy_task = None
_xcfg_cache = None  # Cached xcfg text from SOM, refreshed on SSH connect


# ============================================================================
# COM PORT AUTO-DETECTION
# ============================================================================
CODEX_VID_PID = "376B:0001"  # Codex USB gadget serial

def find_codex_port() -> str | None:
    """Auto-detect the Codex USB serial port by VID:PID."""
    try:
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            if CODEX_VID_PID.lower() in (port.hwid or "").lower():
                return port.device
    except ImportError:
        pass
    return None


# ============================================================================
# NEXUS CALLBACKS - Convert events and put in queue
# ============================================================================
async def on_printdisplay(payload):
    try:
        pd = PrintDisplay.from_payload(payload)

        # Convert braille string to 20×96 matrix
        matrix = braille_string_to_matrix(pd.string)

        # Send matrix + raw braille text to frontend
        event = {
            'type': 'matrix',
            'mat': matrix,
            'braille': pd.string,
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
            'y': touch.y,
            'amp': touch.amp,
            'area': touch.area,
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


async def on_setdotmatrix(payload):
    try:
        sdm = SetDotMatrix.from_payload(payload)
        event = {
            'type': 'matrix',
            'mat': sdm.matrix
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
        print(f"[ERROR] SetDotMatrix callback: {e}")


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
    global nexus, proxy_task

    print("=" * 60)
    print("Starting application...")
    print("=" * 60)

    # Auto-detect and start serial proxy
    com_port = os.environ.get("COM_PORT") or find_codex_port()
    if com_port:
        print(f"[Proxy] Auto-detected Codex on {com_port}")
        proxy = SerialProxy(com_port, 26541)
        proxy_task = asyncio.create_task(proxy.start())
        await asyncio.sleep(1)  # let proxy bind TCP port
    else:
        print("[Proxy] No Codex USB serial found — running without serial proxy")
        print("[Proxy] Set COM_PORT env var or connect the device via USB")

    # Create and start NexusClient
    nexus = NexusClient(
        on_printdisplay=on_printdisplay,
        on_keystroke=on_keystroke,
        on_doubletap=on_doubletap,
        on_touch=on_touch,
        on_setdotmatrix=on_setdotmatrix
    )

    print("Starting NexusClient connection...")
    nexus.start_background()

    # Give it a moment to connect
    await asyncio.sleep(2)
    print("NexusClient started")

    # Try to connect I2C controller if SOM_HOST is set
    som_host = os.environ.get("SOM_HOST")
    if som_host:
        try:
            await i2c.connect(som_host)
        except Exception as e:
            print(f"[I2C] Could not connect at startup: {e}")

    print("Application ready!")
    print("=" * 60)

    yield

    # Shutdown
    print("=" * 60)
    print("Shutting down application...")
    print("=" * 60)

    if i2c.connected:
        await i2c.disconnect()

    if nexus:
        print("Stopping NexusClient...")
        nexus.stop_background()
        print("NexusClient stopped")

    if proxy_task and not proxy_task.done():
        proxy_task.cancel()
        try:
            await proxy_task
        except asyncio.CancelledError:
            pass
        print("Serial proxy stopped")

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


# ============================================================================
# I2C REGISTER ENDPOINTS
# ============================================================================
class SSHConnectRequest(BaseModel):
    host: str
    username: str = "root"

class RegisterWriteRequest(BaseModel):
    value: int

@app.get("/api/ssh/status")
async def ssh_status():
    """Check SSH connection status."""
    return {
        "connected": i2c.connected,
        "host": i2c.host,
    }


@app.post("/api/ssh/connect")
async def ssh_connect(req: SSHConnectRequest):
    """Connect to the SOM via SSH and cache the xcfg file."""
    global _xcfg_cache
    try:
        await i2c.connect(req.host, username=req.username)
        # Cache xcfg for factory default preset
        _xcfg_cache = await i2c.read_xcfg_file()
        return {"status": "connected", "host": req.host}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/ssh/disconnect")
async def ssh_disconnect():
    """Disconnect SSH."""
    await i2c.disconnect()
    return {"status": "disconnected"}

@app.get("/api/registers")
async def get_registers():
    """Read all register values grouped by object."""
    if not i2c.connected:
        return {"error": "Not connected to SOM"}
    try:
        values = await i2c.read_all()
        return {"registers": values}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/registers/info")
async def get_register_info():
    """Get register metadata grouped by object for the frontend."""
    return {"registers": i2c.get_register_info()}

class RegisterWriteAllRequest(BaseModel):
    registers: dict  # {"T100": {"TCHTHR": 40, ...}, "T42": {...}, ...}

# IMPORTANT: Literal routes must be defined BEFORE parameterized routes
# so FastAPI doesn't match "write-all" or "defaults" as a {name} parameter.

@app.post("/api/registers/write-all")
async def write_all_registers(req: RegisterWriteAllRequest):
    """Write all registers from a grouped config dict."""
    if not i2c.connected:
        return {"error": "Not connected to SOM"}
    try:
        results = await i2c.write_all(req.registers)
        return {"registers": results}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/registers/defaults")
async def restore_defaults():
    """Restore all registers to factory defaults."""
    if not i2c.connected:
        return {"error": "Not connected to SOM"}
    try:
        results = await i2c.restore_defaults()
        return {"registers": results}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/registers/{obj}/{name}")
async def write_register_scoped(obj: str, name: str, req: RegisterWriteRequest):
    """Write a single register scoped to an object (e.g., T42/CTRL)."""
    if not i2c.connected:
        return {"error": "Not connected to SOM"}
    obj = obj.upper()
    name = name.upper()
    try:
        await i2c.write_register(name, req.value, obj)
        actual = await i2c.read_register(name, obj)
        return {"object": obj, "register": name, "written": req.value, "readback": actual}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/registers/{name}")
async def write_register(name: str, req: RegisterWriteRequest):
    """Write a single register (flat lookup, backward compatible)."""
    if not i2c.connected:
        return {"error": "Not connected to SOM"}
    name = name.upper()
    try:
        await i2c.write_register(name, req.value)
        actual = await i2c.read_register(name)
        return {"register": name, "written": req.value, "readback": actual}
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# PROFILE ENDPOINTS
# ============================================================================
class ProfileSaveRequest(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
    registers: dict = {}

class ProfileUpdateRequest(BaseModel):
    name: str = None
    description: str = None
    tags: list[str] = None
    registers: dict = None

@app.get("/api/profiles")
async def get_profiles():
    """List all profiles (built-in presets + saved)."""
    builtins = get_builtin_presets(_xcfg_cache)
    builtin_list = [
        {
            "filename": f"__builtin_{i}__",
            "name": p.name,
            "description": p.description,
            "tags": p.tags,
            "builtin": True,
        }
        for i, p in enumerate(builtins)
    ]
    saved = list_profiles()
    return {"profiles": builtin_list + saved}

@app.get("/api/profiles/{filename}")
async def get_profile(filename: str):
    """Load a specific profile."""
    # Check if it's a built-in
    if filename.startswith("__builtin_") and filename.endswith("__"):
        idx = int(filename[10:-2])
        builtins = get_builtin_presets(_xcfg_cache)
        if 0 <= idx < len(builtins):
            p = builtins[idx]
            return {"profile": p.to_dict(), "builtin": True}
        return {"error": "Built-in preset not found"}
    try:
        p = load_profile(filename)
        return {"profile": p.to_dict(), "builtin": False}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/profiles")
async def create_profile(req: ProfileSaveRequest):
    """Save a new profile."""
    profile = TouchProfile(
        name=req.name,
        description=req.description,
        tags=req.tags,
        registers=req.registers,
    )
    filename = save_profile(profile)
    return {"status": "saved", "filename": filename}

@app.put("/api/profiles/{filename}")
async def update_profile(filename: str, req: ProfileUpdateRequest):
    """Update an existing saved profile."""
    if filename.startswith("__builtin_"):
        return {"error": "Cannot modify built-in presets"}
    try:
        profile = load_profile(filename)
        if req.name is not None:
            profile.name = req.name
        if req.description is not None:
            profile.description = req.description
        if req.tags is not None:
            profile.tags = req.tags
        if req.registers is not None:
            profile.registers = req.registers
        save_profile(profile, filename)
        return {"status": "updated", "filename": filename}
    except Exception as e:
        return {"error": str(e)}

@app.delete("/api/profiles/{filename}")
async def remove_profile(filename: str):
    """Delete a saved profile."""
    if filename.startswith("__builtin_"):
        return {"error": "Cannot delete built-in presets"}
    if delete_profile(filename):
        return {"status": "deleted"}
    return {"error": "Profile not found"}

@app.post("/api/profiles/{filename}/apply")
async def apply_profile(filename: str):
    """Load a profile and write all its registers to the device."""
    if not i2c.connected:
        return {"error": "Not connected to SOM"}
    # Load profile
    if filename.startswith("__builtin_") and filename.endswith("__"):
        idx = int(filename[10:-2])
        builtins = get_builtin_presets(_xcfg_cache)
        if 0 <= idx < len(builtins):
            profile = builtins[idx]
        else:
            return {"error": "Built-in preset not found"}
    else:
        try:
            profile = load_profile(filename)
        except Exception as e:
            return {"error": str(e)}
    # Write all registers
    try:
        results = await i2c.write_all(profile.registers)
        return {"status": "applied", "name": profile.name, "registers": results}
    except Exception as e:
        return {"error": str(e)}


# ============================================================================
# SOM DEPLOY / RUN / STOP
# ============================================================================
DEPLOY_CMD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".vscode", "deploy.cmd"
)

@app.post("/api/som/deploy-and-run")
async def som_deploy_and_run():
    """Deploy all repos to SOM and start dev instance."""
    if not os.path.exists(DEPLOY_CMD):
        return {"status": "error", "output": f"deploy.cmd not found at {DEPLOY_CMD}"}
    try:
        proc = await asyncio.create_subprocess_exec(
            "cmd.exe", "/C", DEPLOY_CMD,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            return {"status": "error", "output": output}
        # Now run the dev instance
        run_proc = await asyncio.create_subprocess_exec(
            "ssh", "som", "/tmp/hapticos-dev/run.sh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        run_out, _ = await asyncio.wait_for(run_proc.communicate(), timeout=30)
        output += "\n" + run_out.decode("utf-8", errors="replace")
        return {"status": "ok", "output": output}
    except asyncio.TimeoutError:
        return {"status": "error", "output": "Deploy timed out (120s)"}
    except Exception as e:
        return {"status": "error", "output": str(e)}

@app.post("/api/som/run")
async def som_run():
    """Start the dev instance on SOM (assumes already deployed)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "som", "/tmp/hapticos-dev/run.sh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {"status": "ok", "output": stdout.decode("utf-8", errors="replace")}
    except asyncio.TimeoutError:
        return {"status": "error", "output": "Timed out (30s)"}
    except Exception as e:
        return {"status": "error", "output": str(e)}

@app.post("/api/som/stop")
async def som_stop():
    """Stop the dev instance on SOM."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "som", "/tmp/hapticos-dev/stop.sh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return {"status": "ok", "output": stdout.decode("utf-8", errors="replace")}
    except asyncio.TimeoutError:
        return {"status": "error", "output": "Timed out (30s)"}
    except Exception as e:
        return {"status": "error", "output": str(e)}


# ============================================================================
# WEBSOCKET
# ============================================================================
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
    port = int(os.environ.get("PORT", 8001))
    if frozen:
        # Frozen exe can't import "main" by name — pass the app object directly
        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        # Dev mode: string form required for reload to work
        uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
