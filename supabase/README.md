# Supabase Migrations

This project keeps SQL migrations in two folders:

- `supabase/sql`: ordered, repo-managed migration files used for apply/checksum history.
- `supabase/migrations`: timestamped files used to sync Supabase remote migration history.

## Current baseline migration

- `001_erpermitsys_core.sql` / `20260220140000_erpermitsys_core.sql`
- `002_erpermitsys_realtime_revision.sql` / `20260220153000_erpermitsys_realtime_revision.sql`

This migration creates the state table and storage policies required by the app:

- `public.erpermitsys_state`
- storage bucket `erpermitsys-documents`
- RLS policies/grants for shared access with a key-only workflow
- revision/realtime metadata columns for multi-client sync (`revision`, `updated_at`, `updated_by`)

## Required env for SQL apply

To execute migrations with `psql`, your env file must include a Postgres connection URL:

```env
SUPABASE_DB_URL=postgresql://...
```

`URL` and `PUBLISHABLE_KEY` are enough for app runtime API calls, but they are not a Postgres
connection string and cannot execute DDL migrations by themselves.
