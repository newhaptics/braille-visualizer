# === backend/serial_read.py ===
import serial
import threading
import numpy as np
import queue

from gestures import GestureDetector
from serial_helper import *

SERIAL_PORT = 'COM5'
BAUDRATE = 115200


class SerialHandler:
    def __init__(self, port: str = SERIAL_PORT, baudrate: int = BAUDRATE):
        # Initialize the pyserial connection
        try:
            self.ser = serial.Serial(port, baudrate, timeout=0.1)
        except Exception as e:
            raise RuntimeError(f"Failed to open serial port {port}: {e}")

        self.running = True
        self.matrix = None            # Last received 20×96 matrix
        self.events = queue.Queue()   # Thread‐safe queue of decoded events

        self.gesture_detector = GestureDetector()
        self.lock = threading.Lock()

        # Start the read thread
        self.read_thread = threading.Thread(target=self.read_loop, daemon=True)
        self.read_thread.start()

    def read_bytes(self, n: int) -> bytes:
        """
        Read exactly n bytes from self.ser; blocks until all n are received.
        """
        data = b''
        while len(data) < n and self.running:
            chunk = self.ser.read(n - len(data))
            if not chunk:
                # Timeout occurred—loop until we get enough bytes or self.running is False
                continue
            data += chunk
        return data

    def sync_header(self) -> int:
        """
        Synchronize on the SOF byte (0xAA). Returns the next packet type (1..4).
        Blocks until a valid SOF+type sequence is found.
        """
        while self.running:
            # Read one byte at a time until we see 0xAA
            first = self.ser.read(1)
            if not first:
                continue  # timeout—keep trying
            if first[0] != SOF_BYTE:
                continue

            # If we got 0xAA, read the next byte for packet type
            second = self.ser.read(1)
            if not second:
                continue  # didn’t get type—go back to searching for SOF
            ptype = second[0]
            if ptype in (EVENT_DOWN, EVENT_UP, EVENT_MOVE, EVENT_MATRIX):
                return ptype
            # Otherwise, byte was 0xAA but next byte not in our set—resync
        return -1  # if self.running becomes False

    def read_loop(self):
        """
        Main background thread. Continuously:
          1) sync_header() → packet_type
          2) read the rest of that packet (length depends on type)
          3) validate CRC
          4) decode fields & enqueue into self.events
        """
        while self.running:
            try:
                ptype = self.sync_header()
            except Exception as e:
                print(f"[SerialHandler] sync_header() error: {e}")
                continue

            if not self.running or ptype < 0:
                break

            # ――― Touch events (8 bytes total) ―――
            if ptype in (EVENT_DOWN, EVENT_UP, EVENT_MOVE):
                # We have already consumed SOF (0xAA) and type=ptype,
                # so we need to read: x(2 bytes), y(2 bytes), fid(1 byte), crc(1 byte)
                rest = self.read_bytes(6)
                if len(rest) < 6:
                    continue  # incomplete; loop again

                x_bytes = rest[0:2]
                y_bytes = rest[2:4]
                fid_byte = rest[4:5]
                recv_crc = rest[5]

                header = bytes([SOF_BYTE, ptype]) + \
                    x_bytes + y_bytes + fid_byte
                calc_crc = crc8(header)
                if recv_crc != calc_crc:
                    print(
                        f"[SerialHandler] Touch CRC mismatch: expected 0x{calc_crc:02X}, got 0x{recv_crc:02X}")
                    continue  # drop this packet

                # Parse coordinates (big-endian)
                x = int.from_bytes(x_bytes, byteorder='little', signed=False)
                y = int.from_bytes(y_bytes, byteorder='little', signed=False)
                fid = fid_byte[0]

                # If it's MOVE, you might run gesture detection
                if ptype == EVENT_MOVE:
                    gesture = self.gesture_detector.update(fid, x, y)
                else:
                    gesture = None

                action = {EVENT_DOWN: "down", EVENT_UP: "up",
                          EVENT_MOVE: "move"}[ptype]
                event = {
                    'type':    'touch',
                    'action':  action,
                    'id':      fid,
                    'x':       x,
                    'y':       y
                }
                if gesture is not None:
                    event['gesture'] = gesture

                self.events.put(event)

            # ――― Matrix events (247 bytes total) ―――
            elif ptype == EVENT_MATRIX:
                # We have consumed SOF (0xAA) and type=4, so read:
                # rows(1), cols(1), payload_len(2), payload(payload_len), crc(1)
                header_rest = self.read_bytes(4)
                if len(header_rest) < 4:
                    continue  # incomplete

                rows = header_rest[0]
                cols = header_rest[1]
                plen = int.from_bytes(
                    header_rest[2:4], 'little')  # little-endian
                if (rows, cols) != (20, 96) or plen != 240:
                    # Unexpected shape/length; try to resync by skipping plen+1 bytes
                    _ = self.read_bytes(plen + 1)
                    print(
                        f"[SerialHandler] Unexpected matrix header (rows={rows}, cols={cols}, plen={plen}); skipping")
                    continue

                payload = self.read_bytes(plen)
                if len(payload) < plen:
                    continue  # incomplete; loop again

                recv_crc = self.read_bytes(1)
                if len(recv_crc) < 1:
                    continue

                recv_crc = recv_crc[0]
                header_all = bytes(
                    [SOF_BYTE, EVENT_MATRIX, rows, cols]) + header_rest[2:4]  # 6 bytes
                calc_crc = crc8(header_all + payload)
                if recv_crc != calc_crc:
                    print(
                        f"[SerialHandler] Matrix CRC mismatch: expected 0x{calc_crc:02X}, got 0x{recv_crc:02X}")
                    continue  # drop

                # Unpack the 240-byte bitpacked data → (20,96) array
                try:
                    mat = unpack_matrix_bitpacked_packet(header_all, payload)
                except Exception as e:
                    print(f"[SerialHandler] Error unpacking matrix: {e}")
                    continue

                with self.lock:
                    self.matrix = mat.tolist()  # or keep as numpy array if you prefer
                self.events.put({'type': 'matrix', 'mat': self.matrix})

            else:
                # Unknown type; skip
                print(f"[SerialHandler] Unknown packet type: 0x{ptype:02X}")

    def close(self):
        """
        Stop the thread and close the serial port.
        """
        self.running = False
        if self.ser.is_open:
            try:
                self.ser.cancel_read()
            except AttributeError:
                self.ser.close()
        if self.read_thread.is_alive():
            self.read_thread.join()
        if self.ser.is_open:
            self.ser.close()
