const express = require("express");
const http = require("http");
const WebSocket = require("ws");
const axios = require("axios");

const app = express();
app.use(express.json());

const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

const PORT = process.env.PORT || 4000;

// Replica registry: id -> base URL
// Use env vars so same code works in Docker (hostname) and locally (localhost)
const REPLICAS = {
  replica1: process.env.PEER_REPLICA1_URL || "http://localhost:5001",
  replica2: process.env.PEER_REPLICA2_URL || "http://localhost:5002",
  replica3: process.env.PEER_REPLICA3_URL || "http://localhost:5003",
  replica4: process.env.PEER_REPLICA4_URL || "http://localhost:5004",
  replica5: process.env.PEER_REPLICA5_URL || "http://localhost:5005",
};

let currentLeader = null;
let clients = new Set();

// ─── Leader Discovery ────────────────────────────────────────────────────────

async function discoverLeader() {
  for (const [id, url] of Object.entries(REPLICAS)) {
    try {
      const res = await axios.get(`${url}/status`, { timeout: 800 });
      if (res.data.state === "leader") {
        if (currentLeader !== id) {
          console.log(`[Gateway] Leader discovered: ${id}`);
          currentLeader = id;
        }
        return id;
      }
    } catch (_) {}
  }
  console.warn("[Gateway] No leader found yet, retrying...");
  currentLeader = null;
  return null;
}

// Poll for leader every 300ms
setInterval(discoverLeader, 300);

// ─── WebSocket Handling ──────────────────────────────────────────────────────

wss.on("connection", (ws) => {
  clients.add(ws);
  console.log(`[Gateway] Client connected. Total: ${clients.size}`);

  // Send current canvas state from leader on connect
  sendCurrentState(ws);

  ws.on("message", async (data) => {
    let msg;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }

    if (msg.type === "stroke") {
      await forwardStrokeToLeader(msg.stroke);
    }
  });

  ws.on("close", () => {
    clients.delete(ws);
    console.log(`[Gateway] Client disconnected. Total: ${clients.size}`);
  });

  ws.on("error", (err) => {
    console.error("[Gateway] WS error:", err.message);
    clients.delete(ws);
  });
});

async function forwardStrokeToLeader(stroke) {
  // Try current leader first, then discover
  let attempts = 0;
  while (attempts < 5) {
    if (!currentLeader) {
      await discoverLeader();
      await sleep(200);
      attempts++;
      continue;
    }
    const url = REPLICAS[currentLeader];
    try {
      await axios.post(`${url}/stroke`, { stroke }, { timeout: 1000 });
      return;
    } catch (err) {
      console.warn(`[Gateway] Failed to reach leader ${currentLeader}: ${err.message}`);
      currentLeader = null;
      await discoverLeader();
      attempts++;
    }
  }
  console.error("[Gateway] Could not forward stroke after 5 attempts");
}

async function sendCurrentState(ws) {
  for (let attempt = 0; attempt < 5; attempt++) {
    const orderedReplicas = currentLeader
      ? [[currentLeader, REPLICAS[currentLeader]], ...Object.entries(REPLICAS).filter(([id]) => id !== currentLeader)]
      : Object.entries(REPLICAS);
    for (const [id, url] of orderedReplicas) {
      try {
        const res = await axios.get(`${url}/log`, { timeout: 800 });
        const strokes = res.data.log || [];
        if (ws.readyState === WebSocket.OPEN) {
          console.log(`[Gateway] Sending ${strokes.length} strokes to new client from ${id}`);
          ws.send(JSON.stringify({ type: "init", strokes }));
        }
        return;
      } catch (_) {}
    }
    await sleep(300);
  }
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "init", strokes: [] }));
  }
}

// ─── Commit broadcast endpoint (called by leader) ────────────────────────────

app.post("/broadcast", (req, res) => {
  const { stroke } = req.body;
  if (!stroke) return res.status(400).json({ error: "missing stroke" });

  let sent = 0;
  for (const ws of clients) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stroke", stroke }));
      sent++;
    }
  }
  console.log(`[Gateway] Broadcast stroke to ${sent} clients`);
  res.json({ ok: true, sent });
});

app.get("/health", (_, res) => res.json({ ok: true, leader: currentLeader, clients: clients.size }));

server.listen(PORT, () => {
  console.log(`[Gateway] Listening on port ${PORT}`);
  discoverLeader();
});

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
