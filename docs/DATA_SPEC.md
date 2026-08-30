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

## Integrity Checks

Halt trading research for a symbol when:

- sequence gaps cannot be recovered;
- book becomes crossed/invalid;
- data age exceeds threshold;
- local/exchange clock difference becomes implausible;
- reconnect does not restore a valid snapshot.

For `futures.order_book_update`, track `U` and `u` per contract. A full snapshot resets the local
depth ID; a delta is continuous only when `U == previous u + 1`. Any gap requires dropping the local
book and rebuilding from a fresh snapshot before that symbol can be trusted.

## Retention

During Phase 1 collect continuously while the laptop is running.

Do not prematurely aggregate away raw data required for later replay.
