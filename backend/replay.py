# === backend/file_replay.py ===
import json
import threading
import time
import queue
from typing import Optional


def transform_matrix(mat: list[list[int]]) -> list[list[int]]:
    """
    Apply the same padding/shift operations you had in NumPy,
    but purely with Python lists.
    1) add a row of zeros at the top
    2) drop the last column
    3) add a zero at the front of each row
    """
    if not mat:
        return []

    # how many columns in the existing matrix?
    n_cols = len(mat[0])

    # 1) add a zero-row on top
    with_top = [[0] * n_cols] + mat

    # 2) drop the last column from each row
    dropped = [row[:-1] for row in with_top]

    # 3) add a zero at the front of each row
    transformed = [[0] + row for row in dropped]

    return transformed


class FileReplayHandler:
    def __init__(self, file_path: str):
        """
        Reads JSON lines from file_path (already sorted by 'time'),
        and then enqueues them into self.events with the same inter-message spacing.
        """
        self.file_path = file_path
        self.events: queue.Queue = queue.Queue()
        self.running = True

        # Launch the replay thread
        self.thread = threading.Thread(target=self.replay_loop, daemon=True)
        self.thread.start()

    def replay_loop(self):
        with open(self.file_path, 'r') as f:
            prev_ts: Optional[float] = None

            for line in f:
                if not self.running:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    ts = float(msg['time'])
                except (ValueError, KeyError):
                    # skip lines we can't parse or without a time field
                    continue

                if prev_ts is None:
                    # first message -> dispatch immediately
                    prev_ts = ts
                else:
                    # sleep the difference between this ts and the last
                    delta = ts - prev_ts
                    if delta > 0:
                        time.sleep(delta)
                    prev_ts = ts

                if msg.get("type") == "matrix" and "mat" in msg:
                    msg["mat"] = transform_matrix(msg["mat"])

                # enqueue the dict (still JSON-parsed)
                self.events.put(msg)

    def close(self):
        """Stop replaying and wait for the thread to finish."""
        self.running = False
        if self.thread.is_alive():
            self.thread.join()

