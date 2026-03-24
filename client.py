"""
client.py — CLI client for MiniRaft cluster

Sends commands to any node (auto-redirects to leader).

Usage:
  python client.py --port 5000 write "SET x 42"
  python client.py --port 5000 write "SET y hello"
  python client.py --port 5000 read x
  python client.py --port 5000 status
  python client.py --port 5000 status --all --peers 5000 5001 5002
"""

import argparse
import json
import socket


def send_rpc(host: str, port: int, rpc: str, args: dict):
    try:
        with socket.create_connection((host, port), timeout=3.0) as s:
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
    except ConnectionRefusedError:
        return {"ok": False, "error": f"Could not connect to {host}:{port}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="MiniRaft client")
    parser.add_argument("--host",  default="127.0.0.1")
    parser.add_argument("--port",  type=int, default=5000)

    sub = parser.add_subparsers(dest="cmd")

    # write
    wp = sub.add_parser("write", help="Submit a command  e.g. 'SET x 1' or 'DEL x'")
    wp.add_argument("command", type=str)

    # read
    rp = sub.add_parser("read", help="Read a key from the state machine")
    rp.add_argument("key", type=str)

    # status  — --peers lives here now, not on the main parser
    sp = sub.add_parser("status", help="Show node status")
    sp.add_argument("--all",   action="store_true", help="Show all nodes")
    sp.add_argument("--peers", type=int, nargs="+", help="All node ports")

    args = parser.parse_args()

    if args.cmd == "write":
        r = send_rpc(args.host, args.port, "client_write", {"command": args.command})
        if r.get("ok"):
            print(f"✓ accepted: {r['result']}")
        else:
            print(f"✗ error: {r.get('error')}")

    elif args.cmd == "read":
        r = send_rpc(args.host, args.port, "client_read", {"key": args.key})
        if r.get("ok"):
            print(f"{args.key} = {r['result']}")
        else:
            print(f"✗ error: {r.get('error')}")

    elif args.cmd == "status":
        ports = args.peers if (args.all and args.peers) else [args.port]
        for port in ports:
            r = send_rpc(args.host, port, "status", {})
            if r.get("ok"):
                s = r["result"]
                marker = "★" if s["state"] == "leader" else " "
                print(f"{marker} Node {s['node_id']} [{s['state']:9}] "
                      f"term={s['term']}  commit={s['commit_index']}  "
                      f"snap={s['snapshot_index']}  "
                      f"state_machine={s['state_machine']}")
            else:
                print(f"  Port {port}: unreachable — {r.get('error')}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
