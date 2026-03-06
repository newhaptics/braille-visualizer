# Codex Visualizer Relay
# NexusProxy TCP <-> Browser WebSocket bridge
#
# Architecture:
#   Codex <-USB serial-> NexusProxy:26541 <-TCP (SIG frames)-> This relay <-WS (JSON)-> Browser(s)

import os
import json
import struct
import time
import asyncio
import socket
import logging
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("codex-visualizer")

# ============================================================================
# CONFIG
# ============================================================================
NEXUSPROXY_HOST = os.environ.get("NEXUSPROXY_HOST", "127.0.0.1")
NEXUSPROXY_PORT = int(os.environ.get("NEXUSPROXY_PORT", "26541"))
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# ============================================================================
# SHARED STATE
# ============================================================================
nexus_connected: bool = False
browser_queues: list[asyncio.Queue] = []
browser_queues_lock = asyncio.Lock()
_nexus_task: asyncio.Task | None = None

# ============================================================================
# SIG FRAME PROTOCOL
# ============================================================================
SIG_PRINT_DISPLAY      = 0x00
SIG_DOUBLE_TAP         = 0x01
SIG_KEYSTROKE          = 0x02
SIG_UCP_TOUCH          = 0x04
SIG_TWO_FINGER_DTAP    = 0x06
SIG_TWO_FINGER_SWIPE   = 0x07
SIG_THREE_FINGER_SWIPE = 0x08
SIG_FOUR_FINGER_SWIPE  = 0x09
SIG_FOUR_FINGER_PINCH  = 0x0A
SIG_FOUR_FINGER_SPREAD = 0x0B
SIG_EIGHT_FINGER_DTAP  = 0x0C
SIG_EIGHT_FINGER_HOLD  = 0x0D
SIG_ONE_FINGER_TTAP    = 0x0E

DIRECTION_MAP = {0: "left", 1: "right", 2: "up", 3: "down"}
TOUCH_ACTION_MAP = {1: "down", 2: "up", 3: "move"}

_TOUCH_FMT = "BHHBBB"
_TOUCH_SIZE = struct.calcsize(_TOUCH_FMT)


def sig_deframe(data: bytes) -> tuple[int, bytes]:
    if len(data) < 8 or data[:3] != b"SIG" or data[-4:] != b"EOT\n":
        raise ValueError(f"Invalid SIG frame ({len(data)} bytes)")
    return data[3], data[4:-4]


# ============================================================================
# SIGNAL DECODERS
# ============================================================================
def decode_print_display(payload: bytes) -> dict:
    return {"type": "PrintDisplay", "string": payload.decode("utf-8", errors="replace")}


def decode_double_tap(payload: bytes) -> dict:
    row, col = struct.unpack("BB", payload[:2])
    return {"type": "DoubleTap", "position": [row, col]}


def decode_keystroke(payload: bytes) -> dict:
    if not payload:
        return {"type": "Keystroke", "value": []}
    key_count = payload[0]
    keys, i = [], 1
    for _ in range(key_count):
        if i >= len(payload):
            break
        key_len = payload[i]
        keys.append(payload[i + 1 : i + 1 + key_len].decode("utf-8", errors="replace"))
        i += 1 + key_len
    return {"type": "Keystroke", "value": keys}


def decode_ucp_touch(payload: bytes) -> dict:
    if len(payload) >= _TOUCH_SIZE:
        action_code, x, y, fid, amp, area = struct.unpack(_TOUCH_FMT, payload[:_TOUCH_SIZE])
    else:
        # Fallback for older 6-byte format without amp/area
        action_code, x, y, fid = struct.unpack("BHHB", payload[:6])
        amp, area = 0, 0
    return {
        "type": "Touch",
        "action": TOUCH_ACTION_MAP.get(action_code, f"unknown({action_code})"),
        "id": fid, "x": x, "y": y,
    }


def decode_two_finger_dtap(payload: bytes) -> dict:
    return {"type": "TwoFingerDoubleTap"}


def decode_swipe(name: str):
    def decoder(payload: bytes) -> dict:
        direction = DIRECTION_MAP.get(payload[0], "unknown") if payload else "unknown"
        return {"type": name, "direction": direction}
    return decoder


def decode_four_finger_pinch(payload: bytes) -> dict:
    return {"type": "FourFingerPinch"}


def decode_four_finger_spread(payload: bytes) -> dict:
    return {"type": "FourFingerSpread"}


def decode_eight_finger_dtap(payload: bytes) -> dict:
    return {"type": "EightFingerDoubleTap"}


def decode_eight_finger_hold(payload: bytes) -> dict:
    if not payload:
        return {"type": "EightFingerHold", "positions": []}
    count = payload[0]
    positions = []
    for j in range(count):
        off = 1 + j * 4
        if off + 4 <= len(payload):
            x = (payload[off] << 8) | payload[off + 1]
            y = (payload[off + 2] << 8) | payload[off + 3]
            positions.append([x, y])
    return {"type": "EightFingerHold", "positions": positions}


