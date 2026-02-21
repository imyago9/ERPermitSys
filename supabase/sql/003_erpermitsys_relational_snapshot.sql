begin;

create table if not exists public.erpermitsys_contacts (
    app_id text not null default 'erpermitsys',
    contact_id text not null,
    name text not null default '',
    numbers jsonb not null default '[]'::jsonb,
    emails jsonb not null default '[]'::jsonb,
    roles jsonb not null default '[]'::jsonb,
    contact_methods jsonb not null default '[]'::jsonb,
    list_color text not null default '',
    updated_at timestamptz not null default timezone('utc', now()),
    primary key (app_id, contact_id)
);

create table if not exists public.erpermitsys_jurisdictions (
    app_id text not null default 'erpermitsys',
    jurisdiction_id text not null,
    name text not null default '',
    jurisdiction_type text not null default 'county',
    parent_county text not null default '',
    portal_urls jsonb not null default '[]'::jsonb,
    contact_ids jsonb not null default '[]'::jsonb,
    portal_vendor text not null default '',
    notes text not null default '',
    list_color text not null default '',
    updated_at timestamptz not null default timezone('utc', now()),
    primary key (app_id, jurisdiction_id)
);

create table if not exists public.erpermitsys_properties (
    app_id text not null default 'erpermitsys',
    property_id text not null,
    display_address text not null default '',
    parcel_id text not null default '',
    parcel_id_norm text not null default '',
    jurisdiction_id text not null default '',
    contact_ids jsonb not null default '[]'::jsonb,
    list_color text not null default '',
    tags jsonb not null default '[]'::jsonb,
    notes text not null default '',
    updated_at timestamptz not null default timezone('utc', now()),
    primary key (app_id, property_id)
);

create table if not exists public.erpermitsys_permits (
    app_id text not null default 'erpermitsys',
    permit_id text not null,
    property_id text not null default '',
    permit_type text not null default 'building',
    permit_number text not null default '',
    status text not null default 'requested',
    next_action_text text not null default '',
    next_action_due text not null default '',
    request_date text not null default '',
    application_date text not null default '',
    issued_date text not null default '',
    final_date text not null default '',
    completion_date text not null default '',
    parties jsonb not null default '[]'::jsonb,
    events jsonb not null default '[]'::jsonb,
    document_slots jsonb not null default '[]'::jsonb,
    document_folders jsonb not null default '[]'::jsonb,
    documents jsonb not null default '[]'::jsonb,
    updated_at timestamptz not null default timezone('utc', now()),
    primary key (app_id, permit_id)
);

create table if not exists public.erpermitsys_document_templates (
    app_id text not null default 'erpermitsys',
    template_id text not null,
    name text not null default '',
    permit_type text not null default 'building',
    slots jsonb not null default '[]'::jsonb,
    notes text not null default '',
    updated_at timestamptz not null default timezone('utc', now()),
    primary key (app_id, template_id)
);

create table if not exists public.erpermitsys_active_document_templates (
    app_id text not null default 'erpermitsys',
    permit_type text not null,
    template_id text not null,
    updated_at timestamptz not null default timezone('utc', now()),
    primary key (app_id, permit_type)
);

grant usage on schema public to public;
grant select, insert, update, delete on table public.erpermitsys_contacts to public;
grant select, insert, update, delete on table public.erpermitsys_jurisdictions to public;
grant select, insert, update, delete on table public.erpermitsys_properties to public;
grant select, insert, update, delete on table public.erpermitsys_permits to public;
grant select, insert, update, delete on table public.erpermitsys_document_templates to public;
grant select, insert, update, delete on table public.erpermitsys_active_document_templates to public;

do $$
declare
    tbl text;
    select_policy text;
    insert_policy text;
    update_policy text;
    delete_policy text;
