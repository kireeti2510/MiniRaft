# MiniRaft

A minimal implementation of the **Raft distributed consensus algorithm** in Python, built as part of a Cloud Computing course project.

Raft keeps a cluster of servers in sync even when some crash or messages are lost. It does this by electing one **leader** who controls all writes, then replicating those writes to a majority of nodes before committing them.

---

## Features

| Feature | File | Description |
|---|---|---|
| Leader election | `raft.py` | Randomised timeouts, RequestVote RPC, majority quorum |
| Log replication | `raft.py` | AppendEntries RPC, consistency check, commit index |
| Persistence | `persistence.py` | Atomic JSON flush to disk (survives crashes) |
| Snapshots | `snapshot.py` | Log compaction + InstallSnapshot for lagging nodes |
| Network layer | `server.py` | Newline-delimited JSON over TCP |
| CLI client | `client.py` | Submit writes, read keys, view cluster status |

---

## Architecture

```
┌─────────────┐     AppendEntries / RequestVote      ┌─────────────┐
│   Node 0    │◄───────────────────────────────────►│   Node 1    │
│  (Leader)   │                                      │ (Follower)  │
└──────┬──────┘                                      └─────────────┘
       │                                                      ▲
       │         AppendEntries / RequestVote                  │
       └──────────────────────────────────────────────────────┘
                              Node 2
                           (Follower)

Each node:
  raft.py          ← consensus logic
  persistence.py   ← current_term, voted_for, log  (disk)
  snapshot.py      ← state machine snapshot         (disk)
  server.py        ← TCP wrapper
```

---

## Raft Concepts Implemented

### 1. Leader Election
- Every node starts as a **Follower** with a randomised election timeout (1.5–3s)
- If no heartbeat is received before the timeout, it becomes a **Candidate** and starts an election
- A node wins if it receives votes from a **majority** (`floor(n/2) + 1`)
- The log up-to-date check (§5.4.1) ensures only nodes with the most complete log can win

### 2. Log Replication
- Only the leader accepts client writes
- The leader appends the entry locally, then sends `AppendEntries` to all followers
- An entry is **committed** once a majority has it
- Followers apply committed entries to their state machine in order

### 3. Persistence (Crash Recovery)
- `current_term`, `voted_for`, and `log[]` are flushed to disk atomically before responding to any RPC
- On restart, a node reloads these and resumes from where it left off

### 4. Snapshots (Log Compaction)
- After every 10 committed entries, the leader takes a snapshot of the state machine
- The log entries covered by the snapshot are discarded
- Lagging followers receive the full snapshot via `InstallSnapshot`

---

## State Machine

A simple key-value store:

```
SET key value   →  state_machine[key] = value
DEL key         →  del state_machine[key]
```

---

## Quick Start

### Option A — In-memory demo (no networking)
```bash
python demo.py
```

### Option B — Full networked cluster (3 terminals)

**Terminal 1:**
```bash
python server.py --id 0 --port 5000 --peers 5000 5001 5002
```

**Terminal 2:**
```bash
python server.py --id 1 --port 5001 --peers 5000 5001 5002
```

**Terminal 3:**
```bash
python server.py --id 2 --port 5002 --peers 5000 5001 5002
```

**Terminal 4 (client):**
```bash
# Submit writes
python client.py --port 5000 write "SET x 42"
python client.py --port 5000 write "SET name Alice"

# Read a value
python client.py --port 5000 read x

# View all node statuses
python client.py --port 5000 status --all --peers 5000 5001 5002
```

---

## File Structure

```
miniraft/
├── raft.py          # Core Raft node
├── persistence.py   # Disk persistence
├── snapshot.py      # Snapshot / log compaction
├── server.py        # TCP server + remote peer proxy
├── client.py        # CLI client
├── demo.py          # In-process demo script
└── README.md
```

---

## Relation to Course Syllabus

| Implementation | Syllabus Topic |
|---|---|
| Leader election, heartbeats | Unit 4: Leader election, cluster coordination |
| AppendEntries, commit quorum | Unit 4: Distributed consensus, Raft case study |
| Persistence (fsync) | Unit 4: Fault tolerance, checkpointing |
| Snapshots, log compaction | Unit 4: Application recovery |
| Randomised timeouts | Unit 4: Unreliable communication |
| Replication majority check | Unit 3: Consistency models, replication |

---

## References

- [In Search of an Understandable Consensus Algorithm (Raft paper)](https://raft.github.io/raft.pdf)
- [Raft visualisation](https://raft.github.io)
