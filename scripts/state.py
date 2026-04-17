#!/usr/bin/env python3
"""
state.py — utility to read/write the ClawHack agent state file.

The agent calls this after every cycle to persist progress. The monitor
script reads the same file.

Usage:
    python state.py read --state-file state.json
    python state.py record-cycle --state-file state.json \
        --tx-hash 0x... --volume-in-usd 100 --volume-out-usd 99.96 \
        --current-token USDT
    python state.py init --state-file state.json --starting-capital-usd 122
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


EMPTY_STATE: dict = {
    "total_volume_usd": 0.0,
    "friction_spent_usd": 0.0,
    "cycle_count": 0,
    "last_cycle_ts": 0,
    "last_tx_hash": None,
    "last_tx_status": None,
    "current_token": None,
    "starting_capital_usd": None,
    "rotation_index": 0,
    "idempotency_keys_seen": [],
}


def load(path: Path) -> dict:
    if not path.exists():
        return dict(EMPTY_STATE)
    with path.open() as f:
        return json.load(f)


def save(path: Path, state: dict) -> None:
    with path.open("w") as f:
        json.dump(state, f, indent=2)


def cmd_read(args: argparse.Namespace) -> int:
    print(json.dumps(load(args.state_file), indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    state = dict(EMPTY_STATE)
    state["starting_capital_usd"] = args.starting_capital_usd
    state["current_token"] = args.current_token
    save(args.state_file, state)
    print(json.dumps({"ok": True, "state": state}, indent=2))
    return 0


def cmd_record_cycle(args: argparse.Namespace) -> int:
    state = load(args.state_file)

    if args.idempotency_key and args.idempotency_key in state["idempotency_keys_seen"]:
        print(json.dumps({
            "ok": False,
            "reason": "duplicate_idempotency_key",
            "hint": "this build was already executed; do NOT sign again",
        }, indent=2))
        return 2

    state["cycle_count"] += 1
    state["total_volume_usd"] += args.volume_in_usd
    state["friction_spent_usd"] += (args.volume_in_usd - args.volume_out_usd)
    state["last_cycle_ts"] = int(time.time())
    state["last_tx_hash"] = args.tx_hash
    state["last_tx_status"] = "success"
    state["current_token"] = args.current_token
    state["rotation_index"] = (state.get("rotation_index", 0) + 1) % 3
    if args.idempotency_key:
        state["idempotency_keys_seen"].append(args.idempotency_key)
        state["idempotency_keys_seen"] = state["idempotency_keys_seen"][-200:]

    save(args.state_file, state)
    print(json.dumps({"ok": True, "state": state}, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("read")
    r.add_argument("--state-file", required=True, type=Path)
    r.set_defaults(func=cmd_read)

    i = sub.add_parser("init")
    i.add_argument("--state-file", required=True, type=Path)
    i.add_argument("--starting-capital-usd", required=True, type=float)
    i.add_argument("--current-token", required=True)
    i.set_defaults(func=cmd_init)

    c = sub.add_parser("record-cycle")
    c.add_argument("--state-file", required=True, type=Path)
    c.add_argument("--tx-hash", required=True)
    c.add_argument("--volume-in-usd", required=True, type=float)
    c.add_argument("--volume-out-usd", required=True, type=float)
    c.add_argument("--current-token", required=True)
    c.add_argument("--idempotency-key", default=None)
    c.set_defaults(func=cmd_record_cycle)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
