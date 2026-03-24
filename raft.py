"""
raft.py — Core Raft consensus node

Implements:
  - Leader election (randomised timeouts + RequestVote RPC)
  - Log replication (AppendEntries RPC)
  - Commit index advancement (majority quorum)
  - Step-down on higher term
  - Integration with persistence and snapshot modules
"""

import threading
import time
import random
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

from persistence import PersistentState
from snapshot import Snapshot

# ─────────────────────────────────────────────
# Tunables
# ─────────────────────────────────────────────
ELECTION_TIMEOUT_MIN = 1.5   # seconds
ELECTION_TIMEOUT_MAX = 3.0
HEARTBEAT_INTERVAL   = 0.5
SNAPSHOT_THRESHOLD   = 10    # take snapshot every N committed entries


class State(Enum):
    FOLLOWER  = "follower"
    CANDIDATE = "candidate"
    LEADER    = "leader"


# ─────────────────────────────────────────────
# Log entry
# ─────────────────────────────────────────────
@dataclass
class LogEntry:
    term:    int
    index:   int
    command: str

    def to_dict(self):
        return {"term": self.term, "index": self.index, "command": self.command}

    @staticmethod
    def from_dict(d):
        return LogEntry(term=d["term"], index=d["index"], command=d["command"])


# ─────────────────────────────────────────────
# RaftNode
# ─────────────────────────────────────────────
class RaftNode:
    def __init__(self, node_id: int, peers: list, data_dir: str = "data"):
        self.node_id  = node_id
        self.peers    = peers          # list of other RaftNode objects
        self.data_dir = data_dir

        # ── Persistent state (survives restarts) ──────────────────────
        self._ps = PersistentState(node_id, data_dir)
        saved = self._ps.load()

        self.current_term: int           = saved.get("current_term", 0)
        self.voted_for: Optional[int]    = saved.get("voted_for", None)
        self.log: list[LogEntry]         = [
            LogEntry.from_dict(e) for e in saved.get("log", [])
        ]

        # ── Snapshot state ────────────────────────────────────────────
        self._snap = Snapshot(node_id, data_dir)
        snap_meta  = self._snap.load_meta()

        self.snapshot_index: int = snap_meta.get("last_included_index", 0)
        self.snapshot_term:  int = snap_meta.get("last_included_term",  0)
        # The state machine dict restored from snapshot
        self.state_machine: dict = self._snap.load_data() or {}

        # ── Volatile state ────────────────────────────────────────────
        self.commit_index = self.snapshot_index
        self.last_applied = self.snapshot_index

        # ── Leader-only volatile state ────────────────────────────────
        self.next_index:  dict = {}
        self.match_index: dict = {}

        # ── Status ────────────────────────────────────────────────────
        self.state     = State.FOLLOWER
        self.leader_id: Optional[int] = None

        # ── Election timer ────────────────────────────────────────────
        self.election_deadline = self._new_deadline()
        self.lock = threading.RLock()

        self._running = True
        threading.Thread(target=self._run_loop, daemon=True).start()
        print(f"[Node {self.node_id}] started — term={self.current_term} "
              f"log_len={len(self.log)} snap_idx={self.snapshot_index}")

    # ══════════════════════════════════════════
    # Internal helpers
    # ══════════════════════════════════════════

    def _new_deadline(self) -> float:
        """Randomised election timeout — key to avoiding split votes."""
        return time.time() + random.uniform(ELECTION_TIMEOUT_MIN, ELECTION_TIMEOUT_MAX)

    def _last_log_index(self) -> int:
        """Absolute index of the last log entry (or snapshot index if log empty)."""
        return self.log[-1].index if self.log else self.snapshot_index

    def _last_log_term(self) -> int:
        return self.log[-1].term if self.log else self.snapshot_term

    def _log_term_at(self, index: int) -> int:
        """Return the term of the entry at absolute index, checking snapshot."""
        if index == self.snapshot_index:
            return self.snapshot_term
        local_pos = index - self.snapshot_index - 1
        if 0 <= local_pos < len(self.log):
            return self.log[local_pos].term
        return 0

    def _persist(self):
        """Flush current_term, voted_for, log to disk."""
        self._ps.save({
            "current_term": self.current_term,
            "voted_for":    self.voted_for,
            "log":          [e.to_dict() for e in self.log],
        })

    # ══════════════════════════════════════════
    # Main background loop
    # ══════════════════════════════════════════

    def _run_loop(self):
        while self._running:
            time.sleep(0.1)
            with self.lock:
                if self.state == State.LEADER:
                    self._send_heartbeats()
                elif time.time() >= self.election_deadline:
                    self._start_election()

    # ══════════════════════════════════════════
    # Leader Election
    # ══════════════════════════════════════════

    def _start_election(self):
        """Follower/Candidate starts a new election."""
        self.state         = State.CANDIDATE
        self.current_term += 1
        self.voted_for     = self.node_id
        self._persist()
        self.election_deadline = self._new_deadline()
        votes = 1

        print(f"[Node {self.node_id}] starting election for term {self.current_term}")

        for peer in self.peers:
            granted = peer.request_vote(
                term           = self.current_term,
                candidate_id   = self.node_id,
                last_log_index = self._last_log_index(),
                last_log_term  = self._last_log_term(),
            )
            if granted:
                votes += 1

        majority = (len(self.peers) + 1) // 2 + 1
        if votes >= majority and self.state == State.CANDIDATE:
            self._become_leader()
        else:
            print(f"[Node {self.node_id}] election failed ({votes}/{majority} votes)")

    def _become_leader(self):
        self.state     = State.LEADER
        self.leader_id = self.node_id
        print(f"[Node {self.node_id}] ★ became LEADER for term {self.current_term}")

        for peer in self.peers:
            self.next_index[peer.node_id]  = self._last_log_index() + 1
            self.match_index[peer.node_id] = 0

    # ══════════════════════════════════════════
    # RequestVote RPC
    # ══════════════════════════════════════════

    def request_vote(self, term, candidate_id, last_log_index, last_log_term) -> bool:
        """
        Grant vote if:
          1. Candidate term >= our term
          2. We haven't voted for someone else this term
          3. Candidate log is at least as up-to-date as ours  (§5.4.1)
        """
        with self.lock:
            if term > self.current_term:
                self._step_down(term)

            if term < self.current_term:
                return False

            already_voted = (
                self.voted_for is not None
                and self.voted_for != candidate_id
            )
            if already_voted:
                return False

            # Log up-to-date check
            our_last_term  = self._last_log_term()
            our_last_index = self._last_log_index()
            log_ok = (
                last_log_term > our_last_term
                or (last_log_term == our_last_term and last_log_index >= our_last_index)
            )
            if not log_ok:
                return False

            self.voted_for         = candidate_id
            self.election_deadline = self._new_deadline()
            self._persist()
            print(f"[Node {self.node_id}] voted for {candidate_id} in term {term}")
            return True

    # ══════════════════════════════════════════
    # Client Write API
    # ══════════════════════════════════════════

    def client_write(self, command: str) -> bool:
        """
        Submit a command. Only the leader accepts writes.
        Returns True once the entry is committed.
        """
        with self.lock:
            if self.state != State.LEADER:
                print(f"[Node {self.node_id}] not leader — redirect to {self.leader_id}")
                return False

            entry = LogEntry(
                term    = self.current_term,
                index   = self._last_log_index() + 1,
                command = command,
            )
            self.log.append(entry)
            self._persist()
            print(f"[Node {self.node_id}] appended {entry}")

        self._replicate_log()
        return True

    def client_read(self, key: str):
        """Read a value from the local state machine."""
        with self.lock:
            return self.state_machine.get(key)

    # ══════════════════════════════════════════
    # Log Replication
    # ══════════════════════════════════════════

    def _send_heartbeats(self):
        for peer in self.peers:
            self._send_append_entries(peer)

    def _replicate_log(self):
        with self.lock:
            peers = list(self.peers)
        for peer in peers:
            with self.lock:
                self._send_append_entries(peer)

    def _send_append_entries(self, peer):
        """Send AppendEntries (or heartbeat) to one peer."""
        ni         = self.next_index.get(peer.node_id, 1)
        prev_index = ni - 1
        prev_term  = self._log_term_at(prev_index)

        # Entries to send: everything from next_index onward
        local_start = prev_index - self.snapshot_index   # position in self.log
        entries     = self.log[local_start:] if local_start >= 0 else []

        success, peer_term = peer.append_entries(
            term          = self.current_term,
            leader_id     = self.node_id,
            prev_log_index= prev_index,
            prev_log_term = prev_term,
            entries       = entries,
            leader_commit = self.commit_index,
        )

        if peer_term > self.current_term:
            self._step_down(peer_term)
            return

        if success:
            new_match = prev_index + len(entries)
            self.match_index[peer.node_id] = max(
                self.match_index.get(peer.node_id, 0), new_match
            )
            self.next_index[peer.node_id] = self.match_index[peer.node_id] + 1
            self._advance_commit_index()
        else:
            if self.next_index.get(peer.node_id, 1) > 1:
                self.next_index[peer.node_id] -= 1

    def _advance_commit_index(self):
        """
        Commit the highest N such that:
          - N > commit_index
          - log[N].term == current_term   (Raft safety rule)
          - a majority of nodes have match_index >= N
        """
        majority = (len(self.peers) + 1) // 2 + 1
        for n in range(self._last_log_index(), self.commit_index, -1):
            if self._log_term_at(n) != self.current_term:
                continue
            replicated = 1 + sum(
                1 for m in self.match_index.values() if m >= n
            )
            if replicated >= majority:
                self.commit_index = n
                self._apply_committed()
                # Maybe take a snapshot
                if self.commit_index - self.snapshot_index >= SNAPSHOT_THRESHOLD:
                    self._take_snapshot()
                break

    # ══════════════════════════════════════════
    # AppendEntries RPC
    # ══════════════════════════════════════════

    def append_entries(self, term, leader_id, prev_log_index,
                       prev_log_term, entries, leader_commit):
        """
        Follower handler. Returns (success, current_term).

        Steps (from Raft paper §5.3):
          1. Reject if term < current_term
          2. Reset election timer
          3. Check prev_log_index/prev_log_term consistency
          4. Append new entries, removing conflicts
          5. Advance commit_index
        """
        with self.lock:
            if term > self.current_term:
                self._step_down(term)

            if term < self.current_term:
                return False, self.current_term

            # Valid message from current leader
            self.state             = State.FOLLOWER
            self.leader_id         = leader_id
            self.election_deadline = self._new_deadline()

            # ── Consistency check ──────────────────────────────────────
            if prev_log_index > 0:
                # prev entry must exist
                if prev_log_index < self.snapshot_index:
                    # we've already snapshotted past this — accept
                    pass
                elif prev_log_index == self.snapshot_index:
                    if prev_log_term != self.snapshot_term:
                        return False, self.current_term
                else:
                    local_pos = prev_log_index - self.snapshot_index - 1
                    if local_pos >= len(self.log):
                        return False, self.current_term   # missing
                    if self.log[local_pos].term != prev_log_term:
                        # Conflict — truncate from here
                        self.log = self.log[:local_pos]
                        self._persist()
                        return False, self.current_term

            # ── Append entries ─────────────────────────────────────────
            for entry in entries:
                local_pos = entry.index - self.snapshot_index - 1
                if local_pos < len(self.log):
                    if self.log[local_pos].term != entry.term:
                        # Overwrite conflicting entry and everything after
                        self.log = self.log[:local_pos]
                        self.log.append(entry)
                else:
                    self.log.append(entry)

            if entries:
                self._persist()

            # ── Advance commit index ───────────────────────────────────
            if leader_commit > self.commit_index:
                self.commit_index = min(leader_commit, self._last_log_index())
                self._apply_committed()

            return True, self.current_term

    # ══════════════════════════════════════════
    # State machine & application
    # ══════════════════════════════════════════

    def _apply_committed(self):
        """Apply all committed-but-not-yet-applied entries to state machine."""
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            local_pos = self.last_applied - self.snapshot_index - 1
            if local_pos < 0 or local_pos >= len(self.log):
                continue
            entry = self.log[local_pos]
            self._apply_entry(entry)

    def _apply_entry(self, entry: LogEntry):
        """
        Tiny key-value state machine.
        Supported commands:  SET key value  |  DEL key
        """
        parts = entry.command.strip().split()
        if len(parts) == 3 and parts[0].upper() == "SET":
            self.state_machine[parts[1]] = parts[2]
        elif len(parts) == 2 and parts[0].upper() == "DEL":
            self.state_machine.pop(parts[1], None)
        print(f"[Node {self.node_id}] ✓ applied [{entry.index}] '{entry.command}' "
              f"→ state={self.state_machine}")

    # ══════════════════════════════════════════
    # Snapshots  (log compaction)
    # ══════════════════════════════════════════

    def _take_snapshot(self):
        """
        Compact the log up to last_applied:
          1. Save state machine + metadata to disk
          2. Drop log entries already covered by snapshot
        """
        idx  = self.last_applied
        term = self._log_term_at(idx)

        self._snap.save(
            last_included_index = idx,
            last_included_term  = term,
            data                = self.state_machine,
        )

        # Discard log entries up to and including the snapshot point
        entries_to_drop = idx - self.snapshot_index
        self.log = self.log[entries_to_drop:]

        self.snapshot_index = idx
        self.snapshot_term  = term

        print(f"[Node {self.node_id}] snapshot taken up to index {idx} "
              f"(log compacted, {len(self.log)} entries remain)")

    def install_snapshot(self, last_index, last_term, data: dict):
        """
        Leader sends a full snapshot to a lagging follower.
        Replaces local state completely.
        """
        with self.lock:
            if last_index <= self.snapshot_index:
                return  # already have this or newer

            self._snap.save(last_index, last_term, data)

            # Discard all local log entries covered by snapshot
            self.log = [e for e in self.log if e.index > last_index]

            self.snapshot_index = last_index
            self.snapshot_term  = last_term
            self.state_machine  = dict(data)
            self.commit_index   = max(self.commit_index, last_index)
            self.last_applied   = max(self.last_applied, last_index)

            print(f"[Node {self.node_id}] snapshot installed up to index {last_index}")

    # ══════════════════════════════════════════
    # Step down
    # ══════════════════════════════════════════

    def _step_down(self, new_term: int):
        print(f"[Node {self.node_id}] stepping down: "
              f"term {self.current_term}→{new_term}")
        self.current_term      = new_term
        self.state             = State.FOLLOWER
        self.voted_for         = None
        self.election_deadline = self._new_deadline()
        self._persist()

    # ══════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════

    def stop(self):
        self._running = False
        print(f"[Node {self.node_id}] stopped")

    def status(self) -> dict:
        with self.lock:
            return {
                "node_id":        self.node_id,
                "state":          self.state.value,
                "term":           self.current_term,
                "leader":         self.leader_id,
                "log_len":        len(self.log),
                "commit_index":   self.commit_index,
                "last_applied":   self.last_applied,
                "snapshot_index": self.snapshot_index,
                "state_machine":  dict(self.state_machine),
            }
