# CogHUD Rewrite Plugins

Each plugin lives in its own folder:

- `manifest.json` (required)
- HTML entry file referenced by `entry`
- optional Python backend file referenced by `backend`
- optional `settings.json` defaults (merged into plugin runtime settings)

## Manifest schema (scaffold v1)

```json
{
  "id": "example-plugin",
  "kind": "html-background",
  "name": "Example Plugin",
  "version": "0.1.0",
  "description": "Short description",
  "entry": "index.html",
  "backend": "backend.py",
  "enabled": true
}
```

Notes:

- `id` must match `^[a-z0-9][a-z0-9_-]{0,63}$`
- `kind` is required (recommended: `html-background` or `feature`)
- `entry` and `backend` are relative paths inside the plugin folder
- `entry` is required for `html-background` plugins
- `backend` is optional for HTML-only plugins
- `settings.json` is optional and can hold default backend settings
- `enabled: false` keeps a plugin installed but not discoverable
- `tags` is optional for capability labels in picker UIs (for example: `voice`, `ai`)

## Kind activation policy

- `html-background`: single active at a time
- `feature`: multi-active
- other kinds: multi-active by default

The manager policy is extensible, so future kinds can define their own activation mode.

## Optional backend contract

A backend module can expose either:

1. `create_backend(context)` function, or
2. `PluginBackend` class (constructed with context if accepted)

Optional backend methods:

- `start(context)`
- `stop()`
- `handle_command(command, payload)`
- `snapshot_state()`

`context.emit_event(event_type, payload)` can be used to publish plugin events.

## Plugin settings

Backend contexts now expose:

- `context.settings_path`: persisted settings file path under `rewrite/config/plugins/<plugin-id>/settings.json`
- `context.plugin_settings_path`: optional plugin-local defaults file path (`rewrite/plugins/<plugin-id>/settings.json`)
- `context.load_settings(defaults=None)`: merges `defaults` + plugin-local defaults + persisted settings
- `context.save_settings(mapping)`: saves persisted settings for that plugin