begin
    for tbl in
        select unnest(
            array[
                'erpermitsys_contacts',
                'erpermitsys_jurisdictions',
                'erpermitsys_properties',
                'erpermitsys_permits',
                'erpermitsys_document_templates',
                'erpermitsys_active_document_templates'
            ]
        )
    loop
        execute format('alter table public.%I enable row level security', tbl);

        select_policy := format('eps_%s_select', replace(tbl, 'erpermitsys_', ''));
        if not exists (
            select 1
            from pg_policies
            where schemaname = 'public'
              and tablename = tbl
              and policyname = select_policy
        ) then
            execute format(
                'create policy %I on public.%I for select to public using (app_id = ''erpermitsys'')',
                select_policy,
                tbl
            );
        end if;

        insert_policy := format('eps_%s_insert', replace(tbl, 'erpermitsys_', ''));
        if not exists (
            select 1
            from pg_policies
            where schemaname = 'public'
              and tablename = tbl
              and policyname = insert_policy
        ) then
            execute format(
                'create policy %I on public.%I for insert to public with check (app_id = ''erpermitsys'')',
                insert_policy,
                tbl
            );
        end if;

        update_policy := format('eps_%s_update', replace(tbl, 'erpermitsys_', ''));
        if not exists (
            select 1
            from pg_policies
            where schemaname = 'public'
              and tablename = tbl
              and policyname = update_policy
        ) then
            execute format(
                'create policy %I on public.%I for update to public '
                || 'using (app_id = ''erpermitsys'') with check (app_id = ''erpermitsys'')',
                update_policy,
                tbl
            );
        end if;

        delete_policy := format('eps_%s_delete', replace(tbl, 'erpermitsys_', ''));
        if not exists (
            select 1
            from pg_policies
            where schemaname = 'public'
              and tablename = tbl
              and policyname = delete_policy
        ) then
            execute format(
                'create policy %I on public.%I for delete to public using (app_id = ''erpermitsys'')',
                delete_policy,
                tbl
            );
        end if;
    end loop;
end
$$;

create or replace function public.erpermitsys_save_snapshot(
    p_app_id text,
    p_expected_revision bigint,
    p_schema_version integer,
    p_saved_at_utc timestamptz,
    p_updated_by text,
    p_contacts jsonb,
    p_jurisdictions jsonb,
    p_properties jsonb,
    p_permits jsonb,
    p_document_templates jsonb,
    p_active_document_template_ids jsonb
) returns jsonb
language plpgsql
as $$
declare
    v_app_id text := coalesce(nullif(trim(p_app_id), ''), 'erpermitsys');
    v_expected_revision bigint := greatest(0, coalesce(p_expected_revision, 0));
    v_current_revision bigint := 0;
    v_next_revision bigint := 0;
    v_saved_at timestamptz := coalesce(p_saved_at_utc, timezone('utc', now()));
    v_updated_by text := coalesce(trim(p_updated_by), '');
