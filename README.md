# Distributed Real-Time Drawing Board — Mini-RAFT

A fault-tolerant collaborative drawing platform backed by a simplified RAFT consensus protocol.

---

## Project Structure

```
miniraft/
├── start-local.sh    ← One-command local launcher (no Docker)
├── docker-compose.yml
├── gateway/          ← WebSocket server, leader proxy, broadcast hub
│   ├── index.js
│   └── package.json
├── replica1/         ← RAFT node 1
│   ├── index.js      ← Full RAFT logic (election, replication, heartbeat, sync)
│   └── package.json
├── replica2/         ← RAFT node 2 (same code, different env vars)
├── replica3/         ← RAFT node 3
├── replica4/         ← RAFT node 4
├── replica5/         ← RAFT node 5
└── frontend/
    └── index.html    ← Canvas drawing board (WebSocket client)
```

---

## Quick Start (No Docker)

### Option A — One command (all replicas in one terminal)

```bash
cd miniraft
chmod +x start-local.sh
./start-local.sh
```

Press `Ctrl+C` to stop everything.

---

### Option B — Each replica in its own terminal (recommended for demo)

Open **7 separate terminals** and run one command in each:

**Terminal 1 — Gateway**
```bash
cd ~/Desktop/MiniRaft/miniraft/gateway
REPLICA_ID=gateway PORT=4000 \
  PEER_REPLICA1_URL=http://localhost:5001 \
  PEER_REPLICA2_URL=http://localhost:5002 \
  PEER_REPLICA3_URL=http://localhost:5003 \
  PEER_REPLICA4_URL=http://localhost:5004 \
  PEER_REPLICA5_URL=http://localhost:5005 \
  node index.js
```

**Terminal 2 — Replica 1**
```bash
cd ~/Desktop/MiniRaft/miniraft/replica1
REPLICA_ID=replica1 PORT=5001 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=http://localhost:5001 \
  PEER_REPLICA2_URL=http://localhost:5002 \
  PEER_REPLICA3_URL=http://localhost:5003 \
  PEER_REPLICA4_URL=http://localhost:5004 \
  PEER_REPLICA5_URL=http://localhost:5005 \
  node index.js
```

**Terminal 3 — Replica 2**
```bash
cd ~/Desktop/MiniRaft/miniraft/replica2
REPLICA_ID=replica2 PORT=5002 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=http://localhost:5001 \
  PEER_REPLICA2_URL=http://localhost:5002 \
  PEER_REPLICA3_URL=http://localhost:5003 \
  PEER_REPLICA4_URL=http://localhost:5004 \
  PEER_REPLICA5_URL=http://localhost:5005 \
  node index.js
```

**Terminal 4 — Replica 3**
```bash
cd ~/Desktop/MiniRaft/miniraft/replica3
REPLICA_ID=replica3 PORT=5003 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=http://localhost:5001 \
  PEER_REPLICA2_URL=http://localhost:5002 \
  PEER_REPLICA3_URL=http://localhost:5003 \
  PEER_REPLICA4_URL=http://localhost:5004 \
  PEER_REPLICA5_URL=http://localhost:5005 \
  node index.js
```

**Terminal 5 — Replica 4**
```bash
cd ~/Desktop/MiniRaft/miniraft/replica4
REPLICA_ID=replica4 PORT=5004 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=http://localhost:5001 \
  PEER_REPLICA2_URL=http://localhost:5002 \
  PEER_REPLICA3_URL=http://localhost:5003 \
  PEER_REPLICA4_URL=http://localhost:5004 \
  PEER_REPLICA5_URL=http://localhost:5005 \
  node index.js
```

**Terminal 6 — Replica 5**
```bash
cd ~/Desktop/MiniRaft/miniraft/replica5
REPLICA_ID=replica5 PORT=5005 GATEWAY_URL=http://localhost:4000 \
  PEER_REPLICA1_URL=http://localhost:5001 \
  PEER_REPLICA2_URL=http://localhost:5002 \
  PEER_REPLICA3_URL=http://localhost:5003 \
  PEER_REPLICA4_URL=http://localhost:5004 \
  PEER_REPLICA5_URL=http://localhost:5005 \
  node index.js
```

**Terminal 7 — Frontend**
```bash
npx --yes http-server ~/Desktop/MiniRaft/miniraft/frontend -p 3000
```

### Open the drawing board

```
http://localhost:3000
```

Open in **multiple browser tabs** to simulate multiple users.

---

## Monitoring — Live Status Per Replica

Open one terminal per replica to watch state changes in real time:

```bash
# Replica 1
while true; do clear; echo "--- replica1 ---"; curl -s http://localhost:5001/status | python3 -m json.tool; sleep 1; done

# Replica 2
while true; do clear; echo "--- replica2 ---"; curl -s http://localhost:5002/status | python3 -m json.tool; sleep 1; done

# Replica 3
while true; do clear; echo "--- replica3 ---"; curl -s http://localhost:5003/status | python3 -m json.tool; sleep 1; done

# Replica 4
while true; do clear; echo "--- replica4 ---"; curl -s http://localhost:5004/status | python3 -m json.tool; sleep 1; done

# Replica 5
while true; do clear; echo "--- replica5 ---"; curl -s http://localhost:5005/status | python3 -m json.tool; sleep 1; done
```

