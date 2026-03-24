"""
demo.py — Run a 3-node MiniRaft cluster in a single process

No TCP involved — nodes call each other directly in memory.
Great for quickly verifying correctness before running the full server.

Expected output:
  1. One node wins an election and becomes leader
  2. Leader replicates 5 writes to all followers
  3. After 10 writes, a snapshot is taken automatically
  4. All nodes end up with identical state machines
  5. Restarted node recovers its state from disk
"""

import shutil
import time
import os

from raft import RaftNode


DATA_DIR = "/tmp/miniraft_demo"


def run_demo():
    # Clean slate
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR)

    print("=" * 60)
    print("  MiniRaft Demo — 3-node cluster")
    print("=" * 60)

    # ── 1. Start 3 nodes ──────────────────────────────────────────────
    nodes = [RaftNode(i, [], data_dir=f"{DATA_DIR}/node_{i}") for i in range(3)]
    for n in nodes:
        n.peers = [p for p in nodes if p.node_id != n.node_id]

    print("\n[Demo] Waiting for leader election (~3s)...\n")
    time.sleep(3.5)

    # ── 2. Find leader ────────────────────────────────────────────────
    leader = next((n for n in nodes if n.state.value == "leader"), None)
    if not leader:
        print("ERROR: no leader elected — try increasing sleep time")
        return

    print(f"\n[Demo] Leader is Node {leader.node_id}\n")

    # ── 3. Submit writes ──────────────────────────────────────────────
    commands = [
        "SET name Alice",
        "SET city Bengaluru",
        "SET course CloudComputing",
        "SET grade A",
        "DEL grade",
        "SET semester 6",
        "SET project MiniRaft",
        "SET lang Python",
        "SET score 100",
        "SET done true",   # <-- 10th commit triggers snapshot
    ]

    print("[Demo] Submitting 10 writes (snapshot triggers at 10)...\n")
    for cmd in commands:
        leader.client_write(cmd)
        time.sleep(0.3)

    time.sleep(1.5)   # allow replication and snapshot

    # ── 4. Print final state ──────────────────────────────────────────
    print("\n[Demo] Final cluster state:")
    print("-" * 60)
    for n in nodes:
        s = n.status()
        print(f"  Node {s['node_id']} [{s['state']:9}]  "
              f"term={s['term']}  commit={s['commit_index']}  "
              f"snap={s['snapshot_index']}  log_len={s['log_len']}")
        print(f"    state_machine = {s['state_machine']}")

    # ── 5. Crash-recovery demo ────────────────────────────────────────
    print("\n[Demo] Simulating Node 0 crash and restart...\n")
    nodes[0].stop()
    time.sleep(0.5)

    recovered = RaftNode(0, [], data_dir=f"{DATA_DIR}/node_0")
    recovered.peers = [nodes[1], nodes[2]]
    nodes[1].peers  = [recovered, nodes[2]]
    nodes[2].peers  = [recovered, nodes[1]]
    nodes[0] = recovered

    time.sleep(1.5)
    s = recovered.status()
    print(f"[Demo] Recovered Node 0: term={s['term']}  "
          f"commit={s['commit_index']}  snap={s['snapshot_index']}")
    print(f"  state_machine = {s['state_machine']}")

    print("\n[Demo] Done!")
    for n in nodes:
        n.stop()


if __name__ == "__main__":
    run_demo()
