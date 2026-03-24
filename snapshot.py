"""
snapshot.py — Snapshot storage for Raft log compaction

Why snapshots?
  Without them, the log grows forever.  Once entries are committed
  and applied to the state machine, we no longer need the raw log —
  the state machine already captures their effect.  A snapshot is a
  point-in-time copy of the state machine plus the metadata needed to
  resume Raft from that point:

    last_included_index  — the log index the snapshot replaces up to
    last_included_term   — the term of that entry (for consistency checks)
    data                 — the full state machine dict

Two files per node:
  node_<id>_snap_meta.json   — last_included_index, last_included_term
  node_<id>_snap_data.json   — the state machine dictionary
"""

import json
import os


class Snapshot:
    def __init__(self, node_id: int, data_dir: str = "data"):
        self.meta_path = os.path.join(data_dir, f"node_{node_id}_snap_meta.json")
        self.data_path = os.path.join(data_dir, f"node_{node_id}_snap_data.json")
        os.makedirs(data_dir, exist_ok=True)

    # ── Write ───────────────────────────────────────────────────────────

    def save(self, last_included_index: int, last_included_term: int, data: dict):
        """Persist a snapshot atomically (meta + data)."""
        meta = {
            "last_included_index": last_included_index,
            "last_included_term":  last_included_term,
        }
        self._atomic_write(self.meta_path, meta)
        self._atomic_write(self.data_path, data)
        print(f"[Snapshot] saved: index={last_included_index} term={last_included_term} "
              f"keys={list(data.keys())}")

    def _atomic_write(self, path: str, obj: dict):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    # ── Read ────────────────────────────────────────────────────────────

    def load_meta(self) -> dict:
        """Returns metadata dict, or empty dict if no snapshot exists."""
        return self._safe_load(self.meta_path)

    def load_data(self) -> dict:
        """Returns state machine dict, or empty dict if no snapshot exists."""
        return self._safe_load(self.data_path)

    def _safe_load(self, path: str) -> dict:
        if not os.path.exists(path):
            return {}
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            print(f"[Snapshot] WARNING: could not read {path}")
            return {}

    def exists(self) -> bool:
        return os.path.exists(self.meta_path)
