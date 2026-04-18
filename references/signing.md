# Signing and Broadcasting

`mantle-cli` only **builds** unsigned transactions. It never holds a private key. Your agent signs locally and broadcasts directly to Mantle RPC. This section documents the split and the pattern.

## The split

```
mantle-cli (build)          Agent code (sign + broadcast)
─────────────────          ─────────────────────────────
swap wrap-mnt       ┐
swap approve        │   ┌──►  fill in: nonce, gasPrice, gas
swap build-swap     ├──►│     sign_transaction(tx)
defi swap-quote     │   │     send_raw_transaction(signed)
transfer send-*     ┘   └──►  wait_for_transaction_receipt(hash)
```

Every CLI build command returns:
```json
{
  "intent": "<what_this_is>",
  "human_summary": "...",
  "unsigned_tx": {
    "to":      "0x...",
    "data":    "0x...",
    "value":   "0x0",
    "chainId": 5000
  },
  "idempotency_key": "0x..."
}
```

You must fill in `nonce`, a gas price field, and `gas` (limit) before signing. The CLI does NOT provide these because they depend on live chain state at sign-time.

## Fields to add before signing

| Field | Source | Notes |
|---|---|---|
| `nonce` | `eth_getTransactionCount(addr, "pending")` | Always use `pending` tag. Never cache. |
| `gasPrice` (legacy) OR `maxFeePerGas` + `maxPriorityFeePerGas` (EIP-1559) | `eth_gasPrice` × 1.1 as a floor | Legacy `gasPrice` works on Mantle. EIP-1559 also accepted. Must be ≥ current base fee. |
| `gas` (limit) | `eth_estimateGas({...tx, from: addr})` × 1.5 | Mantle RPC inflates estimates heavily (hundreds of millions). Use the value returned, don't cap lower. Fallback to 50M if estimation fails. |
| `from` | N/A in the tx body | Derived from the signing key. Never put `from` in the tx object being signed. |
| `chainId` | Passthrough from `unsigned_tx.chainId` | Always 5000 for Mantle mainnet. |
| `value` | Passthrough from `unsigned_tx.value` | Hex-encoded. Convert to int for signers that expect integers. |

## Pattern (Python + web3.py + eth-account)

```python
from eth_account import Account
from web3 import Web3

w3 = Web3(Web3.HTTPProvider("https://rpc.mantle.xyz"))
acct = Account.from_key(PRIVATE_KEY)

# 1. Build via mantle-cli (returns dict with unsigned_tx)
unsigned = run_cli_command(...)  # e.g. swap build-swap
tx = unsigned["unsigned_tx"]

# 2. Fill in chain-dependent fields
tx_ready = {
    "to":       Web3.to_checksum_address(tx["to"]),
    "data":     tx["data"],
    "value":    int(tx.get("value", "0x0"), 16) if isinstance(tx.get("value"), str) else 0,
    "chainId":  tx["chainId"],
    "nonce":    w3.eth.get_transaction_count(acct.address, "pending"),
    "gasPrice": int(w3.eth.gas_price * 1.1),
}
try:
    est = w3.eth.estimate_gas({**tx_ready, "from": acct.address})
    tx_ready["gas"] = int(est * 1.5)
except Exception:
    tx_ready["gas"] = 50_000_000

# 3. Sign locally
signed = acct.sign_transaction(tx_ready)

# 4. Broadcast via RPC
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

# 5. Wait for receipt
rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
if rcpt.status != 1:
    raise RuntimeError(f"tx reverted: {tx_hash.hex()}")
```

## Pattern (Node.js + viem)

```javascript
import { createWalletClient, createPublicClient, http } from 'viem'
import { privateKeyToAccount } from 'viem/accounts'
import { mantle } from 'viem/chains'

const account = privateKeyToAccount(PRIVATE_KEY)
const pub = createPublicClient({ chain: mantle, transport: http('https://rpc.mantle.xyz') })
const wallet = createWalletClient({ account, chain: mantle, transport: http('https://rpc.mantle.xyz') })

// 1. Build via mantle-cli
const { unsigned_tx } = await runCli(...)

// 2. Fill in
const nonce = await pub.getTransactionCount({ address: account.address, blockTag: 'pending' })
const gasPrice = await pub.getGasPrice()
let gas
try {
  gas = await pub.estimateGas({ ...unsigned_tx, account: account.address }) * 3n / 2n
} catch {
  gas = 50_000_000n
}

// 3. viem signs + broadcasts in one call; chainId derived from wallet config
const hash = await wallet.sendTransaction({
  to: unsigned_tx.to,
  data: unsigned_tx.data,
  value: BigInt(unsigned_tx.value ?? '0x0'),
  nonce,
  gasPrice: gasPrice * 11n / 10n,
  gas,
})

// 4. Wait for receipt
const rcpt = await pub.waitForTransactionReceipt({ hash, timeout: 180_000 })
if (rcpt.status !== 'success') throw new Error(`tx reverted: ${hash}`)
```

## RPC endpoint

Default: `https://rpc.mantle.xyz` (official, public). Has rate limits — backoff on 429. For sustained activity, use an authenticated endpoint (Ankr, QuickNode, Infura-Mantle) and set via env var or config.

## Strict rules for the agent

1. **Never re-sign a tx after a timeout.** If `wait_for_transaction_receipt` times out, query `eth_getTransactionCount` again to check whether the tx landed under that nonce. If it did, record and move on. If not, resubmit with **the same nonce** and a **bumped gas price** (≥10% higher) — this is a replacement, not a new tx.

2. **Never cache nonce.** Query fresh with `pending` tag before every sign. Stale nonce is the #1 cause of stuck broadcasts.

3. **Accept the inflated gas estimate.** Mantle's `eth_estimateGas` returns values 100–1000× higher than actual consumption. This is correct Mantle behavior — do not attempt to cap the limit lower. Actual gas used will be tiny; the fee is `gas_used × gas_price`, not `gas_limit × gas_price`.

4. **Gas price must match current network conditions.** Hardcoded gas constants fail when base fee moves. Always read `eth_gasPrice` fresh and add a small buffer (10–20%).

5. **Do not use the RealClaw agent-signer proxy** (e.g., `api2.byreal.io/byreal/api/privy-proxy/v1/sign/evm-transaction`) for Mantle transactions at this time — it hardcodes a stale gas price below the current base fee, causing every sign attempt to produce an untransmittable tx. Sign locally with a private key held by your agent's own signer, or use your wallet's native Mantle path.

6. **Store the idempotency key** returned by every build response. Pass it to `scripts/state.py record-cycle --idempotency-key <k>`. If the same key appears twice in one session, the tx is already broadcast — do NOT sign it again.

7. **Verify before recording.** Only call `state.py record-cycle` after `wait_for_transaction_receipt` returns `status: success`. Never record on broadcast-only.

## Field name quirks

- mantle-cli `unsigned_tx.value` is a **hex string** (e.g. `"0x0"`). Convert to int before passing to signers that expect integers.
- mantle-cli `unsigned_tx.chainId` is an **integer** (e.g. `5000`), not a hex string.
- `eth_getTransactionCount` returns a **hex string** from raw RPC but auto-parses to int in web3.py/viem.
