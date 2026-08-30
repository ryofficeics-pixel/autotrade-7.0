# Codex Start Prompt

Read `AGENTS.md` and every file under `docs/` before modifying code.

Implement only Phase 1A from `docs/IMPLEMENTATION_ROADMAP.md`.

Rules:

- Phase 1 is PAPER ONLY.
- Do not implement any real-order path.
- Use NautilusTrader as the single canonical trading engine.
- Do not run Hummingbot as a competing execution engine.
- Hummingbot is reference material for later strategy work.
- Apply Ponytail/YAGNI discipline: smallest safe implementation, reuse native/platform/Nautilus capabilities, avoid speculative abstractions.
- Safety, validation, persistence integrity and diagnostics are never simplified away.
- Target Windows laptop operation.
- Keep future VPS migration possible through externalized config, but do not add VPS/cloud complexity.
- Add tests for every implemented behavior.
- Do not build the dashboard yet.
- At the end, audit the implementation against the Phase 1A exit criteria and report any unresolved risks.
