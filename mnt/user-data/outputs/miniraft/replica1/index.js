const express = require("express");
const axios = require("axios");

const app = express();
app.use(express.json());

// ─── Configuration ────────────────────────────────────────────────────────────
const REPLICA_ID = process.env.REPLICA_ID || "replica1";
const PORT = parseInt(process.env.PORT || "5001");
const GATEWAY_URL = process.env.GATEWAY_URL || "http://gateway:4000";

const ALL_REPLICAS = {
  replica1: "http://replica1:5001",
  replica2: "http://replica2:5002",
  replica3: "http://replica3:5003",
};
const PEERS = Object.entries(ALL_REPLICAS).filter(([id]) => id !== REPLICA_ID);

// ─── RAFT State ───────────────────────────────────────────────────────────────
let state = "follower";  // follower | candidate | leader
let currentTerm = 0;
let votedFor = null;
let log = [];            // Array of { term, index, stroke }
let commitIndex = -1;
let leaderId = null;

// Timers
let electionTimer = null;
let heartbeatTimer = null;

const ELECTION_TIMEOUT_MIN = 500;
const ELECTION_TIMEOUT_MAX = 800;
const HEARTBEAT_INTERVAL = 150;

function log_(msg) {
  console.log(`[${REPLICA_ID}][${state.toUpperCase()}][term=${currentTerm}] ${msg}`);
}

// ─── Timer Management ─────────────────────────────────────────────────────────
function resetElectionTimer() {
  clearTimeout(electionTimer);
  const timeout = ELECTION_TIMEOUT_MIN + Math.random() * (ELECTION_TIMEOUT_MAX - ELECTION_TIMEOUT_MIN);
  electionTimer = setTimeout(startElection, timeout);
}

function stopElectionTimer() {
  clearTimeout(electionTimer);
}

function startHeartbeatTimer() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = setInterval(sendHeartbeats, HEARTBEAT_INTERVAL);
}

function stopHeartbeatTimer() {
  clearInterval(heartbeatTimer);
}

// ─── State Transitions ────────────────────────────────────────────────────────
function becomeFollower(term, newLeader = null) {
  log_(`Becoming FOLLOWER (term ${term})`);
  state = "follower";
  currentTerm = term;
  votedFor = null;
  leaderId = newLeader;
  stopHeartbeatTimer();
  resetElectionTimer();
}

function becomeLeader() {
  log_(`Becoming LEADER`);
  state = "leader";
  leaderId = REPLICA_ID;
  stopElectionTimer();
  startHeartbeatTimer();
  // Immediately send heartbeats
  sendHeartbeats();
}

// ─── Election ─────────────────────────────────────────────────────────────────
async function startElection() {
  if (state === "leader") return;

  state = "candidate";
  currentTerm += 1;
  votedFor = REPLICA_ID;
  let votes = 1; // vote for self

  log_(`Starting election for term ${currentTerm}`);
  resetElectionTimer();

  const votePromises = PEERS.map(async ([peerId, peerUrl]) => {
    try {
      const lastLogIndex = log.length - 1;
      const lastLogTerm = lastLogIndex >= 0 ? log[lastLogIndex].term : 0;
      const res = await axios.post(
        `${peerUrl}/request-vote`,
        { term: currentTerm, candidateId: REPLICA_ID, lastLogIndex, lastLogTerm },
        { timeout: 400 }
      );
      if (res.data.voteGranted) {
        votes++;
        log_(`Got vote from ${peerId} (total: ${votes})`);
      } else if (res.data.term > currentTerm) {
        becomeFollower(res.data.term);
      }
    } catch (err) {
      log_(`No response from ${peerId}: ${err.message}`);
    }
  });

  await Promise.allSettled(votePromises);

  if (state === "candidate" && votes >= 2) {
    becomeLeader();
  } else if (state === "candidate") {
    log_(`Lost election (${votes} votes), reverting to follower`);
    becomeFollower(currentTerm);
  }
}

// ─── Heartbeats ───────────────────────────────────────────────────────────────
async function sendHeartbeats() {
  if (state !== "leader") return;
  for (const [peerId, peerUrl] of PEERS) {
    try {
      const res = await axios.post(
        `${peerUrl}/heartbeat`,
        { term: currentTerm, leaderId: REPLICA_ID, commitIndex },
        { timeout: 300 }
      );
      if (res.data.term > currentTerm) {
        log_(`Higher term from ${peerId}, stepping down`);
        becomeFollower(res.data.term);
        return;
      }
    } catch (_) {}
  }
}

// ─── Log Replication ──────────────────────────────────────────────────────────
async function replicateEntry(entry) {
  let acks = 1; // leader counts itself

  const replicatePromises = PEERS.map(async ([peerId, peerUrl]) => {
    try {
      const prevIndex = log.length - 2;
      const prevTerm = prevIndex >= 0 ? log[prevIndex].term : 0;
      const res = await axios.post(
        `${peerUrl}/append-entries`,
        { term: currentTerm, leaderId: REPLICA_ID, entry, prevLogIndex: prevIndex, prevLogTerm: prevTerm },
        { timeout: 600 }
      );
      if (res.data.success) {
        acks++;
        log_(`AppendEntries ACK from ${peerId} (acks: ${acks})`);
      } else if (res.data.term > currentTerm) {
        becomeFollower(res.data.term);
      }
    } catch (err) {
      log_(`AppendEntries failed for ${peerId}: ${err.message}`);
    }
  });

  await Promise.allSettled(replicatePromises);
  return acks >= 2;
}

