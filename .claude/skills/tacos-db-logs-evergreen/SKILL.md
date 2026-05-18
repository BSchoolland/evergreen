# TACOS Database Logs

TACOS has a wide event system. Read the Prisma schema at `/home/ben/Projects/TACOS/prisma/schema.prisma` for the `events`, `error_logs`, `cost_events`, and `billing_events` tables. Read the event implementation in `packages/scrape-core/src/events/` for how events are structured and created. Always `\d table_name` before querying — the schema is the source of truth, not this file.
