# Failure Recovery Playbook

Read this when a tx fails, a quote looks wrong, or the agent is stuck. All `mantle-cli` commands assume `--json`.

## Symptom: `eth_estimateGas` returned 50M–300M gas

**Not a revert.** Mantle's RPC bakes L1 data-posting costs into the estimate. Normal. Actual gas used is much lower; the node still requires the inflated limit.

**Action:** accept the value. Do not cap lower. Fee in MNT will still be small (check `estimated_fee_mnt` field).

## Symptom: Transaction pending more than 2 minutes

**Causes, in order of likelihood:**
1. Stuck behind another pending tx with same nonce
2. `maxPriorityFeePerGas` too low for current block

**Action:**
```
mantle-cli chain tx --hash <HASH> --json
```
- If `not_found`: tx never reached the mempool. Rebuild with a bumped priority fee.
- If `pending`: get next-pending nonce from your signer, confirm it matches the tx's nonce. If so, wait another block. If mismatch, the nonce slot is taken — send a replacement tx at the stuck nonce with `maxPriorityFeePerGas` bumped ≥ 10%.

Never rebuild a tx with a new nonce until you've confirmed the old one is dropped.

## Symptom: Swap reverts with `STF`, `TF`, or "transfer failed"

**Cause:** allowance exhausted or token balance too low.

**Action:**
```
mantle-cli account allowances <WALLET> --pairs <IN>:<ROUTER> --json
mantle-cli account token-balances --address <WALLET> --tokens <IN> --json
```
- If allowance < amount → approve max again.
- If balance < amount → reduce `--amount` or switch to the token you hold.

## Symptom: Swap reverts with slippage / `Too little received` / `MINIMUM_AMOUNT`

**Cause:** price moved between quote and execution, past `minimum_out_raw`.

**Action:** re-quote, rebuild, retry. Do not raise slippage tolerance. If it reverts twice in a row on the same pair, skip that pair this rotation — liquidity is too thin or the pool is being moved by another actor.

## Symptom: Build command returned the same `idempotency_key` as a previous call this session

**The tx is already broadcast.** Do NOT sign again.

**Action:**
```
mantle-cli chain tx --hash <PREVIOUS_HASH> --json
```
Verify status. If `success`, update state and proceed with the NEXT cycle. If `failed`, investigate the revert reason before retrying.

## Symptom: "Proxy rebuilding tx" / gas params ignored

**Cause:** middleware between agent and signer is overriding EIP-1559 fields.

**Action:** bypass the proxy. Sign the unsigned_tx object returned by `mantle-cli` directly with the wallet. Do not depend on any wrapper to respect `maxFeePerGas`/`maxPriorityFeePerGas`.

## Symptom: `insufficient funds for gas`

**Cause:** native MNT balance too low.

**Action:** stop loop. Convert a small amount of current stable back to WMNT, then unwrap:
```
mantle-cli swap build-swap --provider agni --in USDC --out WMNT --amount 0.5 --recipient <WALLET> --amount-out-min <from_quote> --json
mantle-cli swap unwrap-mnt --amount <wmnt_received> --json
```
Then resume the loop. Keep native MNT above 0.1 at all times during the loop.

## Symptom: Quote returns no route / "no pool found"

**Cause:** the pool doesn't exist on that DEX for that pair/fee combination.

**Action:** drop this (pair, DEX) combination from the rotation for this session. Do not retry. Log it to persistent state so future sessions skip it.

## Symptom: Receipt shows `status: failed` but MNT was deducted

**Tx reverted.** Gas is still spent. The state change didn't happen.

**Action:**
- Do NOT rebuild the same tx.
- Run `mantle-cli chain tx --hash <HASH> --json` for the revert reason (look at the `error` or `revert_reason` field).
- Map revert reason to the symptoms above and recover accordingly.

## Symptom: Suspiciously large quote slippage on a stable pair

Any stable↔stable quote returning < 99% of input value is abnormal.

**Action:**
- Probe with a $10 quote on the same pair to confirm.
- If small quote is also bad → pool is imbalanced or depegging. Skip this pair until it heals.
- If small quote is fine but large quote is bad → your swap size exceeds pool depth. Cut `--amount` in half and retry.

## Hard Stops (do not retry, escalate to user)

- Multiple consecutive reverts with different causes → something is systemically wrong
- Native MNT below 0.05 and no stables to refuel from
- Wallet private key signing error
- More than 10 consecutive failed cycles
- User explicitly signals stop
