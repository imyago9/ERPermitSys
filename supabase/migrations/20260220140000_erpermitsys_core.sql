begin;

create table if not exists public.erpermitsys_state (
    app_id text primary key,
    schema_version integer not null default 3,
    backend text not null,
    saved_at_utc timestamptz not null default timezone('utc', now()),
    payload jsonb not null default '{}'::jsonb
);

comment on table public.erpermitsys_state is
    'Shared app state payload for ERPermitSys Supabase backend.';

alter table public.erpermitsys_state enable row level security;

grant usage on schema public to public;
grant select, insert, update on table public.erpermitsys_state to public;

do $$
begin
    if not exists (
        select 1
        from pg_policies
        where schemaname = 'public'
          and tablename = 'erpermitsys_state'
          and policyname = 'erpermitsys_state_select'
    ) then
        create policy erpermitsys_state_select
            on public.erpermitsys_state
            for select
            to public
            using (app_id = 'erpermitsys');
    end if;

    if not exists (
        select 1
        from pg_policies
        where schemaname = 'public'
          and tablename = 'erpermitsys_state'
          and policyname = 'erpermitsys_state_insert'
    ) then
        create policy erpermitsys_state_insert
            on public.erpermitsys_state
            for insert
            to public
            with check (app_id = 'erpermitsys');
    end if;

    if not exists (
        select 1
        from pg_policies
        where schemaname = 'public'
          and tablename = 'erpermitsys_state'
          and policyname = 'erpermitsys_state_update'
    ) then
        create policy erpermitsys_state_update
            on public.erpermitsys_state
            for update
            to public
            using (app_id = 'erpermitsys')
            with check (app_id = 'erpermitsys');
    end if;
end
$$;

insert into storage.buckets (id, name, public)
values ('erpermitsys-documents', 'erpermitsys-documents', false)
on conflict (id) do nothing;

grant usage on schema storage to public;
grant select on storage.buckets to public;
grant select, insert, update, delete on storage.objects to public;

do $$
begin
    if not exists (
        select 1
        from pg_policies
        where schemaname = 'storage'
          and tablename = 'objects'
          and policyname = 'erpermitsys_objects_select'
    ) then
        create policy erpermitsys_objects_select
            on storage.objects
            for select
            to public
            using (bucket_id = 'erpermitsys-documents');
    end if;

    if not exists (
        select 1
        from pg_policies
        where schemaname = 'storage'
          and tablename = 'objects'
          and policyname = 'erpermitsys_objects_insert'
    ) then
        create policy erpermitsys_objects_insert
            on storage.objects
            for insert
            to public
            with check (bucket_id = 'erpermitsys-documents');
    end if;

    if not exists (
        select 1
        from pg_policies
        where schemaname = 'storage'
          and tablename = 'objects'
          and policyname = 'erpermitsys_objects_update'
    ) then
        create policy erpermitsys_objects_update
            on storage.objects
            for update
            to public
            using (bucket_id = 'erpermitsys-documents')
            with check (bucket_id = 'erpermitsys-documents');
    end if;

    if not exists (
        select 1
        from pg_policies
        where schemaname = 'storage'
          and tablename = 'objects'
          and policyname = 'erpermitsys_objects_delete'
    ) then
        create policy erpermitsys_objects_delete
            on storage.objects
            for delete
            to public
            using (bucket_id = 'erpermitsys-documents');
    end if;
end
$$;

commit;
