# Market Data Specification

## Secondary TradingView Observation

TradingView is not an authoritative market-data source for Autotrade. Local CLI health, current chart
symbol/timeframe, a bounded quote and bounded current indicator values enter only the dashboard state.
They are not persisted and do not alter Gate/Nautilus values or trading decisions.

OHLCV, Pine, strategy, depth and screenshot extraction remain disabled. Upstream usage restrictions
must be resolved before any persisted data-collection or automated decision experiment is designed.

## Goal

Capture enough real microstructure data to research short-horizon signals and realistic paper execution.

## Required Streams

Per symbol, capture where available:

- L2 order book updates/snapshots
- trades
- best bid
- best ask
- bid size
- ask size
- mark price
- index price
- funding rate
- instrument metadata
- exchange timestamp
- local receive timestamp

## Required Derived Fields

- mid price
- spread absolute
- spread basis points
- top-N bid depth
- top-N ask depth
- order-book imbalance
- microprice
- trade-flow imbalance
- rolling realized volatility
- short-horizon volume
- update rate
- observed feed latency

## Timestamp Policy

Store:
- exchange timestamp;
- local receive timestamp;
- normalized UTC timestamp.

Never replace exchange time with local time.

Nautilus uses nanosecond-precision UTC timestamps internally; preserve the highest available source precision.

## Storage

Preferred:
- append/replay-friendly data;
- Parquet for historical research;
- lightweight local state DB for operational metadata.

Keep raw data separate from derived features.

Current Phase 1 implementation starts with append-only JSONL capture for public Gate WebSocket
messages. Each normalized row must preserve the original Gate payload, exchange timestamp, normalized
UTC exchange time where available, and local receive UTC. Parquet conversion belongs after raw capture
proves stable.

The runnable capture command now connects to the public Gate futures WebSocket, records trades,
best bid/ask, L2 updates and ticker messages, reconciles each symbol against a REST depth snapshot,
and writes a hash-chained event stream plus an atomic manifest. Capture is isolated from trading and
cannot influence entries, risk, sizing or execution. A dataset is accepted as `COMPLETE` only after
one uninterrupted connection, zero sequence gaps, zero drops and a synchronized book sequence for
every requested symbol. Replay deterministically reconstructs absolute-size depth, deletes zero-size
levels, rejects crossed/empty books and reports a digest over final spread, depth, imbalance and
microprice metrics. Long-duration acceptance and conversion into strategy events remain pending.

## Integrity Checks

Halt trading research for a symbol when:

- sequence gaps cannot be recovered;
- book becomes crossed/invalid;
- data age exceeds threshold;
- local/exchange clock difference becomes implausible;
- reconnect does not restore a valid snapshot.

For `futures.order_book_update`, track `U` and `u` per contract. A full snapshot resets the local
depth ID. With `expected = previous u + 1`, a delta is continuous only when
`U <= expected <= u`; a delta whose `u < expected` is a duplicate. Any forward gap requires dropping
the local book and rebuilding from a fresh snapshot before that symbol can be trusted.

## Retention

During Phase 1 collect continuously while the laptop is running.

Do not prematurely aggregate away raw data required for later replay.

Frozen research evidence is stored under `research/datasets/<dataset_id>/`. A freeze verifies the
source chain, snapshots a complete source byte prefix, normalizes the observations, and records the
source-prefix SHA-256, normalized event hash, row count, sample window, timestamp policy, Git commit,
and capture-config hash. The manifest and events are write-once/read-only; mutation causes verification
failure. Canonical datasets, experiment ledgers, registries, and lifecycle history have no automatic
deletion. `reports/research-latest.*` is regenerable and is not the evidence authority.

## AMA Control Evidence

The independent XAU control writes schema-v2, append-only, hash-chained JSONL to
`data/runs/<run_id>/ama-control-v2.jsonl`. Every accepted poll receives one observation identity.
Paired raw and filtered KAMA20 candidate records are emitted on a decision-state change or at the
configured 30-second evaluation cadence. Records include run/session,
strategy/version/parameter identity, UTC observation time, market inputs, KAMA values, quote-step ATR
proxy, hysteresis, regime, reference state, risk notional, execution assumptions, allow/reject reason,
and an initially empty outcome.

Counterfactual outcomes are appended only after later observations cross 10-second, 30-second,
1-minute, 3-minute, 5-minute, and 15-minute horizons. They retain the original decision identity and
label avoided loss or missed opportunity only from the later observed price. No future value is added
to the original decision record.

The ATR field is explicitly a quote-step movement proxy, not candle true range. REST poll receive time
is the observation clock because the ticker endpoint does not provide a reliable event timestamp for
this control. These limitations must remain visible in audit conclusions.

The control atomically refreshes the requested `reports/*_latest.md` research surfaces plus structured
JSON summaries. `reports/` is intentionally Git-ignored because those files are continuously rewritten
runtime derivatives; the hash-chained run dataset is the evidence source of truth.

## WIDE_CRYPTO_RESEARCH_V1

The wide-crypto family freezes public Gate USDT perpetual evidence for a snapshot-selected 20-market
universe. It stores 15-minute and 1-hour OHLCV, derived complete 4-hour bars, quote turnover, the
selection snapshot, funding snapshot, bid and ask snapshot, mark and index snapshot, contract rules,
listing age, rejection reasons, receive time, Git commit, config hash, source identity, and file hashes.

Historical executable BBO, historical funding, queue position, and fills are unavailable from the
selected candle source. They remain explicitly unavailable. Snapshot spread and funding may be used
only as labeled proxies in sensitivity analysis. Current-universe selection creates survivorship bias,
which blocks promotion until point-in-time universe history exists.
