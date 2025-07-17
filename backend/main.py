# === backend/main.py ===
import os
import uvicorn
from contextlib import asynccontextmanager
from queue import Empty

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from serial_read import SerialHandler
from debug_tools import *

BASE_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_PATH = os.path.join(BASE_PATH, 'frontend')

# serial connection
ser: SerialHandler | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ser
    ser = SerialHandler()
    try:
        yield
    finally:
        ser.close()


app = FastAPI(lifespan=lifespan)

# serve static frontend from the frontend folder
app.mount("/static", StaticFiles(directory=FRONTEND_PATH, html=True), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

clients = []

# serve index manually
@app.get("/")
async def get_index():
    return FileResponse(os.path.join(FRONTEND_PATH, "index.html"))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    print("Client connected")

    try:
        while True:
            try:
                event = ser.events.get_nowait()
            except Empty:
                await asyncio.sleep(0.01)
                continue

            for client in clients[:]:
                try:
                    await client.send_json(event)
                except Exception as e:
                    print("Error sending event to sketch: ", e)
                    print("Removing stale client...")
                    clients.remove(client)

    except (WebSocketDisconnect, asyncio.CancelledError) as e:
        print(f"Client disconnected with {e}")
        if websocket in clients:
            clients.remove(websocket)
        await websocket.close(code=1001)


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)