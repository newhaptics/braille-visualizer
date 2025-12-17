import asyncio
import threading
import time
import subprocess
from typing import Awaitable, Callable, Optional
from signal import serialize, deserialize

EOT = b"EOT\n"
UCP_PROXY_PORT = 26541
SENTINEL = object()


class NexusClient:
    """Async TCP client with optional background-thread runner."""

    def __init__(
        self,
        on_printdisplay: Optional[Callable[[str], Awaitable[None]]],
        on_keystroke: Optional[Callable[[str], Awaitable[None]]],
        on_doubletap: Optional[Callable[[str], Awaitable[None]]],
        on_touch: Optional[Callable[[bytes], Awaitable[None]]],
    ) -> None:
        self.printdisplay_callback = on_printdisplay
        self.keystroke_callback = on_keystroke
        self.doubletap_callback = on_doubletap
        self.touch_callback = on_touch

        self._closed = asyncio.Event()
        self.out_queue: asyncio.Queue = asyncio.Queue()
        self._io_lock = asyncio.Lock()

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._write_task: Optional[asyncio.Task] = None

        # Runner bits (only used if you call start_background)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    # --------------------------- public async API ----------------------------

    async def connect(self, host: str = "localhost", port: int = UCP_PROXY_PORT) -> None:
        """Open the TCP connection and run read/write tasks until closed."""
        await asyncio.sleep(1)
        self._reader, self._writer = await asyncio.open_connection(host, port)

        self._read_task = asyncio.create_task(
            self._client_read_process(), name="nexus.read")
        self._write_task = asyncio.create_task(
            self._client_write_process(), name="nexus.write")

        try:
            await asyncio.gather(self._read_task, self._write_task)
        finally:
            await self._finalize_transport()

    async def close(self, timeout: float = 2.0) -> None:
        """Graceful shutdown."""
        if self._closed.is_set():
            return
        self._closed.set()

        # Wake writer to exit
        try:
            self.out_queue.put_nowait(SENTINEL)
        except Exception:
            pass

        # Cancel tasks
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if self._write_task and not self._write_task.done():
            self._write_task.cancel()

        # Wait briefly for tasks to end
        tasks = [t for t in (
            self._read_task, self._write_task) if t is not None]
        if tasks:
            try:
                await asyncio.wait(tasks, timeout=timeout)
            except Exception:
                pass

        await self._finalize_transport()

    # ----------------------- optional background runner ----------------------

    def start_background(self, host: str = "localhost", port: int = UCP_PROXY_PORT) -> None:
        """Start an event loop in a daemon thread and run connect() there."""
        if self._thread and self._thread.is_alive():
            return  # already running

        def _run():
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)

            # kick off connect() and keep the loop alive
            loop.create_task(self.connect(host=host, port=port))
            try:
                loop.run_forever()
            finally:
                # Drain/cancel pending tasks and close the loop
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                try:
                    loop.run_until_complete(asyncio.gather(
                        *pending, return_exceptions=True))
                except Exception:
                    pass
                loop.close()

        self._thread = threading.Thread(
            target=_run, name="NexusClientLoop", daemon=True)
        self._thread.start()

    def stop_background(self, timeout: float = 3.0) -> None:
        """Request shutdown and stop the loop thread."""
        loop = self._loop
        if not loop:
            return

        # Ask the async client to close
        fut = asyncio.run_coroutine_threadsafe(self.close(), loop)
        try:
            fut.result(timeout=timeout)
        except Exception:
            pass

        # Stop the loop and join the thread
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass

        if self._thread:
            self._thread.join(timeout=timeout)

        self._loop = None
        self._thread = None

    # ------------------------------ internals --------------------------------

    async def _client_read_process(self) -> None:
        reader = self._reader
        assert reader is not None
        try:
            while not self._closed.is_set():
                data = await reader.readuntil(EOT)
                event_id, payload = deserialize(data)
                if event_id == 0x00 and self.printdisplay_callback:
                    await self.printdisplay_callback(payload)
                elif event_id == 0x01 and self.doubletap_callback:
                    await self.doubletap_callback(payload)
                elif event_id == 0x02 and self.keystroke_callback:
                    await self.keystroke_callback(payload)
                elif event_id == 0x04 and self.touch_callback:
                    await self.touch_callback(payload)
        except (asyncio.CancelledError, asyncio.IncompleteReadError):
            pass  # expected on shutdown
        except Exception as e:
            print(f"[NexusClient] Read error: {e}")

    async def _client_write_process(self) -> None:
        writer = self._writer
        assert writer is not None
        try:
            while True:
                item = await self.out_queue.get()
                if item is SENTINEL:
                    break
                data = serialize(item)
                async with self._io_lock:
                    writer.write(data)
                    await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[NexusClient] Write error: {e}")

    async def _finalize_transport(self) -> None:
        w = self._writer
        self._writer = None
        self._reader = None
        if w is not None:
            try:
                w.close()
                # await asyncio.wait_for(w.wait_closed(), timeout=1.0)
            except Exception:
                pass