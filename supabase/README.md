# Supabase Migrations

This project keeps SQL migrations in two folders:

- `supabase/sql`: ordered, repo-managed migration files used for apply/checksum history.
- `supabase/migrations`: timestamped files used to sync Supabase remote migration history.

## Current baseline migration

- `001_erpermitsys_core.sql` / `20260220140000_erpermitsys_core.sql`
- `002_erpermitsys_realtime_revision.sql` / `20260220153000_erpermitsys_realtime_revision.sql`
- `003_erpermitsys_relational_snapshot.sql` / `20260221110000_erpermitsys_relational_snapshot.sql`
- `004_erpermitsys_incremental_sync.sql` / `20260221153000_erpermitsys_incremental_sync.sql`
- `005_erpermitsys_payload_delta_and_tombstone_retention.sql` / `20260221170000_erpermitsys_payload_delta_and_tombstone_retention.sql`

These migrations create the shared state metadata row, normalized snapshot tables, and storage policies required by the app:

- `public.erpermitsys_state`
- `public.erpermitsys_contacts`
- `public.erpermitsys_jurisdictions`
- `public.erpermitsys_properties`
- `public.erpermitsys_permits`
- `public.erpermitsys_document_templates`
- `public.erpermitsys_active_document_templates`
- storage bucket `erpermitsys-documents`
- RLS policies/grants for shared access with a key-only workflow
- revision/realtime metadata columns for multi-client sync (`revision`, `updated_at`, `updated_by`)
- atomic snapshot RPC `public.erpermitsys_save_snapshot(...)` used by desktop clients
- incremental per-record sync RPCs (`public.erpermitsys_fetch_snapshot(...)`, `public.erpermitsys_apply_changes(...)`)
- tombstone delete support on entity tables via `deleted_at` so deletes replicate safely across clients
- periodic tombstone pruning (retention cleanup) to prevent unbounded soft-delete growth
- incremental `payload` mirror updates without full table snapshot rebuilds on every write

`public.erpermitsys_state.payload` is retained as a compatibility mirror for older clients,
but current builds read/write the relational tables through incremental RPC updates.

## Required env for SQL apply

To execute migrations with `psql`, your env file must include a Postgres connection URL:

```env
SUPABASE_DB_URL=postgresql://...
```

`URL` and `PUBLISHABLE_KEY` are enough for app runtime API calls, but they are not a Postgres
connection string and cannot execute DDL migrations by themselves.