begin
    insert into public.erpermitsys_state (
        app_id,
        schema_version,
        backend,
        saved_at_utc,
        updated_at,
        updated_by,
        revision,
        payload
    )
    values (
        v_app_id,
        coalesce(p_schema_version, 3),
        'supabase',
        v_saved_at,
        timezone('utc', now()),
        v_updated_by,
        0,
        '{}'::jsonb
    )
    on conflict (app_id) do nothing;

    select coalesce(revision, 0)
    into v_current_revision
    from public.erpermitsys_state
    where app_id = v_app_id
    for update;

    if v_current_revision <> v_expected_revision then
        return jsonb_build_object(
            'applied', false,
            'conflict', true,
            'revision', v_current_revision
        );
    end if;

    delete from public.erpermitsys_active_document_templates where app_id = v_app_id;
    delete from public.erpermitsys_document_templates where app_id = v_app_id;
    delete from public.erpermitsys_permits where app_id = v_app_id;
    delete from public.erpermitsys_properties where app_id = v_app_id;
    delete from public.erpermitsys_jurisdictions where app_id = v_app_id;
    delete from public.erpermitsys_contacts where app_id = v_app_id;

    insert into public.erpermitsys_contacts (
        app_id, contact_id, name, numbers, emails, roles, contact_methods, list_color, updated_at
    )
    select
        v_app_id,
        trim(coalesce(item->>'contact_id', '')),
        trim(coalesce(item->>'name', '')),
        case when jsonb_typeof(item->'numbers') = 'array' then item->'numbers' else '[]'::jsonb end,
        case when jsonb_typeof(item->'emails') = 'array' then item->'emails' else '[]'::jsonb end,
        case when jsonb_typeof(item->'roles') = 'array' then item->'roles' else '[]'::jsonb end,
        case when jsonb_typeof(item->'contact_methods') = 'array' then item->'contact_methods' else '[]'::jsonb end,
        trim(coalesce(item->>'list_color', '')),
        timezone('utc', now())
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_contacts, '[]'::jsonb)) = 'array'
            then coalesce(p_contacts, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) as item
    where trim(coalesce(item->>'contact_id', '')) <> '';

    insert into public.erpermitsys_jurisdictions (
        app_id,
        jurisdiction_id,
        name,
        jurisdiction_type,
        parent_county,
        portal_urls,
        contact_ids,
        portal_vendor,
        notes,
        list_color,
        updated_at
    )
    select
        v_app_id,
        trim(coalesce(item->>'jurisdiction_id', '')),
        trim(coalesce(item->>'name', '')),
        trim(coalesce(item->>'jurisdiction_type', 'county')),
        trim(coalesce(item->>'parent_county', '')),
        case when jsonb_typeof(item->'portal_urls') = 'array' then item->'portal_urls' else '[]'::jsonb end,
        case when jsonb_typeof(item->'contact_ids') = 'array' then item->'contact_ids' else '[]'::jsonb end,
        trim(coalesce(item->>'portal_vendor', '')),
        trim(coalesce(item->>'notes', '')),
        trim(coalesce(item->>'list_color', '')),
        timezone('utc', now())
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_jurisdictions, '[]'::jsonb)) = 'array'
            then coalesce(p_jurisdictions, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) as item
    where trim(coalesce(item->>'jurisdiction_id', '')) <> '';

    insert into public.erpermitsys_properties (
        app_id,
        property_id,
        display_address,
        parcel_id,
        parcel_id_norm,
        jurisdiction_id,
        contact_ids,
        list_color,
        tags,
        notes,
        updated_at
    )
    select
        v_app_id,
        trim(coalesce(item->>'property_id', '')),
        trim(coalesce(item->>'display_address', '')),
        trim(coalesce(item->>'parcel_id', '')),
        trim(coalesce(item->>'parcel_id_norm', '')),
        trim(coalesce(item->>'jurisdiction_id', '')),
        case when jsonb_typeof(item->'contact_ids') = 'array' then item->'contact_ids' else '[]'::jsonb end,
        trim(coalesce(item->>'list_color', '')),
        case when jsonb_typeof(item->'tags') = 'array' then item->'tags' else '[]'::jsonb end,
        trim(coalesce(item->>'notes', '')),
        timezone('utc', now())
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_properties, '[]'::jsonb)) = 'array'
            then coalesce(p_properties, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) as item
    where trim(coalesce(item->>'property_id', '')) <> '';

    insert into public.erpermitsys_permits (
        app_id,
        permit_id,
        property_id,
        permit_type,
        permit_number,
        status,
        next_action_text,
        next_action_due,
        request_date,
        application_date,
        issued_date,
        final_date,
        completion_date,
        parties,
        events,
        document_slots,
        document_folders,
        documents,
        updated_at
    )
    select
        v_app_id,
        trim(coalesce(item->>'permit_id', '')),
        trim(coalesce(item->>'property_id', '')),
        trim(coalesce(item->>'permit_type', 'building')),
        trim(coalesce(item->>'permit_number', '')),
        trim(coalesce(item->>'status', 'requested')),
        trim(coalesce(item->>'next_action_text', '')),
        trim(coalesce(item->>'next_action_due', '')),
        trim(coalesce(item->>'request_date', '')),
        trim(coalesce(item->>'application_date', '')),
        trim(coalesce(item->>'issued_date', '')),
        trim(coalesce(item->>'final_date', '')),
        trim(coalesce(item->>'completion_date', '')),
        case when jsonb_typeof(item->'parties') = 'array' then item->'parties' else '[]'::jsonb end,
        case when jsonb_typeof(item->'events') = 'array' then item->'events' else '[]'::jsonb end,
        case when jsonb_typeof(item->'document_slots') = 'array' then item->'document_slots' else '[]'::jsonb end,
        case when jsonb_typeof(item->'document_folders') = 'array' then item->'document_folders' else '[]'::jsonb end,
        case when jsonb_typeof(item->'documents') = 'array' then item->'documents' else '[]'::jsonb end,
        timezone('utc', now())
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_permits, '[]'::jsonb)) = 'array'
            then coalesce(p_permits, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) as item
    where trim(coalesce(item->>'permit_id', '')) <> '';

    insert into public.erpermitsys_document_templates (
        app_id,
        template_id,
        name,
        permit_type,
        slots,
        notes,
        updated_at
    )
    select
        v_app_id,
        trim(coalesce(item->>'template_id', '')),
        trim(coalesce(item->>'name', '')),
        trim(coalesce(item->>'permit_type', 'building')),
        case when jsonb_typeof(item->'slots') = 'array' then item->'slots' else '[]'::jsonb end,
        trim(coalesce(item->>'notes', '')),
        timezone('utc', now())
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_document_templates, '[]'::jsonb)) = 'array'
            then coalesce(p_document_templates, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) as item
    where trim(coalesce(item->>'template_id', '')) <> '';

    insert into public.erpermitsys_active_document_templates (
        app_id,
        permit_type,
        template_id,
        updated_at
    )
    select
        v_app_id,
        trim(key),
        trim(value),
        timezone('utc', now())
    from jsonb_each_text(
        case when jsonb_typeof(coalesce(p_active_document_template_ids, '{}'::jsonb)) = 'object'
            then coalesce(p_active_document_template_ids, '{}'::jsonb)
            else '{}'::jsonb
        end
    )
    where trim(key) <> ''
      and trim(value) <> '';

    v_next_revision := v_current_revision + 1;
    update public.erpermitsys_state
    set
        schema_version = coalesce(p_schema_version, schema_version, 3),
        backend = 'supabase',
        saved_at_utc = v_saved_at,
        updated_at = timezone('utc', now()),
        updated_by = v_updated_by,
        revision = v_next_revision,
        payload = jsonb_build_object(
            'contacts',
            case when jsonb_typeof(coalesce(p_contacts, '[]'::jsonb)) = 'array'
                then coalesce(p_contacts, '[]'::jsonb)
                else '[]'::jsonb
            end,
            'jurisdictions',
            case when jsonb_typeof(coalesce(p_jurisdictions, '[]'::jsonb)) = 'array'
                then coalesce(p_jurisdictions, '[]'::jsonb)
                else '[]'::jsonb
            end,
            'properties',
            case when jsonb_typeof(coalesce(p_properties, '[]'::jsonb)) = 'array'
                then coalesce(p_properties, '[]'::jsonb)
                else '[]'::jsonb
            end,
            'permits',
            case when jsonb_typeof(coalesce(p_permits, '[]'::jsonb)) = 'array'
                then coalesce(p_permits, '[]'::jsonb)
                else '[]'::jsonb
            end,
            'document_templates',
            case when jsonb_typeof(coalesce(p_document_templates, '[]'::jsonb)) = 'array'
                then coalesce(p_document_templates, '[]'::jsonb)
                else '[]'::jsonb
            end,
            'active_document_template_ids',
            case when jsonb_typeof(coalesce(p_active_document_template_ids, '{}'::jsonb)) = 'object'
                then coalesce(p_active_document_template_ids, '{}'::jsonb)
                else '{}'::jsonb
            end
        )
    where app_id = v_app_id;

    return jsonb_build_object(
        'applied', true,
        'conflict', false,
        'revision', v_next_revision
    );
