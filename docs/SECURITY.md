# Security

## Phase 1

No real trading credentials are required.

Prefer public Gate.io market-data endpoints only where possible.

The wide-crypto backfill uses unauthenticated Gate public endpoints. It sends no API key, reads no
environment secret, and stores no credential or browser session material in its immutable dataset.

## Secrets

When private TestNet/live credentials are introduced later:

- store in `.env` or OS secret mechanism;
- never commit;
- never log;
- never expose to browser;
- never send to analytics;
- never include withdrawal permission;
- use least privilege.

## Git

Required `.gitignore` entries:

```gitignore
.env
.env.*
!.env.example
data/
logs/
*.db
*.sqlite
playwright-report/
test-results/
```

## Browser

The dashboard must never receive exchange API secrets.

Browser talks only to the local application API.

## TradingView CDP

- port 9222 must listen only on `127.0.0.1` or `::1`;
- never expose CDP to LAN, internet, reverse proxies, router forwarding or public container mappings;
- the adapter overwrites `TV_CDP_HOST` with `127.0.0.1` and never returns the endpoint to the browser;
- subprocesses use a fixed argument list and `shell=False`;
- raw CLI stdout/stderr is not logged on error;
- TradingView credentials, cookies, tokens and session material must not be persisted or displayed;
- screenshots are rejected by Phase 1 configuration;
- dependency advisories in the pinned upstream checkout require review before enabling.

## Local Network

Phase 1 dashboard should bind to localhost by default.

Do not expose the dashboard to the public internet.

## Future Live Gate

Live activation must require:
- separate live credentials;
- trading permission only;
- explicit environment;
- explicit confirmation gate;
- risk checks;
- documented manual enablement.

Withdrawal capability must remain disabled.
