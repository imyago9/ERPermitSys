begin;

create or replace function public.erpermitsys_payload_apply_collection(
    p_base jsonb,
    p_upserts jsonb,
    p_deletes jsonb,
    p_id_key text
) returns jsonb
language sql
as $$
    with base_rows as (
        select item as row
        from jsonb_array_elements(
            case
                when jsonb_typeof(coalesce(p_base, '[]'::jsonb)) = 'array'
                    then coalesce(p_base, '[]'::jsonb)
                else '[]'::jsonb
            end
        ) item
        where jsonb_typeof(item) = 'object'
    ),
    delete_ids as (
        select trim(value) as row_id
        from jsonb_array_elements_text(
            case
                when jsonb_typeof(coalesce(p_deletes, '[]'::jsonb)) = 'array'
                    then coalesce(p_deletes, '[]'::jsonb)
                else '[]'::jsonb
            end
        ) value
        where trim(value) <> ''
    ),
    upsert_rows as (
        select item as row,
               trim(coalesce(item ->> p_id_key, '')) as row_id
        from jsonb_array_elements(
            case
                when jsonb_typeof(coalesce(p_upserts, '[]'::jsonb)) = 'array'
                    then coalesce(p_upserts, '[]'::jsonb)
                else '[]'::jsonb
            end
        ) item
        where jsonb_typeof(item) = 'object'
          and trim(coalesce(item ->> p_id_key, '')) <> ''
    ),
    kept_base as (
        select b.row,
               trim(coalesce(b.row ->> p_id_key, '')) as row_id
        from base_rows b
        where trim(coalesce(b.row ->> p_id_key, '')) <> ''
          and not exists (
              select 1 from delete_ids d where d.row_id = trim(coalesce(b.row ->> p_id_key, ''))
          )
          and not exists (
              select 1 from upsert_rows u where u.row_id = trim(coalesce(b.row ->> p_id_key, ''))
          )
    ),
    merged as (
        select row, row_id from kept_base
        union all
        select row, row_id from upsert_rows
    )
    select coalesce(jsonb_agg(row order by row_id), '[]'::jsonb)
    from merged;
$$;

create or replace function public.erpermitsys_payload_apply_template_map(
    p_base jsonb,
    p_upserts jsonb,
    p_deletes jsonb
) returns jsonb
language plpgsql
as $$
declare
    v_result jsonb := '{}'::jsonb;
    v_pair jsonb;
    v_key text;
    v_template_id text;
begin
    if jsonb_typeof(coalesce(p_base, '{}'::jsonb)) = 'object' then
        v_result := coalesce(p_base, '{}'::jsonb);
    end if;

    for v_key in
        select trim(value)
        from jsonb_array_elements_text(
            case
                when jsonb_typeof(coalesce(p_deletes, '[]'::jsonb)) = 'array'
                    then coalesce(p_deletes, '[]'::jsonb)
                else '[]'::jsonb
            end
        ) value
    loop
        if v_key <> '' then
            v_result := v_result - v_key;
        end if;
    end loop;

    for v_pair in
        select item
        from jsonb_array_elements(
            case
                when jsonb_typeof(coalesce(p_upserts, '[]'::jsonb)) = 'array'
                    then coalesce(p_upserts, '[]'::jsonb)
                else '[]'::jsonb
            end
        ) item
        where jsonb_typeof(item) = 'object'
    loop
        v_key := trim(coalesce(v_pair ->> 'permit_type', ''));
        v_template_id := trim(coalesce(v_pair ->> 'template_id', ''));
        if v_key = '' or v_template_id = '' then
            continue;
        end if;
        v_result := jsonb_set(v_result, array[v_key], to_jsonb(v_template_id), true);
    end loop;

    return coalesce(v_result, '{}'::jsonb);
end;
$$;

create or replace function public.erpermitsys_prune_tombstones(
    p_app_id text,
    p_delete_before timestamptz
) returns void
language plpgsql
as $$
declare
    v_app_id text := coalesce(nullif(trim(p_app_id), ''), 'erpermitsys');
    v_cutoff timestamptz := coalesce(p_delete_before, timezone('utc', now()) - interval '30 days');
