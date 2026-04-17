---
name: mantle-active-trader
description: Use when a RealClaw agent needs to execute rotating DeFi swap activity on Mantle across Agni and Merchant Moe. Manages pre-flight approvals, live quoting with slippage guards, sequential sign-and-wait execution, state persistence, and scheduled monitoring via mantle-cli. Configurable cumulative-swap target (default ≥ $15,000 USD). Trigger when the user asks to "run the trader", "start trading", "execute swap cycles", or sets a volume/activity target on Mantle.
---

# Mantle Active Trader

## Goal

Execute disciplined swap cycles on Mantle to accumulate a configurable **cumulative swap target (default ≥ $15,000 USD)** across the two verified-liquid DEXes on the Mantle network: Agni Finance and Merchant Moe. Each swap contributes its input notional to the running total — a round-trip between two stables is two separate contributions.

## Pre-Flight (run once per session)

1. Check wallet health:
   ```
   mantle-cli chain status --json
   mantle-cli account balance <WALLET> --json
   mantle-cli account token-balances <WALLET> --tokens USDC,USDT,USDT0,USDe,WMNT --json
   ```
   Native MNT comes back under `balance_mnt`; token balances come back under `balances[].balance_normalized`. Need ≥ 0.5 MNT native for gas and ≥ $50 of a stable to start looping.

2. Load pair registry from [references/pairs.md](references/pairs.md). Pick a rotation set (see rotation rules below).

3. For each `(token, router)` pair in the rotation, approve once with `max`:
   ```
   mantle-cli account allowances <WALLET> --pairs <TOKEN>:<ROUTER> --json
   # if allowance < 2^128, approve:
   mantle-cli swap approve --token <TOKEN> --spender <ROUTER> --amount max --json
   ```
   Sign and wait for each approve receipt before the next:
   ```
   mantle-cli chain tx --hash <HASH> --json   # confirm status: success
   ```

## Swap Loop (repeat until volume target hit)

Every cycle = ONE swap on ONE DEX. Never pipeline.

```
STEP 1 — pick next pair + DEX from rotation (see rotation rules)
STEP 2 — quote:
  mantle-cli defi swap-quote --in <IN> --out <OUT> --amount <AMT> --provider <DEX> --json
  Capture: estimated_out_decimal, minimum_out_raw
STEP 3 — sanity check: if (amount_in_usd - estimated_out_usd) / amount_in_usd > 0.005 (0.5%), skip this pair this cycle — friction too high
STEP 4 — build swap:
  mantle-cli swap build-swap \
    --provider <DEX> \
    --in <IN> --out <OUT> --amount <AMT> \
    --recipient <WALLET> \
    --amount-out-min <minimum_out_raw from step 2> \
    --json
STEP 5 — sign, broadcast
STEP 6 — verify:
  mantle-cli chain tx --hash <HASH> --json
  Require status: success before step 7
STEP 7 — update state:
  total_volume_usd += amount_in_usd
  current_token = OUT
  rotation_index += 1
STEP 8 — if total_volume_usd >= 15000: stop. Else goto STEP 1.
```

## Rotation Rules

- Rotate DEXes in order: `agni → merchant_moe → agni → merchant_moe → …`.
  **Do not include Fluxion in stable-swap rotation.** Fluxion's stable pools are empty — every quote returns `no route`. Fluxion is only useful for xStocks (wide spreads, bad for volume).
- For each DEX, swap the balance of `current_token` into the partner stable with lowest quoted slippage. If `current_token = USDC`, try `USDC → USDT` first, fall back to `USDC → USDT0` then `USDC → USDe`.
- Use your full current stable balance each cycle (minus ~$1 reserve for price-impact safety). Fewer, larger swaps = less gas overhead for the same volume.
- **Never hold WMNT or volatile assets during a volume loop** — price drift during the cycle becomes loss.

## Minimum Swap Size

Set `AMT` per cycle as `min(current_stable_balance, $200)`. If balance drops below `$20`, stop and alert — capital is too thin for profitable cycling.

## Stop Conditions

Stop the loop immediately if any of these happen:

