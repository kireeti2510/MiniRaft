# Distributed Real-Time Drawing Board — Mini-RAFT

A fault-tolerant collaborative drawing platform backed by a simplified RAFT consensus protocol.

---

## Project Structure

```
miniraft/
├── docker-compose.yml
├── gateway/          ← WebSocket server, leader proxy, broadcast hub
│   ├── index.js
│   ├── package.json
│   └── Dockerfile
├── replica1/         ← RAFT node 1
│   ├── index.js      ← Full RAFT logic (election, replication, heartbeat, sync)
│   ├── package.json
│   └── Dockerfile
├── replica2/         ← RAFT node 2 (same code, different env vars)
├── replica3/         ← RAFT node 3 (same code, different env vars)
└── frontend/
    └── index.html    ← Canvas drawing board (WebSocket client)
```

---

## Quick Start

### 1. Build and run everything

```bash
docker compose up --build
```

### 2. Open the drawing board

```
http://localhost:3000
```

Open in **multiple browser tabs** to simulate multiple users. Strokes drawn in one tab appear in all others.

---

## How to Demo the Assignment Requirements

### ✅ Leader Election
Watch the logs on startup:
```bash
docker compose logs -f replica1 replica2 replica3
```
You will see one replica win the election and log `Becoming LEADER`.

### ✅ Kill the Leader & Automatic Failover
```bash
# Find the current leader first
curl http://localhost:4000/health

# Kill the leader (e.g., replica1)
docker compose stop replica1
```
Within ~800ms, another replica becomes leader. Drawing continues uninterrupted.

### ✅ Hot Reload a Replica (Zero Downtime)
Edit any file in `replica2/index.js` (add a comment). The container auto-reloads via nodemon. RAFT re-elects if needed. Clients stay connected.

### ✅ Rejoin a Stopped Replica
```bash
docker compose start replica1
```
Replica1 starts as Follower with empty log, detects mismatch, leader syncs all missing entries via `/sync-log`. Replica1 is back in sync.

### ✅ Consistent State After Failures
Strokes are only broadcast to clients AFTER a majority (2/3) of replicas confirm replication. If a replica is down, the other 2 still form a majority.

---

## API Reference

### Gateway
| Endpoint | Method | Description |
|----------|--------|-------------|
| `ws://localhost:4000` | WS | Browser WebSocket connection |
| `/broadcast` | POST | Called by leader to push committed strokes to all clients |
| `/health` | GET | Returns current leader and client count |

### Each Replica (ports 5001/5002/5003)
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
Leader → AppendEntries → Follower1, Follower2
Leader → (majority ACKs) → Commit → POST /broadcast → Gateway → all browsers
```

### Catch-Up (Restarted Node)
```
Restart → Follower (empty log)
Follower receives AppendEntries → prevLogIndex mismatch → returns logLength
Leader calls POST /sync-log on follower → sends all committed entries from that index
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

# Live logs for all services
docker compose logs -f

# Restart a single replica (triggers catch-up)
docker compose restart replica2

# Stress test: rapid kill/restart
docker compose stop replica1 && sleep 1 && docker compose start replica1
```

---

## Architecture Diagram (Text)

```
┌─────────────┐      ┌──────────────────────────────────────────┐
│  Browser 1  │      │              Docker Network               │
│  Browser 2  │      │                                          │
│  Browser 3  │      │  ┌──────────┐     ┌──────────────────┐  │
│             │◄─WS──┼──┤ Gateway  │────►│   Replica 1      │  │
└─────────────┘      │  │ :4000    │     │   (RAFT Leader)  │  │
                     │  └──────────┘     └────────┬─────────┘  │
                     │       ▲                     │ AppendEntries
                     │       │ /broadcast          ▼            │
                     │       │           ┌──────────────────┐  │
                     │       └───────────┤   Replica 2      │  │
                     │                   │   (Follower)     │  │
                     │                   └──────────────────┘  │
                     │                   ┌──────────────────┐  │
                     │                   │   Replica 3      │  │
                     │                   │   (Follower)     │  │
                     │                   └──────────────────┘  │
                     └──────────────────────────────────────────┘
```

---

## Team

This project was built as a 3-week distributed systems assignment simulating:
- Kubernetes-style leader consensus (etcd/RAFT)
- Real-time collaborative apps (Figma, Miro)
- Zero-downtime rolling deployments
- Microservice fault tolerance
