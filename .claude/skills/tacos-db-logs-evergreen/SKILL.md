# TACOS Database Logs & Wide Event System

TACOS uses a wide event system for observability. All tables live in the same Postgres database accessed via `DATABASE_URL`.

## Core Tables

### `events` — Wide Events (primary logging)
One row per HTTP request or background job. The `data` JSON column carries the full structured payload.

Key columns: `id`, `timestamp`, `type` ("request"|"job"), `level` ("info"|"warn"|"error"), `requestId` (req_*), `traceId` (trc_*), `userId`, `path`, `method`, `statusCode`, `durationMs`, `error` (boolean), `data` (JSON).

The `data` JSON contains sections: `request`, `session`, `job`, `user`, `auth`, `business`, `llm`, `error`, `performance`, `scraper`, `transcription`, `trace`, `polling`. Route handlers annotate the in-flight event using `setEventField("business.action", "...")`.

### `error_logs` — Legacy error log
Written by the Express error handler for non-404 errors. Columns: `id`, `code` (e.g. "SCRAPE_FAILED"), `message`, `stack`, `context` (JSON), `requestId`, `userId`, `statusCode`, `resolved` (boolean), `createdAt`.

### `cost_events` — Cost ledger
One row per billable action. Columns: `id`, `timestamp`, `userId`, `category` ("llm"|"scraping"|"transcription"), `feature` ("script-create"|"video-analysis"|"onboarding"|"job-*"|etc.), `costUsd`, `metadata` (JSON), `eventId` (links to wide event traceId).

### `billing_events` — Stripe payment history
Columns: `id`, `userId`, `stripeEventId`, `type`, `amountCents`, `currency`, `metadata`, `createdAt`.

## Useful Queries

Recent errors:
```sql
SELECT timestamp, path, data->'error'->>'code' as err_code, data->'error'->>'message' as err_msg FROM events WHERE error = true ORDER BY timestamp DESC LIMIT 20;
```

Unresolved error_logs:
```sql
SELECT code, message, "createdAt" FROM error_logs WHERE resolved = false ORDER BY "createdAt" DESC LIMIT 20;
```

Slow requests (>2s):
```sql
SELECT timestamp, path, "durationMs", data->'performance' as perf FROM events WHERE type = 'request' AND "durationMs" > 2000 ORDER BY "durationMs" DESC LIMIT 20;
```

Cost by category today:
```sql
SELECT category, SUM("costUsd") as total FROM cost_events WHERE timestamp >= CURRENT_DATE GROUP BY category;
```

Failed scrape jobs:
```sql
SELECT timestamp, data->'job'->>'job_id' as job_id, data->'error'->>'message' as err FROM events WHERE type = 'job' AND error = true ORDER BY timestamp DESC LIMIT 20;
```

## Connecting

Local dev: `psql "$DATABASE_URL"` (from TACOS/.env)
Staging: `ssh tacos-staging "cd ~/TACOS && psql \"\$(grep '^DATABASE_URL=' .env | cut -d= -f2-)\" -c 'QUERY'"`

The Prisma schema at `/home/ben/Projects/TACOS/prisma/schema.prisma` is the source of truth for all column names. Always check with `\d table_name` before querying, especially on staging which may be on an older migration.