end;
$$;

grant execute on function public.erpermitsys_save_snapshot(
    text,
    bigint,
    integer,
    timestamptz,
    text,
    jsonb,
    jsonb,
    jsonb,
    jsonb,
    jsonb,
    jsonb
) to public;

do $$
begin
    if exists (
        select 1 from pg_publication where pubname = 'supabase_realtime'
    ) then
        if not exists (
            select 1
            from pg_publication_rel rel
            join pg_publication pub on pub.oid = rel.prpubid
            join pg_class cls on cls.oid = rel.prrelid
            join pg_namespace ns on ns.oid = cls.relnamespace
            where pub.pubname = 'supabase_realtime'
              and ns.nspname = 'public'
              and cls.relname = 'erpermitsys_contacts'
        ) then
            alter publication supabase_realtime add table public.erpermitsys_contacts;
        end if;

        if not exists (
            select 1
            from pg_publication_rel rel
            join pg_publication pub on pub.oid = rel.prpubid
            join pg_class cls on cls.oid = rel.prrelid
            join pg_namespace ns on ns.oid = cls.relnamespace
            where pub.pubname = 'supabase_realtime'
              and ns.nspname = 'public'
              and cls.relname = 'erpermitsys_jurisdictions'
        ) then
            alter publication supabase_realtime add table public.erpermitsys_jurisdictions;
        end if;

        if not exists (
            select 1
            from pg_publication_rel rel
            join pg_publication pub on pub.oid = rel.prpubid
            join pg_class cls on cls.oid = rel.prrelid
            join pg_namespace ns on ns.oid = cls.relnamespace
            where pub.pubname = 'supabase_realtime'
              and ns.nspname = 'public'
              and cls.relname = 'erpermitsys_properties'
        ) then
            alter publication supabase_realtime add table public.erpermitsys_properties;
        end if;

        if not exists (
            select 1
            from pg_publication_rel rel
            join pg_publication pub on pub.oid = rel.prpubid
            join pg_class cls on cls.oid = rel.prrelid
            join pg_namespace ns on ns.oid = cls.relnamespace
            where pub.pubname = 'supabase_realtime'
              and ns.nspname = 'public'
              and cls.relname = 'erpermitsys_permits'
        ) then
            alter publication supabase_realtime add table public.erpermitsys_permits;
        end if;

        if not exists (
            select 1
            from pg_publication_rel rel
            join pg_publication pub on pub.oid = rel.prpubid
            join pg_class cls on cls.oid = rel.prrelid
            join pg_namespace ns on ns.oid = cls.relnamespace
            where pub.pubname = 'supabase_realtime'
              and ns.nspname = 'public'
              and cls.relname = 'erpermitsys_document_templates'
        ) then
            alter publication supabase_realtime add table public.erpermitsys_document_templates;
        end if;

        if not exists (
            select 1
            from pg_publication_rel rel
            join pg_publication pub on pub.oid = rel.prpubid
            join pg_class cls on cls.oid = rel.prrelid
            join pg_namespace ns on ns.oid = cls.relnamespace
            where pub.pubname = 'supabase_realtime'
              and ns.nspname = 'public'
              and cls.relname = 'erpermitsys_active_document_templates'
        ) then
            alter publication supabase_realtime add table public.erpermitsys_active_document_templates;
        end if;
    end if;
