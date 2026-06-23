# TACOS Staging Database

Applies to the TACOS `code_db` project (project 1). Read-only access to the TACOS staging Supabase database (PostgreSQL).

## Connection

Fetch the connection string from AWS SSM:

```bash
DB_URL=$(aws ssm get-parameter --name "/tacos/staging/DATABASE_URL" --with-decryption --query "Parameter.Value" --output text)
```

Then query with psql:

```bash
psql "$DB_URL" -c "YOUR QUERY HERE"
```

## Rules

- **Read-only.** Never run INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or any other mutating statement.
- Always fetch the connection string from SSM at runtime — do not hardcode it.
- For schema discovery, run `\dt` to list tables and `\d tablename` to describe columns.
- Keep queries focused. Use LIMIT when exploring unfamiliar tables.

## Project Details

- Supabase project: TACOS-stg
- Region: us-east-1
- Database host: db.ruvtsqzpahgxqjphbake.supabase.co
