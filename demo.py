"""
demo.py — Run a 3-node MiniRaft cluster in a single process

No TCP involved — nodes call each other directly in memory.
Verifies: election, replication, snapshot, crash recovery.
"""

import shutil
import time
import os

from raft import RaftNode

DATA_DIR = "/tmp/miniraft_demo"


def run_demo():
    if os.path.exists(DATA_DIR):
        shutil.rmtree(DATA_DIR)

    print("=" * 60)
    print("  MiniRaft Demo — 3-node cluster")
    print("=" * 60)

    # ── Start 3 nodes ─────────────────────────────────────────────────
    nodes = [RaftNode(i, [], data_dir=f"{DATA_DIR}/node_{i}") for i in range(3)]
    for n in nodes:
        n.peers = [p for p in nodes if p.node_id != n.node_id]

    print("\n[Demo] Waiting for leader election (~3s)...\n")
    time.sleep(3.5)

    leader = next((n for n in nodes if n.state.value == "leader"), None)
    if not leader:
        print("ERROR: no leader elected — try increasing the sleep time above")
        return

    print(f"\n[Demo] Leader is Node {leader.node_id}\n")

    # ── Submit 10 writes ──────────────────────────────────────────────
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
        "SET done true",   # 10th commit triggers snapshot
    ]

    print("[Demo] Submitting 10 writes (snapshot triggers at 10)...\n")
    for cmd in commands:
        leader.client_write(cmd)
        time.sleep(0.3)

    # Wait for all nodes to fully converge (heartbeat propagates commit)
    time.sleep(2.0)

    # ── Final state ───────────────────────────────────────────────────
    print("\n[Demo] Final cluster state:")
    print("-" * 60)
    all_match = True
    states = []
    for n in nodes:
        s = n.status()
        states.append(s["state_machine"])
        print(f"  Node {s['node_id']} [{s['state']:9}]  "
              f"term={s['term']}  commit={s['commit_index']}  "
              f"snap={s['snapshot_index']}  log_len={s['log_len']}")
        print(f"    state_machine = {s['state_machine']}")

    if len(set(str(s) for s in states)) == 1:
        print("\n  ✓ All nodes agree on the same state machine")
    else:
        print("\n  ✗ WARNING: nodes disagree — check replication")

    # ── Crash + recovery demo ─────────────────────────────────────────
    print("\n[Demo] Simulating Node 0 crash and restart...\n")
    victim_id = nodes[0].node_id
    nodes[0].stop()
    time.sleep(0.5)

    recovered = RaftNode(victim_id, [], data_dir=f"{DATA_DIR}/node_{victim_id}")
    recovered.peers = [nodes[1], nodes[2]]
    nodes[1].peers  = [recovered if p.node_id == victim_id else p for p in nodes[1].peers]
    nodes[2].peers  = [recovered if p.node_id == victim_id else p for p in nodes[2].peers]
    nodes[0] = recovered

    time.sleep(1.5)
    s = recovered.status()
    print(f"\n[Demo] Recovered Node {victim_id}:")
    print(f"  term={s['term']}  commit={s['commit_index']}  "
          f"snap={s['snapshot_index']}")
    print(f"  state_machine = {s['state_machine']}")

    if s["state_machine"] == states[0]:
        print("  ✓ State machine fully restored from disk")
    else:
        print("  ✗ WARNING: recovered state differs")

    print("\n[Demo] Done!")
    for n in nodes:
        n.stop()


if __name__ == "__main__":
    run_demo()