exception
    when insufficient_privilege then
        raise notice 'Skipping supabase_realtime publication update due to permissions.';
end
$$;

do $$
declare
    state_row record;
begin
    select
        app_id,
        payload,
        revision,
        schema_version,
        saved_at_utc,
        updated_by
    into state_row
    from public.erpermitsys_state
    where app_id = 'erpermitsys'
    limit 1;

    if state_row.app_id is null then
        return;
    end if;

    if exists (select 1 from public.erpermitsys_contacts where app_id = state_row.app_id limit 1)
        or exists (select 1 from public.erpermitsys_jurisdictions where app_id = state_row.app_id limit 1)
        or exists (select 1 from public.erpermitsys_properties where app_id = state_row.app_id limit 1)
        or exists (select 1 from public.erpermitsys_permits where app_id = state_row.app_id limit 1)
        or exists (select 1 from public.erpermitsys_document_templates where app_id = state_row.app_id limit 1)
    then
        return;
    end if;

    if jsonb_typeof(state_row.payload) <> 'object' then
        return;
    end if;

    perform public.erpermitsys_save_snapshot(
        state_row.app_id,
        coalesce(state_row.revision, 0),
        coalesce(state_row.schema_version, 3),
        coalesce(state_row.saved_at_utc, timezone('utc', now())),
        coalesce(state_row.updated_by, 'migration'),
        coalesce(state_row.payload -> 'contacts', '[]'::jsonb),
        coalesce(state_row.payload -> 'jurisdictions', '[]'::jsonb),
        coalesce(state_row.payload -> 'properties', '[]'::jsonb),
        coalesce(state_row.payload -> 'permits', '[]'::jsonb),
        coalesce(
            state_row.payload -> 'document_templates',
            state_row.payload -> 'checklist_templates',
            '[]'::jsonb
        ),
        coalesce(
            state_row.payload -> 'active_document_template_ids',
            state_row.payload -> 'default_template_ids',
            '{}'::jsonb
        )
    );
end
$$;

commit;
