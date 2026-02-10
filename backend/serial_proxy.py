"""Serial-to-TCP proxy for UCP frames.

Bridges the SOM's USB gadget serial (COM port on Windows) to a TCP socket
that NexusClient can connect to. UCP frames are delimited by b"EOT\n".

Usage:
    py serial_proxy.py COM67          # defaults to TCP port 26541
    py serial_proxy.py COM67 26541    # explicit port
"""

import asyncio
import sys
import serial_asyncio

EOT = b"EOT\n"
DEFAULT_PORT = 26541
BAUD_RATE = 115200


def log(msg):
    print(msg, flush=True)


class SerialProxy:
    def __init__(self, com_port: str, tcp_port: int):
        self.com_port = com_port
        self.tcp_port = tcp_port
        self.clients: list[asyncio.StreamWriter] = []
        self.serial_writer = None

    async def start(self):
        # Open serial port
        log(f"[Proxy] Opening {self.com_port} at {BAUD_RATE} baud...")
        self.serial_reader, self.serial_writer = (
            await serial_asyncio.open_serial_connection(
                url=self.com_port, baudrate=BAUD_RATE
            )
        )
        log(f"[Proxy] Serial port {self.com_port} opened")

        # Start TCP server
        server = await asyncio.start_server(
            self._handle_client, "0.0.0.0", self.tcp_port
        )
        log(f"[Proxy] TCP server listening on port {self.tcp_port}")

        # Read from serial, forward to TCP clients
        serial_task = asyncio.create_task(self._serial_read_loop())

        async with server:
            await asyncio.gather(server.serve_forever(), serial_task)

    async def _serial_read_loop(self):
        """Read UCP frames from serial and forward to all TCP clients."""
        try:
            while True:
                data = await self.serial_reader.readuntil(EOT)
                log(f"[Proxy] Serial frame: {len(data)} bytes, {len(self.clients)} clients")
                dead = []
                for writer in self.clients:
                    try:
                        writer.write(data)
                        await writer.drain()
                    except Exception:
                        dead.append(writer)
                for w in dead:
                    self.clients.remove(w)
                    try:
                        w.close()
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log(f"[Proxy] Serial read error: {e}")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        log(f"[Proxy] TCP client connected: {addr}")
        self.clients.append(writer)

        try:
            while True:
                data = await reader.readuntil(EOT)
                if self.serial_writer:
                    self.serial_writer.write(data)
                    await self.serial_writer.drain()
        except (asyncio.CancelledError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            log(f"[Proxy] TCP client {addr} error: {e}")
        finally:
            if writer in self.clients:
                self.clients.remove(writer)
            try:
                writer.close()
            except Exception:
                pass
            log(f"[Proxy] TCP client disconnected: {addr}")


async def main():
    if len(sys.argv) < 2:
        log(f"Usage: py serial_proxy.py <COM_PORT> [TCP_PORT]")
        log(f"  Example: py serial_proxy.py COM67")
        sys.exit(1)

    com_port = sys.argv[1]
    tcp_port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT

    proxy = SerialProxy(com_port, tcp_port)
    await proxy.start()


if __name__ == "__main__":
    asyncio.run(main())