def decode_one_finger_ttap(payload: bytes) -> dict:
    row, col = struct.unpack("BB", payload[:2])
    return {"type": "OneFingerTripleTap", "position": [row, col]}


DECODERS = {
    SIG_PRINT_DISPLAY:      decode_print_display,
    SIG_DOUBLE_TAP:         decode_double_tap,
    SIG_KEYSTROKE:          decode_keystroke,
    SIG_UCP_TOUCH:          decode_ucp_touch,
    SIG_TWO_FINGER_DTAP:    decode_two_finger_dtap,
    SIG_TWO_FINGER_SWIPE:   decode_swipe("TwoFingerSwipe"),
    SIG_THREE_FINGER_SWIPE: decode_swipe("ThreeFingerSwipe"),
    SIG_FOUR_FINGER_SWIPE:  decode_swipe("FourFingerSwipe"),
    SIG_FOUR_FINGER_PINCH:  decode_four_finger_pinch,
    SIG_FOUR_FINGER_SPREAD: decode_four_finger_spread,
    SIG_EIGHT_FINGER_DTAP:  decode_eight_finger_dtap,
    SIG_EIGHT_FINGER_HOLD:  decode_eight_finger_hold,
    SIG_ONE_FINGER_TTAP:    decode_one_finger_ttap,
}


# ============================================================================
# BROADCAST
# ============================================================================
async def broadcast(message: dict):
    for q in browser_queues:
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass


# ============================================================================
# NEXUSPROXY TCP CLIENT
# ============================================================================
async def nexus_tcp_loop():
    global nexus_connected
    backoff = 1.0

    while True:
        writer = None
        try:
            logger.info("Connecting to NexusProxy at %s:%d ...", NEXUSPROXY_HOST, NEXUSPROXY_PORT)
            reader, writer = await asyncio.open_connection(NEXUSPROXY_HOST, NEXUSPROXY_PORT)

            sock = writer.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            nexus_connected = True
            backoff = 1.0
            logger.info("NexusProxy connected")
            await broadcast({"type": "_device_status", "connected": True})

            while True:
                data = await reader.readuntil(b"EOT\n")
                if not data:
                    break

                try:
                    signal_id, payload = sig_deframe(data)
                except ValueError as e:
                    logger.warning("Bad SIG frame: %s", e)
                    continue

                decoder = DECODERS.get(signal_id)
                if decoder is None:
                    continue

                message = decoder(payload)
                message["timestamp"] = time.time()
                await broadcast(message)

        except asyncio.CancelledError:
            break
        except (ConnectionRefusedError, OSError) as e:
            logger.warning("NexusProxy connection failed: %s", e)
        except asyncio.IncompleteReadError:
            logger.warning("NexusProxy connection closed")
        except Exception as e:
            logger.error("NexusProxy error: %s", e)
        finally:
            if nexus_connected:
                nexus_connected = False
                await broadcast({"type": "_device_status", "connected": False})
                logger.info("NexusProxy disconnected")
            if writer is not None:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        logger.info("Reconnecting in %.0fs ...", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


# ============================================================================
# APP LIFECYCLE
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _nexus_task
    logger.info("=" * 60)
    logger.info("Codex Visualizer Relay")
    logger.info("  NexusProxy: %s:%d", NEXUSPROXY_HOST, NEXUSPROXY_PORT)
    logger.info("  Frontend:   %s", FRONTEND_DIR)
    logger.info("=" * 60)

    _nexus_task = asyncio.create_task(nexus_tcp_loop())
    yield

    logger.info("Shutting down...")
    _nexus_task.cancel()
    try:
        await _nexus_task
    except asyncio.CancelledError:
        pass


# ============================================================================
# FASTAPI APP
# ============================================================================
app = FastAPI(title="Codex Visualizer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    async with browser_queues_lock:
        browser_queues.append(queue)

    logger.info("Browser connected (total: %d)", len(browser_queues))

    # Send current device status
    try:
        await websocket.send_json({"type": "_device_status", "connected": nexus_connected})
    except Exception:
        pass

    async def send_loop():
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)

    async def recv_loop():
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    send_task = asyncio.create_task(send_loop())
    recv_task = asyncio.create_task(recv_loop())

    try:
        done, pending = await asyncio.wait(
            [send_task, recv_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    except Exception as e:
        logger.error("Browser connection error: %s", e)
    finally:
        async with browser_queues_lock:
            if queue in browser_queues:
                browser_queues.remove(queue)
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("Browser disconnected (remaining: %d)", len(browser_queues))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "nexus_connected": nexus_connected,
        "browser_clients": len(browser_queues),
    }


if __name__ == "__main__":
    port = int(os.environ.get("RELAY_PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