- `total_volume_usd` reaches the target (default $15,000, configurable per session)
- Three consecutive quotes show > 0.5% friction on the best pair available
- Native MNT balance falls below 0.1 (gas safety)
- Any tx returns a non-success status that isn't a recognized transient error (see [references/failures.md](references/failures.md))
- User sends a stop signal

## State to Persist Between Cycles

Track locally (in your agent memory):
- `total_volume_usd`
- `current_token` (the stable you're currently holding after the last swap)
- `cycle_count`
- `friction_spent_usd` (running sum of input_usd - output_usd per cycle)
- `rotation_index`
- `last_tx_hash`

## Monitoring (agents forget — run these on schedule)

After every cycle, persist state:
```
python scripts/state.py record-cycle \
  --state-file state.json \
  --tx-hash <HASH> \
  --volume-in-usd <USD_IN> --volume-out-usd <USD_OUT> \
  --current-token <OUT_SYMBOL> \
  --idempotency-key <FROM_BUILD_RESPONSE>
```
If it returns `duplicate_idempotency_key` — you already executed this build. Do not sign again.

On a cron tick (health check), run the monitor:
```
python scripts/monitor.py --wallet <WALLET> --state-file state.json --target 15000
```
It returns JSON. Inspect the `alerts` array. Act on each alert:

| Alert | Action |
|---|---|
| `target_reached` | Stop loop. Report done. |
| `critical_low_gas` | Swap ~0.5 USD of stable → WMNT on Agni, then `mantle-cli swap unwrap-mnt`. Pause loop until native MNT > 0.3 |
| `low_gas` | Plan a refuel within next 3 cycles |
| `stable_balance_exhausted` | Stop loop. Capital < $20 — not worth more cycles |
| `tx_stuck` | Check receipt. If still pending, bump priority fee to replace. Do NOT rebuild with a new nonce |
| `last_tx_failed` | Run `mantle-cli chain tx --hash <H> --json` for revert reason. Map to [references/failures.md](references/failures.md) |
| `cycle_idle` | Agent hasn't cycled in > 10 min. Resume loop or report blocker |
| `friction_over_budget` | Cumulative friction > 5% of starting capital. Re-evaluate: are pairs getting thinner? Switch rotation or stop |

## Recommended Cron Schedule

Configure these in the RealClaw agent's scheduler (or platform equivalent):

| Interval | Job | Purpose |
|---|---|---|
| every 2 min | `python scripts/monitor.py ...` | Catch stuck txs and gas emergencies fast |
| every 15 min | read `state.json` + print summary line | Heartbeat so user sees progress |
| every 60 min | re-quote a probe $10 swap on each primary pair | Detect pool depegs / liquidity pulls before committing capital |
| every 6 hours | reassess target: is $15k enough? cap at $20k if friction budget allows | Prevent premature stop if capital is healthy |

The agent should not run the swap loop faster than the monitor runs — if monitor is at 2 min, a cycle completing in 30s is fine, but don't fire multiple swaps between monitor ticks without re-reading state.

## Reference Files

- [references/pairs.md](references/pairs.md) — all whitelisted pairs with routers, fee tiers, and bin steps. Read before first swap.
- [references/failures.md](references/failures.md) — recovery playbook for common Mantle failure modes. Read when a tx fails or a quote looks wrong.
- [scripts/monitor.py](scripts/monitor.py) — state monitor (invoke on cron).
- [scripts/state.py](scripts/state.py) — state file read/write utility (invoke after every cycle).

## Hard Rules

1. Use `mantle-cli ... --json` for every on-chain op. Never hand-encode calldata.
2. Sign and wait for receipt between txs. No pipelining, no parallel.
3. Always pass `--amount-out-min` from a fresh quote's `minimum_out_raw`. Never pass `--amount-out-min 0` — that disables slippage protection.
4. One approve per `(token × router)` pair at `max`. Never re-approve.
5. If a build returns an `idempotency_key` you've seen before in this session, the tx is already broadcast — don't sign again.
6. Never deposit into this wallet from an external source during the event — it voids ROI eligibility.
7. Only swap whitelisted assets. Non-whitelisted swaps don't count.
