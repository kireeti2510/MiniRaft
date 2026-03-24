"""
persistence.py — Durable storage for Raft's persistent state

Raft requires three fields to survive crashes (§5.7):
  - current_term  (must not go backwards)
  - voted_for     (must not vote twice in the same term)
  - log[]         (must not lose committed entries)

We store these as a single JSON file per node, flushing with
os.fsync() so data reaches the physical disk before we ACK.
"""

import json
import os


class PersistentState:
    def __init__(self, node_id: int, data_dir: str = "data"):
        self.path = os.path.join(data_dir, f"node_{node_id}_state.json")
        os.makedirs(data_dir, exist_ok=True)

    def save(self, state: dict):
        """
        Atomically write state to disk.

        We write to a temp file first, then rename — this guarantees
        that a crash mid-write never leaves a corrupted state file.
        """
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())   # flush kernel buffers → physical disk
        os.replace(tmp_path, self.path)  # atomic on POSIX

    def load(self) -> dict:
        """Load state from disk. Returns empty dict if no file exists yet."""
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            print(f"[Persistence] WARNING: could not read {self.path}, starting fresh")
            return {}
