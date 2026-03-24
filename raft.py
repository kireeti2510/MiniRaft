"""
raft.py — Core Raft consensus node

Implements:
  - Leader election   (randomised timeouts + RequestVote RPC)
  - Log replication   (AppendEntries RPC + majority commit)
  - Persistence       (term / votedFor / log survive crashes)
  - Snapshots         (log compaction + InstallSnapshot for lagging nodes)
"""

import threading
import time
import random
from enum import Enum
from dataclasses import dataclass
from typing import Optional

from persistence import PersistentState
from snapshot import Snapshot

ELECTION_TIMEOUT_MIN = 1.5
ELECTION_TIMEOUT_MAX = 3.0
HEARTBEAT_INTERVAL   = 0.3
SNAPSHOT_THRESHOLD   = 10


class State(Enum):
    FOLLOWER  = "follower"
    CANDIDATE = "candidate"
    LEADER    = "leader"


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


class RaftNode:
    def __init__(self, node_id: int, peers: list, data_dir: str = "data"):
        self.node_id  = node_id
        self.peers    = peers
        self.data_dir = data_dir

        self._ps   = PersistentState(node_id, data_dir)
        self._snap = Snapshot(node_id, data_dir)

        saved     = self._ps.load()
        snap_meta = self._snap.load_meta()

        self.current_term: int        = saved.get("current_term", 0)
        self.voted_for: Optional[int] = saved.get("voted_for", None)
        self.log: list[LogEntry]      = [
            LogEntry.from_dict(e) for e in saved.get("log", [])
        ]

        self.snapshot_index: int = snap_meta.get("last_included_index", 0)
        self.snapshot_term:  int = snap_meta.get("last_included_term",  0)
        self.state_machine: dict = self._snap.load_data() or {}

        self.commit_index = self.snapshot_index
        self.last_applied = self.snapshot_index

        # Replay any log entries above the snapshot boundary on startup
        self._replay_log_on_startup()

        self.next_index:  dict = {}
        self.match_index: dict = {}

        self.state     = State.FOLLOWER
        self.leader_id: Optional[int] = None

        self.election_deadline = self._new_deadline()
        self.lock = threading.RLock()

        self._running = True
        self._thread  = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        print(f"[Node {self.node_id}] started — "
              f"term={self.current_term} log_len={len(self.log)} "
              f"snap_idx={self.snapshot_index} "
              f"state_machine={self.state_machine}")

    # ── Helpers ───────────────────────────────────────────────────────

    def _new_deadline(self) -> float:
        return time.time() + random.uniform(ELECTION_TIMEOUT_MIN, ELECTION_TIMEOUT_MAX)

    def _last_log_index(self) -> int:
        return self.log[-1].index if self.log else self.snapshot_index

    def _last_log_term(self) -> int:
        return self.log[-1].term if self.log else self.snapshot_term

    def _log_term_at(self, index: int) -> int:
        if index == 0:
            return 0
        if index == self.snapshot_index:
            return self.snapshot_term
        local_pos = index - self.snapshot_index - 1
        if 0 <= local_pos < len(self.log):
            return self.log[local_pos].term
        return 0

    def _persist(self):
        self._ps.save({
            "current_term": self.current_term,
            "voted_for":    self.voted_for,
            "log":          [e.to_dict() for e in self.log],
        })

    def _replay_log_on_startup(self):
        """Apply persisted log entries above snapshot index so state machine is current."""
        for entry in self.log:
            if entry.index > self.snapshot_index:
                self._apply_entry(entry, silent=True)
                self.last_applied = entry.index
                self.commit_index = entry.index

    # ── Background loop ───────────────────────────────────────────────

    def _run_loop(self):
        last_heartbeat = 0.0
        while self._running:
            time.sleep(0.05)
            with self.lock:
                if self.state == State.LEADER:
                    now = time.time()
                    if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                        self._send_heartbeats()
                        last_heartbeat = now
                elif time.time() >= self.election_deadline:
                    self._start_election()

    # ── Leader Election ───────────────────────────────────────────────

    def _start_election(self):
        self.state         = State.CANDIDATE
        self.current_term += 1
        self.voted_for     = self.node_id
        self._persist()
        self.election_deadline = self._new_deadline()
        votes = 1

        print(f"[Node {self.node_id}] starting election for term {self.current_term}")

        my_term       = self.current_term
        my_last_index = self._last_log_index()
        my_last_term  = self._last_log_term()

        self.lock.release()
        try:
            for peer in list(self.peers):
                try:
                    if peer.request_vote(term=my_term, candidate_id=self.node_id,
                                         last_log_index=my_last_index,
                                         last_log_term=my_last_term):
                        votes += 1
                except Exception:
                    pass
        finally:
            self.lock.acquire()

        majority = (len(self.peers) + 1) // 2 + 1
        if votes >= majority and self.state == State.CANDIDATE and self.current_term == my_term:
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

    # ── RequestVote RPC ───────────────────────────────────────────────

    def request_vote(self, term, candidate_id, last_log_index, last_log_term) -> bool:
        with self.lock:
            if term > self.current_term:
                self._step_down(term)
            if term < self.current_term:
                return False
            already_voted = (self.voted_for is not None
                             and self.voted_for != candidate_id)
            if already_voted:
                return False
            log_ok = (last_log_term > self._last_log_term()
                      or (last_log_term == self._last_log_term()
                          and last_log_index >= self._last_log_index()))
            if not log_ok:
                return False
            self.voted_for         = candidate_id
            self.election_deadline = self._new_deadline()
            self._persist()
            print(f"[Node {self.node_id}] voted for {candidate_id} in term {term}")
            return True

    # ── Client API ────────────────────────────────────────────────────

    def client_write(self, command: str) -> bool:
        with self.lock:
            if self.state != State.LEADER:
                print(f"[Node {self.node_id}] not leader — redirect to {self.leader_id}")
                return False
            entry = LogEntry(term=self.current_term,
                             index=self._last_log_index() + 1,
                             command=command)
            self.log.append(entry)
            self._persist()
            print(f"[Node {self.node_id}] appended {entry}")

        self._replicate_to_all()
        return True

    def client_read(self, key: str):
        with self.lock:
            return self.state_machine.get(key)

    # ── Log Replication ───────────────────────────────────────────────

    def _send_heartbeats(self):
        for peer in list(self.peers):
            self._send_to_peer(peer)

    def _replicate_to_all(self):
        with self.lock:
            peers = list(self.peers)
        for peer in peers:
            with self.lock:
                self._send_to_peer(peer)

    def _send_to_peer(self, peer):
        """
        Send AppendEntries OR InstallSnapshot to one peer.
        If the peer's next_index has fallen behind our snapshot, we must
        send a full snapshot instead of log entries.
        """
        ni = self.next_index.get(peer.node_id, 1)

        # If peer needs entries we've already compacted → send snapshot
        if ni <= self.snapshot_index:
            self._send_install_snapshot(peer)
            return

        self._send_append_entries(peer)

    def _send_install_snapshot(self, peer):
        """Send our current snapshot to a lagging peer."""
        my_term         = self.current_term
        snap_index      = self.snapshot_index
        snap_term       = self.snapshot_term
        snap_data       = dict(self.state_machine)

        self.lock.release()
        try:
            peer.install_snapshot(snap_index, snap_term, snap_data)
        except Exception:
            pass
        finally:
            self.lock.acquire()

        if self.state != State.LEADER or self.current_term != my_term:
            return

        # After installing snapshot, peer is caught up to snapshot_index
        self.next_index[peer.node_id]  = snap_index + 1
        self.match_index[peer.node_id] = snap_index

    def _send_append_entries(self, peer):
        ni          = self.next_index.get(peer.node_id, 1)
        prev_index  = ni - 1
        prev_term   = self._log_term_at(prev_index)
        local_start = prev_index - self.snapshot_index
        entries     = self.log[local_start:] if local_start >= 0 else []

        my_term      = self.current_term
        my_commit    = self.commit_index
        my_leader_id = self.node_id

        self.lock.release()
        try:
            success, peer_term = peer.append_entries(
                term=my_term, leader_id=my_leader_id,
                prev_log_index=prev_index, prev_log_term=prev_term,
                entries=entries, leader_commit=my_commit,
            )
        except Exception:
            success, peer_term = False, 0
        finally:
            self.lock.acquire()

        if self.state != State.LEADER or self.current_term != my_term:
            return

        if peer_term > self.current_term:
            self._step_down(peer_term)
            return

        if success:
            new_match = prev_index + len(entries)
            self.match_index[peer.node_id] = max(
                self.match_index.get(peer.node_id, 0), new_match)
            self.next_index[peer.node_id] = self.match_index[peer.node_id] + 1
            self._advance_commit_index()
        else:
            if self.next_index.get(peer.node_id, 1) > 1:
                self.next_index[peer.node_id] -= 1

    def _advance_commit_index(self):
        majority = (len(self.peers) + 1) // 2 + 1
        for n in range(self._last_log_index(), self.commit_index, -1):
            if self._log_term_at(n) != self.current_term:
                continue
            replicated = 1 + sum(1 for m in self.match_index.values() if m >= n)
            if replicated >= majority:
                self.commit_index = n
                self._apply_committed()
                if self.commit_index - self.snapshot_index >= SNAPSHOT_THRESHOLD:
                    self._take_snapshot()
                break

    # ── AppendEntries RPC ─────────────────────────────────────────────

    def append_entries(self, term, leader_id, prev_log_index,
                       prev_log_term, entries, leader_commit):
        with self.lock:
            if term > self.current_term:
                self._step_down(term)
            if term < self.current_term:
                return False, self.current_term

            self.state             = State.FOLLOWER
            self.leader_id         = leader_id
            self.election_deadline = self._new_deadline()

            # Consistency check
            if prev_log_index > self.snapshot_index:
                local_pos = prev_log_index - self.snapshot_index - 1
                if local_pos >= len(self.log):
                    return False, self.current_term
                if self.log[local_pos].term != prev_log_term:
                    self.log = self.log[:local_pos]
                    self._persist()
                    return False, self.current_term
            elif prev_log_index < self.snapshot_index:
                # Trim entries that are already covered by our snapshot
                skip    = self.snapshot_index - prev_log_index
                entries = entries[skip:]

            # Append new entries
            for entry in entries:
                local_pos = entry.index - self.snapshot_index - 1
                if local_pos < len(self.log):
                    if self.log[local_pos].term != entry.term:
                        self.log = self.log[:local_pos]
                        self.log.append(entry)
                else:
                    self.log.append(entry)

            if entries:
                self._persist()

            if leader_commit > self.commit_index:
                self.commit_index = min(leader_commit, self._last_log_index())
                self._apply_committed()

            return True, self.current_term

    # ── State machine ─────────────────────────────────────────────────

    def _apply_committed(self):
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            local_pos = self.last_applied - self.snapshot_index - 1
            if 0 <= local_pos < len(self.log):
                self._apply_entry(self.log[local_pos])

    def _apply_entry(self, entry: LogEntry, silent: bool = False):
        parts = entry.command.strip().split()
        if len(parts) == 3 and parts[0].upper() == "SET":
            self.state_machine[parts[1]] = parts[2]
        elif len(parts) == 2 and parts[0].upper() == "DEL":
            self.state_machine.pop(parts[1], None)
        if not silent:
            print(f"[Node {self.node_id}] ✓ applied [{entry.index}] "
                  f"'{entry.command}' → state={self.state_machine}")

    # ── Snapshots ─────────────────────────────────────────────────────

    def _take_snapshot(self):
        idx  = self.last_applied
        term = self._log_term_at(idx)
        self._snap.save(last_included_index=idx,
                        last_included_term=term,
                        data=self.state_machine)
        entries_to_drop     = idx - self.snapshot_index
        self.log            = self.log[entries_to_drop:]
        self.snapshot_index = idx
        self.snapshot_term  = term
        print(f"[Node {self.node_id}] snapshot taken up to index {idx} "
              f"(log compacted, {len(self.log)} entries remain)")

    def install_snapshot(self, last_index: int, last_term: int, data: dict):
        with self.lock:
            if last_index <= self.snapshot_index:
                return
            self._snap.save(last_index, last_term, data)
            self.log            = [e for e in self.log if e.index > last_index]
            self.snapshot_index = last_index
            self.snapshot_term  = last_term
            self.state_machine  = dict(data)
            self.commit_index   = max(self.commit_index, last_index)
            self.last_applied   = max(self.last_applied, last_index)
            print(f"[Node {self.node_id}] snapshot installed — "
                  f"index={last_index} state={self.state_machine}")

    # ── Step down ─────────────────────────────────────────────────────

    def _step_down(self, new_term: int):
        print(f"[Node {self.node_id}] stepping down: term {self.current_term}→{new_term}")
        self.current_term      = new_term
        self.state             = State.FOLLOWER
        self.voted_for         = None
        self.election_deadline = self._new_deadline()
        self._persist()

    # ── Lifecycle ─────────────────────────────────────────────────────

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