begin
    delete from public.erpermitsys_contacts
    where app_id = v_app_id and deleted_at is not null and deleted_at < v_cutoff;

    delete from public.erpermitsys_jurisdictions
    where app_id = v_app_id and deleted_at is not null and deleted_at < v_cutoff;

    delete from public.erpermitsys_properties
    where app_id = v_app_id and deleted_at is not null and deleted_at < v_cutoff;

    delete from public.erpermitsys_permits
    where app_id = v_app_id and deleted_at is not null and deleted_at < v_cutoff;

    delete from public.erpermitsys_document_templates
    where app_id = v_app_id and deleted_at is not null and deleted_at < v_cutoff;

    delete from public.erpermitsys_active_document_templates
    where app_id = v_app_id and deleted_at is not null and deleted_at < v_cutoff;
end;
$$;

create or replace function public.erpermitsys_apply_changes(
    p_app_id text,
    p_expected_revision bigint,
    p_schema_version integer,
    p_saved_at_utc timestamptz,
    p_updated_by text,
    p_contacts_upserts jsonb,
    p_contacts_deletes jsonb,
    p_jurisdictions_upserts jsonb,
    p_jurisdictions_deletes jsonb,
    p_properties_upserts jsonb,
    p_properties_deletes jsonb,
    p_permits_upserts jsonb,
    p_permits_deletes jsonb,
    p_document_templates_upserts jsonb,
    p_document_templates_deletes jsonb,
    p_active_document_template_ids_upserts jsonb,
    p_active_document_template_ids_deletes jsonb
) returns jsonb
language plpgsql
as $$
declare
    v_app_id text := coalesce(nullif(trim(p_app_id), ''), 'erpermitsys');
    v_expected_revision bigint := greatest(0, coalesce(p_expected_revision, 0));
    v_current_revision bigint := 0;
    v_next_revision bigint := 0;
    v_payload jsonb := '{}'::jsonb;
    v_saved_at timestamptz := coalesce(p_saved_at_utc, timezone('utc', now()));
    v_updated_by text := coalesce(trim(p_updated_by), '');
    v_now timestamptz := timezone('utc', now());
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
        v_now,
        v_updated_by,
        0,
        '{}'::jsonb
    )
    on conflict (app_id) do nothing;

    select
        coalesce(revision, 0),
        case
            when jsonb_typeof(coalesce(payload, '{}'::jsonb)) = 'object'
                then coalesce(payload, '{}'::jsonb)
            else '{}'::jsonb
        end
    into v_current_revision, v_payload
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

    insert into public.erpermitsys_contacts (
        app_id,
        contact_id,
        name,
        numbers,
        emails,
        roles,
        contact_methods,
        list_color,
        updated_at,
        updated_by,
        deleted_at
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
        v_now,
        v_updated_by,
        null
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_contacts_upserts, '[]'::jsonb)) = 'array'
            then coalesce(p_contacts_upserts, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(coalesce(item->>'contact_id', '')) <> ''
    on conflict (app_id, contact_id) do update
    set
        name = excluded.name,
        numbers = excluded.numbers,
        emails = excluded.emails,
        roles = excluded.roles,
        contact_methods = excluded.contact_methods,
        list_color = excluded.list_color,
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = null;

    insert into public.erpermitsys_contacts (
        app_id,
        contact_id,
        updated_at,
        updated_by,
        deleted_at
    )
    select
        v_app_id,
        trim(item),
        v_now,
        v_updated_by,
        v_now
    from jsonb_array_elements_text(
        case when jsonb_typeof(coalesce(p_contacts_deletes, '[]'::jsonb)) = 'array'
            then coalesce(p_contacts_deletes, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(item) <> ''
    on conflict (app_id, contact_id) do update
    set
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = excluded.deleted_at;

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
        updated_at,
        updated_by,
        deleted_at
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
        v_now,
        v_updated_by,
        null
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_jurisdictions_upserts, '[]'::jsonb)) = 'array'
            then coalesce(p_jurisdictions_upserts, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(coalesce(item->>'jurisdiction_id', '')) <> ''
    on conflict (app_id, jurisdiction_id) do update
    set
        name = excluded.name,
        jurisdiction_type = excluded.jurisdiction_type,
        parent_county = excluded.parent_county,
        portal_urls = excluded.portal_urls,
        contact_ids = excluded.contact_ids,
        portal_vendor = excluded.portal_vendor,
        notes = excluded.notes,
        list_color = excluded.list_color,
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = null;

    insert into public.erpermitsys_jurisdictions (
        app_id,
        jurisdiction_id,
        updated_at,
        updated_by,
        deleted_at
    )
    select
        v_app_id,
        trim(item),
        v_now,
        v_updated_by,
        v_now
    from jsonb_array_elements_text(
        case when jsonb_typeof(coalesce(p_jurisdictions_deletes, '[]'::jsonb)) = 'array'
            then coalesce(p_jurisdictions_deletes, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(item) <> ''
    on conflict (app_id, jurisdiction_id) do update
    set
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = excluded.deleted_at;

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
        updated_at,
        updated_by,
        deleted_at
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
        v_now,
        v_updated_by,
        null
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_properties_upserts, '[]'::jsonb)) = 'array'
            then coalesce(p_properties_upserts, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(coalesce(item->>'property_id', '')) <> ''
    on conflict (app_id, property_id) do update
    set
        display_address = excluded.display_address,
        parcel_id = excluded.parcel_id,
        parcel_id_norm = excluded.parcel_id_norm,
        jurisdiction_id = excluded.jurisdiction_id,
        contact_ids = excluded.contact_ids,
        list_color = excluded.list_color,
        tags = excluded.tags,
        notes = excluded.notes,
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = null;

    insert into public.erpermitsys_properties (
        app_id,
        property_id,
        updated_at,
        updated_by,
        deleted_at
    )
    select
        v_app_id,
        trim(item),
        v_now,
        v_updated_by,
        v_now
    from jsonb_array_elements_text(
        case when jsonb_typeof(coalesce(p_properties_deletes, '[]'::jsonb)) = 'array'
            then coalesce(p_properties_deletes, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(item) <> ''
    on conflict (app_id, property_id) do update
    set
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = excluded.deleted_at;

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
        updated_at,
        updated_by,
        deleted_at
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
        v_now,
        v_updated_by,
        null
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_permits_upserts, '[]'::jsonb)) = 'array'
            then coalesce(p_permits_upserts, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(coalesce(item->>'permit_id', '')) <> ''
    on conflict (app_id, permit_id) do update
    set
        property_id = excluded.property_id,
        permit_type = excluded.permit_type,
        permit_number = excluded.permit_number,
        status = excluded.status,
        next_action_text = excluded.next_action_text,
        next_action_due = excluded.next_action_due,
        request_date = excluded.request_date,
        application_date = excluded.application_date,
        issued_date = excluded.issued_date,
        final_date = excluded.final_date,
        completion_date = excluded.completion_date,
        parties = excluded.parties,
        events = excluded.events,
        document_slots = excluded.document_slots,
        document_folders = excluded.document_folders,
        documents = excluded.documents,
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = null;

    insert into public.erpermitsys_permits (
        app_id,
        permit_id,
        updated_at,
        updated_by,
        deleted_at
    )
    select
        v_app_id,
        trim(item),
        v_now,
        v_updated_by,
        v_now
    from jsonb_array_elements_text(
        case when jsonb_typeof(coalesce(p_permits_deletes, '[]'::jsonb)) = 'array'
            then coalesce(p_permits_deletes, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(item) <> ''
    on conflict (app_id, permit_id) do update
    set
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = excluded.deleted_at;

    insert into public.erpermitsys_document_templates (
        app_id,
        template_id,
        name,
        permit_type,
        slots,
        notes,
        updated_at,
        updated_by,
        deleted_at
    )
    select
        v_app_id,
        trim(coalesce(item->>'template_id', '')),
        trim(coalesce(item->>'name', '')),
        trim(coalesce(item->>'permit_type', 'building')),
        case when jsonb_typeof(item->'slots') = 'array' then item->'slots' else '[]'::jsonb end,
        trim(coalesce(item->>'notes', '')),
        v_now,
        v_updated_by,
        null
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_document_templates_upserts, '[]'::jsonb)) = 'array'
            then coalesce(p_document_templates_upserts, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(coalesce(item->>'template_id', '')) <> ''
    on conflict (app_id, template_id) do update
    set
        name = excluded.name,
        permit_type = excluded.permit_type,
        slots = excluded.slots,
        notes = excluded.notes,
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = null;

    insert into public.erpermitsys_document_templates (
        app_id,
        template_id,
        updated_at,
        updated_by,
        deleted_at
    )
    select
        v_app_id,
        trim(item),
        v_now,
        v_updated_by,
        v_now
    from jsonb_array_elements_text(
        case when jsonb_typeof(coalesce(p_document_templates_deletes, '[]'::jsonb)) = 'array'
            then coalesce(p_document_templates_deletes, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(item) <> ''
    on conflict (app_id, template_id) do update
    set
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = excluded.deleted_at;

    insert into public.erpermitsys_active_document_templates (
        app_id,
        permit_type,
        template_id,
        updated_at,
        updated_by,
        deleted_at
    )
    select
        v_app_id,
        trim(coalesce(item->>'permit_type', '')),
        trim(coalesce(item->>'template_id', '')),
        v_now,
        v_updated_by,
        null
    from jsonb_array_elements(
        case when jsonb_typeof(coalesce(p_active_document_template_ids_upserts, '[]'::jsonb)) = 'array'
            then coalesce(p_active_document_template_ids_upserts, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(coalesce(item->>'permit_type', '')) <> ''
      and trim(coalesce(item->>'template_id', '')) <> ''
    on conflict (app_id, permit_type) do update
    set
        template_id = excluded.template_id,
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = null;

    insert into public.erpermitsys_active_document_templates (
        app_id,
        permit_type,
        template_id,
        updated_at,
        updated_by,
        deleted_at
    )
    select
        v_app_id,
        trim(item),
        '',
        v_now,
        v_updated_by,
        v_now
    from jsonb_array_elements_text(
        case when jsonb_typeof(coalesce(p_active_document_template_ids_deletes, '[]'::jsonb)) = 'array'
            then coalesce(p_active_document_template_ids_deletes, '[]'::jsonb)
            else '[]'::jsonb
        end
    ) item
    where trim(item) <> ''
    on conflict (app_id, permit_type) do update
    set
        updated_at = excluded.updated_at,
        updated_by = excluded.updated_by,
        deleted_at = excluded.deleted_at;

    v_next_revision := v_current_revision + 1;
    if mod(v_next_revision, 25) = 0 then
        perform public.erpermitsys_prune_tombstones(v_app_id, v_now - interval '30 days');
    end if;

    update public.erpermitsys_state
    set
        schema_version = coalesce(p_schema_version, schema_version, 3),
        backend = 'supabase',
        saved_at_utc = v_saved_at,
        updated_at = v_now,
        updated_by = v_updated_by,
        revision = v_next_revision,
        payload = jsonb_build_object(
            'contacts',
            public.erpermitsys_payload_apply_collection(
                v_payload -> 'contacts',
                p_contacts_upserts,
                p_contacts_deletes,
                'contact_id'
            ),
            'jurisdictions',
            public.erpermitsys_payload_apply_collection(
                v_payload -> 'jurisdictions',
                p_jurisdictions_upserts,
                p_jurisdictions_deletes,
                'jurisdiction_id'
            ),
            'properties',
            public.erpermitsys_payload_apply_collection(
                v_payload -> 'properties',
                p_properties_upserts,
                p_properties_deletes,
                'property_id'
            ),
            'permits',
            public.erpermitsys_payload_apply_collection(
                v_payload -> 'permits',
                p_permits_upserts,
                p_permits_deletes,
                'permit_id'
            ),
            'document_templates',
            public.erpermitsys_payload_apply_collection(
                v_payload -> 'document_templates',
                p_document_templates_upserts,
                p_document_templates_deletes,
                'template_id'
            ),
            'active_document_template_ids',
            public.erpermitsys_payload_apply_template_map(
                v_payload -> 'active_document_template_ids',
                p_active_document_template_ids_upserts,
                p_active_document_template_ids_deletes
            )
        )
    where app_id = v_app_id;

    return jsonb_build_object(
        'applied', true,
        'conflict', false,
        'revision', v_next_revision
    );
end;
$$;

grant execute on function public.erpermitsys_payload_apply_collection(jsonb, jsonb, jsonb, text) to public;
grant execute on function public.erpermitsys_payload_apply_template_map(jsonb, jsonb, jsonb) to public;
grant execute on function public.erpermitsys_prune_tombstones(text, timestamptz) to public;

grant execute on function public.erpermitsys_apply_changes(
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
    jsonb,
    jsonb,
    jsonb,
    jsonb,
    jsonb,
    jsonb,
    jsonb
) to public;

commit;
