#!/usr/bin/env python3
"""
monitor.py — ClawHack agent state monitor.

Reads wallet state via mantle-cli, merges with persisted local state
(state.json), and prints a JSON status report with alert flags.

Designed to be invoked by the RealClaw cron scheduler. Output is a single
JSON object on stdout; alerts are keys with truthy values that the agent
must react to this tick.

Usage:
    python monitor.py --wallet <0x...> --state-file state.json [--target 15000]

Environment:
    MANTLE_CLI — override path to the mantle-cli binary (default: PATH lookup).

Exit codes:
    0 — healthy, no alerts
    1 — alerts present (agent must act)
    2 — hard failure (mantle-cli unreachable, state corrupt, etc.)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MANTLE_CLI = os.environ.get("MANTLE_CLI") or shutil.which("mantle-cli") or "mantle-cli"

MIN_NATIVE_MNT = 0.1
WARN_NATIVE_MNT = 0.3
STUCK_TX_SECONDS = 120
MAX_FRICTION_PCT = 0.05
CYCLE_IDLE_ALERT_SECONDS = 600


def run_cli(args: list[str]) -> dict:
    try:
        out = subprocess.run(
            [MANTLE_CLI, *args, "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return {"error": "mantle-cli not installed"}
    except subprocess.TimeoutExpired:
        return {"error": "mantle-cli timeout"}
    stdout = out.stdout.strip()
    if stdout:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            pass
    return {"error": out.stderr.strip() or f"non-zero exit {out.returncode}"}


def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "total_volume_usd": 0.0,
            "friction_spent_usd": 0.0,
            "cycle_count": 0,
            "last_cycle_ts": 0,
            "last_tx_hash": None,
            "last_tx_status": None,
            "current_token": None,
            "starting_capital_usd": None,
        }
    with path.open() as f:
        return json.load(f)


def check_native(wallet: str) -> tuple[float, list[str]]:
    alerts: list[str] = []
    res = run_cli(["account", "balance", wallet])
    if res.get("error"):
        alerts.append("balance_query_failed")
        return 0.0, alerts
    mnt = float(res.get("balance_mnt", 0))
    if mnt < MIN_NATIVE_MNT:
        alerts.append("critical_low_gas")
    elif mnt < WARN_NATIVE_MNT:
        alerts.append("low_gas")
    return mnt, alerts


def check_stables(wallet: str) -> tuple[dict[str, float], list[str]]:
    alerts: list[str] = []
    res = run_cli([
        "account", "token-balances", wallet,
        "--tokens", "USDC,USDT,USDT0,USDe",
    ])
    if res.get("error"):
        alerts.append("token_balance_query_failed")
        return {}, alerts
    balances = {
        b["symbol"]: float(b.get("balance_normalized", 0))
        for b in res.get("balances", [])
        if b.get("error") is None
    }
    total = sum(balances.values())
    if total < 20:
        alerts.append("stable_balance_exhausted")
    return balances, alerts


def check_last_tx(state: dict) -> list[str]:
    alerts: list[str] = []
    h = state.get("last_tx_hash")
    if not h:
        return alerts
    res = run_cli(["chain", "tx", "--hash", h])
    if res.get("code") == "TX_NOT_FOUND":
        if time.time() - state.get("last_cycle_ts", 0) > STUCK_TX_SECONDS:
            alerts.append("tx_stuck")
        state["last_tx_status"] = "pending_or_missing"
        return alerts
    if res.get("error"):
        alerts.append("tx_status_query_failed")
        return alerts
    status = res.get("status")
    state["last_tx_status"] = status
    if status == "failed":
        alerts.append("last_tx_failed")
    return alerts


def check_progress(state: dict, target: float) -> list[str]:
    alerts: list[str] = []
    vol = state.get("total_volume_usd", 0.0)
    if vol >= target:
        alerts.append("target_reached")
    friction = state.get("friction_spent_usd", 0.0)
    starting = state.get("starting_capital_usd") or 1.0
    if friction / starting > MAX_FRICTION_PCT:
        alerts.append("friction_over_budget")
    idle = time.time() - state.get("last_cycle_ts", 0)
    if state.get("cycle_count", 0) > 0 and idle > CYCLE_IDLE_ALERT_SECONDS:
        alerts.append("cycle_idle")
    return alerts


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--wallet", required=True)
    p.add_argument("--state-file", required=True, type=Path)
    p.add_argument("--target", type=float, default=15000.0)
    args = p.parse_args()

    report: dict = {"ts": int(time.time()), "wallet": args.wallet, "alerts": []}

    try:
        state = load_state(args.state_file)
    except Exception as e:
        print(json.dumps({"fatal": f"state_load_failed: {e}"}))
        return 2

    native_mnt, native_alerts = check_native(args.wallet)
    stables, stable_alerts = check_stables(args.wallet)
    tx_alerts = check_last_tx(state)
    progress_alerts = check_progress(state, args.target)

    report.update({
        "native_mnt": native_mnt,
        "stables": stables,
        "total_volume_usd": state.get("total_volume_usd"),
        "friction_spent_usd": state.get("friction_spent_usd"),
        "cycle_count": state.get("cycle_count"),
        "last_tx_hash": state.get("last_tx_hash"),
        "last_tx_status": state.get("last_tx_status"),
        "target_volume_usd": args.target,
        "progress_pct": round(
            100 * state.get("total_volume_usd", 0) / args.target, 2
        ),
    })

    report["alerts"] = sorted(set(
        native_alerts + stable_alerts + tx_alerts + progress_alerts
    ))

    try:
        with args.state_file.open("w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        report["alerts"].append(f"state_write_failed:{e}")

    print(json.dumps(report, indent=2))
    return 1 if report["alerts"] else 0


if __name__ == "__main__":
    sys.exit(main())