---

## How to Demo the Assignment Requirements

### ✅ Leader Election
Start all replicas and watch. Within ~1 second one node logs `Becoming LEADER`.
Check who is leader:
```bash
curl http://localhost:4000/health
```

### ✅ Kill the Leader & Automatic Failover

In **Option B** setup: press `Ctrl+C` in the terminal of whichever replica is the leader.

Within ~800ms the surviving replicas hold a new election and a new leader is chosen. The term number increases by 1. Drawing continues uninterrupted.

### ✅ Fault Tolerance (Quorum)

With 5 replicas, majority quorum = 3.

| Replicas alive | Can elect leader? | Drawboard works? |
|---|---|---|
| 5 | ✅ Yes | ✅ Yes |
| 4 | ✅ Yes | ✅ Yes |
| 3 | ✅ Yes | ✅ Yes |
| 2 | ❌ No | ❌ Stalls |
| 1 | ❌ No | ❌ Stalls |

### ✅ Rejoin a Stopped Replica
Restart the stopped replica's terminal command. It rejoins as Follower, detects log mismatch, and the leader syncs all missing entries via `/sync-log`. It is back in sync automatically.

### ✅ Consistent State After Failures
Strokes are only broadcast to clients **after a majority (3/5) of replicas confirm replication**. No data is lost even if replicas crash mid-replication.

---

## API Reference

### Gateway
| Endpoint | Method | Description |
|----------|--------|-------------|
| `ws://localhost:4000` | WS | Browser WebSocket connection |
| `/broadcast` | POST | Called by leader to push committed strokes to all clients |
| `/health` | GET | Returns current leader and client count |

### Each Replica (ports 5001–5005)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Returns state (follower/candidate/leader), term, commitIndex |
| `/log` | GET | Returns all committed log entries |
| `/stroke` | POST | Accept a stroke (leader only) |
| `/request-vote` | POST | RAFT vote request RPC |
| `/append-entries` | POST | RAFT log replication RPC |
| `/heartbeat` | POST | RAFT heartbeat RPC |
| `/sync-log` | POST | Push missing entries to a catching-up follower |
| `/health` | GET | Basic health check |

---

## Mini-RAFT Protocol Summary

### Node States
```
FOLLOWER → (timeout, no heartbeat) → CANDIDATE → (majority votes) → LEADER
CANDIDATE → (higher term seen)     → FOLLOWER
LEADER    → (higher term seen)     → FOLLOWER
```

### Timing
- **Election timeout**: 500–800ms (randomised to avoid split votes)
- **Heartbeat interval**: 150ms

### Log Replication Flow
```
Browser → (WS) → Gateway → (HTTP POST /stroke) → Leader
Leader → AppendEntries → Followers
Leader → (majority ACKs) → Commit → POST /broadcast → Gateway → all browsers
```

### Catch-Up (Restarted Node)
```
Restart → Follower (empty log)
Follower receives heartbeat → detects log mismatch → returns logLength
Leader calls POST /sync-log → sends all committed entries from that index
Follower applies entries → back in sync
```

---

## Useful Debug Commands

```bash
# Check leader
curl http://localhost:4000/health

# Check each replica state
curl http://localhost:5001/status
curl http://localhost:5002/status
curl http://localhost:5003/status

# View committed log on replica
curl http://localhost:5001/log
```

---

## Architecture Diagram

```
┌─────────────┐      ┌────────────────────────────────────────────────────┐
│  Browser 1  │      │                   Local Network                     │
│  Browser 2  │      │                                                      │
│  Browser 3  │      │  ┌──────────┐     ┌──────────────────────────────┐ │
│             │◄─WS──┼──┤ Gateway  │────►│   Replica 1  (RAFT Leader)   │ │
└─────────────┘      │  │ :4000    │     └──────────────┬───────────────┘ │
                     │  └──────────┘          AppendEntries / Heartbeat    │
                     │       ▲                           │                  │
                     │       │ /broadcast                ▼                  │
                     │       │            ┌──────────────────────────────┐ │
                     │       └────────────┤   Replica 2  (Follower)      │ │
                     │                    └──────────────────────────────┘ │
                     │                    ┌──────────────────────────────┐ │
                     │                    │   Replica 3  (Follower)      │ │
                     │                    └──────────────────────────────┘ │
                     │                    ┌──────────────────────────────┐ │
                     │                    │   Replica 4  (Follower)      │ │
                     │                    └──────────────────────────────┘ │
                     │                    ┌──────────────────────────────┐ │
                     │                    │   Replica 5  (Follower)      │ │
                     │                    └──────────────────────────────┘ │
                     └────────────────────────────────────────────────────┘
```

---

## Team

This project was built as a 3-week distributed systems assignment simulating:
- Kubernetes-style leader consensus (etcd/RAFT)
- Real-time collaborative apps (Figma, Miro)
- Zero-downtime rolling deployments
- Microservice fault tolerance