// ─── Sync Log (for restarted nodes) ──────────────────────────────────────────
async function syncFollower(followerId, peerUrl, fromIndex) {
  const missing = log.filter((e) => e.index > fromIndex && e.index <= commitIndex);
  try {
    await axios.post(`${peerUrl}/sync-log`, { entries: missing, commitIndex }, { timeout: 1000 });
    log_(`Synced ${missing.length} entries to ${followerId}`);
  } catch (err) {
    log_(`Sync failed for ${followerId}: ${err.message}`);
  }
}

// ─── Broadcast to Gateway ─────────────────────────────────────────────────────
async function broadcastToGateway(stroke) {
  try {
    await axios.post(`${GATEWAY_URL}/broadcast`, { stroke }, { timeout: 800 });
  } catch (err) {
    log_(`Gateway broadcast failed: ${err.message}`);
  }
}

// ─── HTTP Endpoints ───────────────────────────────────────────────────────────

// Status - used by gateway for leader discovery
app.get("/status", (_, res) => {
  res.json({ id: REPLICA_ID, state, term: currentTerm, leader: leaderId, logLength: log.length, commitIndex });
});

// Full committed log - used by gateway to init new clients
app.get("/log", (_, res) => {
  const committed = log.filter((e) => e.index <= commitIndex);
  res.json({ log: committed.map((e) => e.stroke) });
});

// Receive stroke from Gateway (leader only)
app.post("/stroke", async (req, res) => {
  if (state !== "leader") {
    return res.status(403).json({ error: "not leader", leaderId });
  }
  const { stroke } = req.body;
  if (!stroke) return res.status(400).json({ error: "missing stroke" });

  const entry = { term: currentTerm, index: log.length, stroke };
  log.push(entry);
  log_(`Received stroke, replicating (index=${entry.index})`);

  const committed = await replicateEntry(entry);
  if (committed) {
    commitIndex = entry.index;
    log_(`Committed stroke at index ${commitIndex}`);
    await broadcastToGateway(stroke);
    return res.json({ ok: true, index: commitIndex });
  } else {
    log_(`Failed to replicate stroke (not enough acks)`);
    return res.status(500).json({ error: "replication failed" });
  }
});

// RequestVote RPC
app.post("/request-vote", (req, res) => {
  const { term, candidateId, lastLogIndex, lastLogTerm } = req.body;

  if (term > currentTerm) {
    becomeFollower(term);
  }

  const myLastIndex = log.length - 1;
  const myLastTerm = myLastIndex >= 0 ? log[myLastIndex].term : 0;
  const logOk = term > myLastTerm || (term === myLastTerm && lastLogIndex >= myLastIndex);

  const voteGranted =
    term >= currentTerm &&
    (votedFor === null || votedFor === candidateId) &&
    logOk;

  if (voteGranted) {
    votedFor = candidateId;
    currentTerm = term;
    resetElectionTimer();
    log_(`Voted for ${candidateId} in term ${term}`);
  }

  res.json({ term: currentTerm, voteGranted });
});

// AppendEntries RPC
app.post("/append-entries", (req, res) => {
  const { term, leaderId: lid, entry, prevLogIndex, prevLogTerm } = req.body;

  if (term < currentTerm) {
    return res.json({ success: false, term: currentTerm });
  }

  becomeFollower(term, lid);

  // Check prevLog consistency
  if (prevLogIndex >= 0) {
    const prevEntry = log[prevLogIndex];
    if (!prevEntry || prevEntry.term !== prevLogTerm) {
      log_(`prevLog mismatch at ${prevLogIndex}, my log length: ${log.length}`);
      return res.json({ success: false, term: currentTerm, logLength: log.length });
    }
  }

  // Append entry
  if (entry) {
    // Remove any conflicting entries
    if (log.length > entry.index) {
      log.splice(entry.index);
    }
    log.push(entry);
    log_(`Appended entry at index ${entry.index}`);
  }

  res.json({ success: true, term: currentTerm });
});

// Heartbeat RPC
app.post("/heartbeat", (req, res) => {
  const { term, leaderId: lid, commitIndex: leaderCommit } = req.body;

  if (term < currentTerm) {
    return res.json({ term: currentTerm, success: false });
  }

  if (term > currentTerm || state !== "follower") {
    becomeFollower(term, lid);
  } else {
    leaderId = lid;
    resetElectionTimer();
  }

  // Advance commit index
  if (leaderCommit > commitIndex) {
    commitIndex = Math.min(leaderCommit, log.length - 1);
  }

  res.json({ term: currentTerm, success: true });
});

// Sync log RPC (catch-up for restarted follower)
app.post("/sync-log", (req, res) => {
  const { entries, commitIndex: newCommit } = req.body;
  if (!entries || !Array.isArray(entries)) {
    return res.status(400).json({ error: "missing entries" });
  }

  for (const entry of entries) {
    if (log.length <= entry.index) {
      log.push(entry);
    } else {
      log[entry.index] = entry;
    }
  }

  if (newCommit > commitIndex) {
    commitIndex = newCommit;
  }

  log_(`Sync complete. Log length: ${log.length}, commitIndex: ${commitIndex}`);
  res.json({ ok: true, logLength: log.length });
});

// Health check
app.get("/health", (_, res) => res.json({ ok: true, id: REPLICA_ID, state, term: currentTerm }));

// ─── Start ────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  log_(`Replica ${REPLICA_ID} started on port ${PORT}`);
  // Stagger startup to avoid all starting election simultaneously
  const startupDelay = Math.random() * 300;
  setTimeout(resetElectionTimer, startupDelay);
});

// Graceful shutdown
process.on("SIGTERM", () => {
  log_(`Shutting down gracefully`);
  stopElectionTimer();
  stopHeartbeatTimer();
  process.exit(0);
});
