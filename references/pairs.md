# Pair Registry — ClawHack Whitelisted DEXes

All addresses are Mantle mainnet (chain_id 5000). Use `--json` flag on every `mantle-cli` call.

## Routers

| DEX | Provider flag | Router | Notes |
|---|---|---|---|
| Agni Finance | `agni` | `0xb52b1f5e08c04a8c33f4c7363fa2de23b9bc169f` (SmartRouter) | V3; auto-routes across pools |
| Agni direct | `agni` | `0x319B69888b0d11cEC22caA5034e25FfFBDc88421` (SwapRouter) | V3 direct pool |
| Merchant Moe | `merchant_moe` | `0x013e138EF6008ae5FDFDE29700e3f2Bc61d21E3a` (LB Router V2.2) | Liquidity Book |
| Fluxion | `fluxion` | `0x5628a59df0ecac3f3171f877a94beb26ba6dfaa0` (V3 SwapRouter) | V3 |

## Stable Pairs (primary for volume farming)

**Verified live on Mantle mainnet — USDC ↔ USDT probe returned clean quotes on Agni + Merchant Moe; Fluxion returned `no route`.** Fluxion does not have stable-pair liquidity — exclude it from volume rotation.

| Pair | Agni fee_tier | Merchant Moe bin_step | Fluxion | Expected round-trip friction |
|---|---|---|---|---|
| USDC ↔ USDT | 100 | 1 | no route | ~0.05–0.10% (Agni verified: 1 USDC → 0.9995 USDT) |
| USDC ↔ USDT0 | 100 | 1 | no route | ~0.05–0.10% |
| USDT ↔ USDT0 | — | 1 | no route | ~0.02–0.06% (MM only) |
| USDC ↔ USDe | 500 | 1 | no route | ~0.10–0.20% |
| USDT ↔ USDe | — | 1 | no route | ~0.10–0.20% (MM only) |
| USDe ↔ USDT0 | — | 1 | no route | ~0.10–0.20% (MM only) |

Always run a $10 probe quote first before committing a full-balance swap:
```
mantle-cli defi swap-quote --in USDC --out USDT --amount 10 --provider best --json
```
Discard any pair where the quote fails or friction > 0.5%.

## Volatile Pairs (avoid during volume loops)

Listed for reference only. Not to be used in the volume farming loop — price drift between quote and execution becomes realized loss.

| Pair | Agni fee_tier | Merchant Moe bin_step | Fluxion fee_tier |
|---|---|---|---|
| WMNT ↔ USDC | 10000 | 20 | — |
| WMNT ↔ USDT | — | 15 | — |
| WMNT ↔ USDT0 | 500 | 20 | — |
| WMNT ↔ USDe | — | 20 | — |
| WETH ↔ WMNT | 500 | — | — |

## Whitelisted Token Addresses

Stables:
- USDC: `0x09Bc4E0D864854c6aFB6eB9A9cdF58aC190D0dF9` (6 decimals)
- USDT: `0x201EBa5CC46D216Ce6DC03F6a759e8E766e956aE` (6 decimals)
- USDT0: `0x779Ded0c9e1022225f8E0630b35a9b54bE713736` (6 decimals)
- USDe: `0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34` (18 decimals)

Volatile (reference only):
- WMNT: `0x78c1b0C915c4FAA5FffA6CAbf0219DA63d7f4cb8` (18 decimals)
- WETH: `0xdEAddEaDdeadDEadDEADDEAddEADDEAddead1111` (18 decimals)

You can pass either the symbol or the address to any `mantle-cli` command. Symbols are safer — no typo risk.

## Approvals Needed (one-time max approves)

Stable rotation uses Agni + Merchant Moe only. Approve:

```
USDC → Agni SwapRouter (0x319B69888b0d11cEC22caA5034e25FfFBDc88421)
USDC → LB Router V2.2 (0x013e138EF6008ae5FDFDE29700e3f2Bc61d21E3a)
USDT → (same two routers)
USDT0 → (same two routers)
USDe → (same two routers)
```

8 approves total. Fire them one at a time, sign, wait for receipt, next. Skip approves for tokens you don't plan to rotate through (e.g., if never touching USDe, skip its 2 approves).

## Live Liquidity Check

Before the first cycle on any pair, run a test quote for $10 to confirm the pool exists and slippage is acceptable:

```
mantle-cli defi swap-quote --in USDC --out USDT --amount 10 --provider agni --json
```

Discard any pair where the quote fails or friction > 0.5% on a $10 probe.
