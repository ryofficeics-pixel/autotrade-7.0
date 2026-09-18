# Autotrade 7.1 architecture

V2 remains the frozen baseline and may submit PAPER orders when `[paper_strategy].enabled = true`.
NautilusTrader remains the sole execution and portfolio authority. PAPER risk, freshness, accounting,
recovery, and persistence gates still decide whether entries are armed.

Entry V3 runs as a WebSocket shadow consumer. It reuses `GateMarketCapture`, `MarketEventNormalizer`, and `GateLocalOrderBook`; raw capture events remain hash chained and V3 decisions are written to a separate JSONL evidence file. A sequence gap, unsynchronized book, malformed event, or capture failure prevents a valid V3 decision for that symbol.

The REST poller still provides universe and contract metadata. It is not an Entry V3 timing source.
