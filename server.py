"""
server.py — TCP server that wraps a RaftNode

Each node runs one RaftServer.  Remote peers communicate by sending
JSON-encoded RPC messages over a persistent TCP connection.

Message format (newline-delimited JSON):
  Request:  {"rpc": "<name>", "args": {...}}
  Response: {"ok": true/false, "result": <value>}

Supported RPCs:
  request_vote     — leader election
  append_entries   — log replication / heartbeat
  install_snapshot — send snapshot to lagging follower
  client_write     — submit a command (leader only)
  client_read      — read a key from state machine
  status           — node diagnostics

Usage:
  python server.py --id 0 --port 5000 --peers 5001 5002
  python server.py --id 1 --port 5001 --peers 5000 5002
  python server.py --id 2 --port 5002 --peers 5000 5001
"""

import argparse
import json
import socket
import threading
import time

from raft import RaftNode, LogEntry


# ─────────────────────────────────────────────
# Remote peer proxy
# ─────────────────────────────────────────────

class RemotePeer:
    """
    Looks like a RaftNode to the local node, but forwards
    every RPC call over TCP to the actual remote node.
    """

    def __init__(self, peer_id: int, host: str, port: int):
        self.node_id = peer_id
        self.host    = host
        self.port    = port

    def _call(self, rpc: str, args: dict):
        """Send one RPC and return the result (or None on failure)."""
        try:
            with socket.create_connection((self.host, self.port), timeout=1.0) as s:
                msg = json.dumps({"rpc": rpc, "args": args}) + "\n"
                s.sendall(msg.encode())
                resp = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                    if b"\n" in resp:
                        break
                return json.loads(resp.decode().strip())
        except Exception as e:
            # Network partition / peer down — silently fail
            return None

    # ── RPC shims ─────────────────────────────────────────────────────

    def request_vote(self, term, candidate_id, last_log_index, last_log_term):
        r = self._call("request_vote", {
            "term": term, "candidate_id": candidate_id,
            "last_log_index": last_log_index, "last_log_term": last_log_term,
        })
        return r["result"] if r else False

    def append_entries(self, term, leader_id, prev_log_index,
                       prev_log_term, entries, leader_commit):
        r = self._call("append_entries", {
            "term": term, "leader_id": leader_id,
            "prev_log_index": prev_log_index, "prev_log_term": prev_log_term,
            "entries": [e.to_dict() for e in entries],
            "leader_commit": leader_commit,
        })
        if r:
            return r["result"]["success"], r["result"]["term"]
        return False, 0

    def install_snapshot(self, last_index, last_term, data):
        self._call("install_snapshot", {
            "last_index": last_index, "last_term": last_term, "data": data,
        })


# ─────────────────────────────────────────────
# TCP Server
# ─────────────────────────────────────────────

class RaftServer:
    def __init__(self, node_id: int, port: int, peer_ports: list,
                 host: str = "127.0.0.1", data_dir: str = "data"):
        self.port = port
        self.host = host

        # Build remote peer proxies
        peers = [
            RemotePeer(i, host, p)
            for i, p in enumerate(peer_ports)
            if i != node_id
        ]

        self.node = RaftNode(node_id, peers, data_dir=f"{data_dir}/node_{node_id}")

        # Wire up peer node_ids correctly
        all_ports = list(peer_ports)
        for proxy in peers:
            proxy.node_id = all_ports.index(proxy.port)

    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(16)
        print(f"[Server] node {self.node.node_id} listening on {self.host}:{self.port}")

        while True:
            conn, addr = srv.accept()
            threading.Thread(
                target=self._handle,
                args=(conn,),
                daemon=True
            ).start()

    def _handle(self, conn: socket.socket):
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            msg    = json.loads(data.decode().strip())
            result = self._dispatch(msg["rpc"], msg["args"])
            resp   = json.dumps({"ok": True, "result": result}) + "\n"
            conn.sendall(resp.encode())
        except Exception as e:
            err = json.dumps({"ok": False, "error": str(e)}) + "\n"
            try:
                conn.sendall(err.encode())
            except Exception:
                pass
        finally:
            conn.close()

    def _dispatch(self, rpc: str, args: dict):
        n = self.node
        if rpc == "request_vote":
            return n.request_vote(
                args["term"], args["candidate_id"],
                args["last_log_index"], args["last_log_term"],
            )
        elif rpc == "append_entries":
            entries = [LogEntry.from_dict(e) for e in args["entries"]]
            success, term = n.append_entries(
                args["term"], args["leader_id"],
                args["prev_log_index"], args["prev_log_term"],
                entries, args["leader_commit"],
            )
            return {"success": success, "term": term}
        elif rpc == "install_snapshot":
            n.install_snapshot(args["last_index"], args["last_term"], args["data"])
            return True
        elif rpc == "client_write":
            return n.client_write(args["command"])
        elif rpc == "client_read":
            return n.client_read(args["key"])
        elif rpc == "status":
            return n.status()
        else:
            raise ValueError(f"Unknown RPC: {rpc}")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MiniRaft node server")
    parser.add_argument("--id",    type=int, required=True,  help="This node's ID (0-based)")
    parser.add_argument("--port",  type=int, required=True,  help="Port to listen on")
    parser.add_argument("--peers", type=int, nargs="+",      help="All node ports (including this one)")
    parser.add_argument("--host",  type=str, default="127.0.0.1")
    parser.add_argument("--data",  type=str, default="data", help="Data directory")
    args = parser.parse_args()

    srv = RaftServer(
        node_id    = args.id,
        port       = args.port,
        peer_ports = args.peers,
        host       = args.host,
        data_dir   = args.data,
    )
    srv.start()
