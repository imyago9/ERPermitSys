begin;

alter table public.erpermitsys_state
    add column if not exists revision bigint;

alter table public.erpermitsys_state
    add column if not exists updated_at timestamptz;

alter table public.erpermitsys_state
    add column if not exists updated_by text;

update public.erpermitsys_state
set
    revision = coalesce(revision, 0),
    updated_at = coalesce(updated_at, saved_at_utc, timezone('utc', now())),
    updated_by = coalesce(updated_by, '')
where
    revision is null
    or updated_at is null
    or updated_by is null;

alter table public.erpermitsys_state
    alter column revision set default 0;
alter table public.erpermitsys_state
    alter column updated_at set default timezone('utc', now());
alter table public.erpermitsys_state
    alter column updated_by set default '';

alter table public.erpermitsys_state
    alter column revision set not null;
alter table public.erpermitsys_state
    alter column updated_at set not null;
alter table public.erpermitsys_state
    alter column updated_by set not null;

do $$
begin
    if exists (
        select 1
        from pg_publication
        where pubname = 'supabase_realtime'
    ) and not exists (
        select 1
        from pg_publication_rel rel
        join pg_publication pub on pub.oid = rel.prpubid
        join pg_class cls on cls.oid = rel.prrelid
        join pg_namespace ns on ns.oid = cls.relnamespace
        where pub.pubname = 'supabase_realtime'
          and ns.nspname = 'public'
          and cls.relname = 'erpermitsys_state'
    ) then
        alter publication supabase_realtime add table public.erpermitsys_state;
    end if;
exception
    when insufficient_privilege then
        raise notice 'Skipping supabase_realtime publication update due to permissions.';
end
$$;

commit;
