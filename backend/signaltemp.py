import struct
import json
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Any, Tuple, Type

def serialize(signal):

    # NH start sequence
    start_sequence = b"SIG"

    # NH terminator sequence
    terminator_sequence = b"EOT\n"

    fmt = "BBB" + "B" + signal.transport_info[1] + "BBBB"
    id = signal.transport_info[0]
    # TODO: return a serial writable struct
    packed_data = struct.pack(fmt, *start_sequence, id, *signal.transport_data, *terminator_sequence)
    return packed_data

def deserialize(packed_data) -> tuple:
    """Extracts and returns the signal ID and payload from packed data."""

    # Ensure the packed data is at least long enough for the header and footer
    if len(packed_data) < 7:  # "SIG" (3) + id (1) + "EOT\n" (4) = 8 bytes minimum
        raise ValueError("Packed data is too short. Possible corruption.")

    # Unpack the start sequence and message type
    start_sequence, signal_id = struct.unpack("3sB", packed_data[:4])
    if start_sequence != b"SIG":
        raise ValueError("Invalid start sequence. Possible corruption.")

    # Verify terminator sequence
    if packed_data[-4:] != b"EOT\n":
        raise ValueError("Invalid terminator sequence. Possible corruption.")

    # Extract payload
    payload = packed_data[4:-4]  # Exclude "SIG" header and "EOT\n" footer

    return signal_id, payload

class Signal:
    """This class represents an event in the system with data associated with it"""

    @property
    def name(self) -> str:
        """Returns the name of the signal type."""
        return self.__class__.__name__

    @property
    def transport_info(self) -> tuple:
        pass

    @property
    def transport_data(self):
        pass

    @classmethod
    def from_payload(cls, payload):
        """Base class method to reconstruct signals. Must be implemented in subclasses."""
        raise NotImplementedError("Subclasses must implement from_payload()")

@dataclass  # 0x00
class PrintDisplay(Signal):
    string: str = None

    @property
    def transport_info(self) -> tuple:
        byte_len = len(self.string.encode("utf-8"))
        return 0x00, byte_len * "B"  # now 384 × "B"

    @property
    def transport_data(self):
        return self.string.encode("utf-8")

    @classmethod
    def from_payload(cls, payload):
        """Reconstructs PrintDisplay from binary payload."""
        return cls(string=payload.decode("utf-8"))

@dataclass  # 0x01
class DoubleTap(Signal):
    position: tuple = None

    @property
    def row(self):
        return self.position[0]

    @property
    def column(self):
        return self.position[1]

    @property
    def transport_info(self) -> tuple:
        return (0x01, "BB")  # Two bytes for (row, column)

    @property
    def transport_data(self):
        return self.position

    @classmethod
    def from_payload(cls, payload):
        """Reconstructs DoubleTap from binary payload."""
        row, col = struct.unpack("BB", payload)
        return cls(position=(row, col))

@dataclass  # 0x02
class Keystroke(Signal):
    value: set[str] = None

    @property
    def transport_info(self) -> tuple:
        """Returns (message type, format string)."""
        keystroke_list = sorted(self.value)  # Ensure consistent ordering
        format_string = "B"  # 1 byte for the number of keystrokes

        # Add format for each keystroke: length (1 byte) + actual string
        for key in keystroke_list:
            format_string += "B" + f"{len(key)}s"  # Length byte + string data

        return (0x02, format_string)

    @property
    def transport_data(self):
        """Returns data as [num_keys, (length, key_bytes)...]."""
        keystroke_list = sorted(self.value)  # Ensure predictable order
        keystroke_count = len(keystroke_list)

        data = [keystroke_count]  # First byte is the number of keystrokes

        for key in keystroke_list:
            encoded_key = key.encode("utf-8")  # Convert string to bytes
            data.append(len(encoded_key))  # Append key length
            data.append(encoded_key)  # Append key bytes

        return data

    @classmethod
    def from_payload(cls, payload):
        """Reconstructs Keystroke from binary payload."""
        key_count = payload[0]
        keys = []
        i = 1
        while i < len(payload):
            key_length = payload[i]
            key_bytes = payload[i+1:i+1+key_length]
            keys.append(bytes(key_bytes).decode("utf-8"))
            i += 1 + key_length
        return cls(value=set(keys))