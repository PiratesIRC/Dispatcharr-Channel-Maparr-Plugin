# Dispatcharr Plugin Migration Guide: HTTP API → Django ORM

This document describes the architectural changes in Dispatcharr's plugin system and provides a step-by-step guide for migrating plugins from the old HTTP API pattern to the new Django ORM pattern. It is based on the Channel Mapparr plugin migration (v0.6.0a → v0.7.0).

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [What Changed](#what-changed)
3. [Migration Checklist](#migration-checklist)
4. [Step-by-Step Migration](#step-by-step-migration)
5. [ORM Patterns & Recipes](#orm-patterns--recipes)
6. [Common Pitfalls](#common-pitfalls)
7. [File Reference](#file-reference)

---

## Architecture Overview

### Old Architecture (Pre-Migration)

Plugins ran as **external consumers** of the Dispatcharr API:

```
Plugin Process ──HTTP/requests──► Dispatcharr API ──► Django ORM ──► Database
                 (token auth)
```

- Plugins needed `dispatcharr_url`, `dispatcharr_username`, `dispatcharr_password` settings
- Used `import requests` for all data access
- Authenticated via `/api/accounts/token/` to get JWT tokens
- Token caching was managed per-plugin
- All reads were `GET /api/...` calls; all writes were `PATCH`/`POST` calls

### New Architecture (Post-Migration)

Plugins run **inside** the Django backend process with full ORM access:

```
Plugin (in-process) ──► Django ORM ──► Database
```

- No URL/credentials needed — the plugin IS the backend
- Direct `from apps.channels.models import Channel` etc.
- Full access to `transaction.atomic()`, `bulk_update()`, `select_related()`
- WebSocket notifications via `send_websocket_update()`
- Logger and settings provided via `context` dict in `run()`

---

## What Changed

| Aspect | Old Pattern | New Pattern |
|--------|-------------|-------------|
| **Data access** | `requests.get(url, headers=...)` | `Model.objects.filter(...)` |
| **Authentication** | JWT token via `/api/accounts/token/` | None needed |
| **Settings fields** | Included `dispatcharr_url`, username, password | Removed — security anti-pattern |
| **Bulk writes** | `requests.patch("/api/.../edit/bulk/", json=payload)` | `Model.objects.bulk_update(instances, fields)` |
| **Creating records** | `requests.post("/api/.../", json=payload)` | `Model.objects.create(...)` or `get_or_create(...)` |
| **WebSocket refresh** | Raw `channel_layer.group_send()` | `send_websocket_update()` |
| **Module exports** | `fields = Plugin.fields` + multiple `Plugin()` instances | Only the `Plugin` class |
| **`__init__.py`** | `from .plugin import Plugin, fields, actions` | `from .plugin import Plugin` |
| **`plugin.json`** | Had `key`, `module`, `class` fields | Has `fields` and `actions` arrays |

---

## Migration Checklist

Use this checklist when migrating a plugin:

- [ ] **Remove `import requests`** from plugin.py
- [ ] **Remove credential settings fields** (`dispatcharr_url`, `dispatcharr_username`, `dispatcharr_password`)
- [ ] **Remove token caching** (`cached_api_token`, `token_cache_time`, `TOKEN_CACHE_DURATION`)
- [ ] **Remove HTTP helper methods** (`_get_api_token`, `_get_api_data`, `_patch_api_data`, `_post_api_data`)
- [ ] **Add Django ORM imports** (see [Imports section](#1-update-imports))
- [ ] **Create ORM helper methods** to replace HTTP helpers
- [ ] **Update every action method** to use ORM instead of API calls
- [ ] **Update `_trigger_frontend_refresh()`** to use `send_websocket_update()`
- [ ] **Update `__init__.py`** — only export `Plugin` class
- [ ] **Remove module-level exports** (bare `Plugin()` instantiations, `fields = Plugin.fields`, etc.)
- [ ] **Update `plugin.json`** — remove `key`/`module`/`class`, add `fields`/`actions` arrays
- [ ] **Update `_generate_csv_settings_header()`** — remove credential field exclusions
- [ ] **Update `validate_settings_action()`** — remove API connectivity test, add ORM test
- [ ] **Bump version** in both plugin.py and plugin.json
- [ ] **Run `python -m py_compile plugin.py`** to verify syntax

---

## Step-by-Step Migration

### 1. Update Imports

**Remove:**
```python
import requests
```

**Add:**
```python
from apps.channels.models import Channel, ChannelGroup, Logo, Stream, ChannelStream
from django.db import transaction
from core.utils import send_websocket_update
```

Only import models you actually use. Common models:

| Model | Import Path | Used For |
|-------|-------------|----------|
| `Channel` | `apps.channels.models` | Channel CRUD |
| `ChannelGroup` | `apps.channels.models` | Group CRUD |
| `Logo` | `apps.channels.models` | Logo lookups |
| `Stream` | `apps.channels.models` | Stream queries |
| `ChannelStream` | `apps.channels.models` | Linking streams to channels |

**Note:** `from apps.m3u.models import M3UAccount` is available but you likely don't need to import it directly — you can traverse the FK via `Stream.objects.filter(m3u_account__name="...")`.

### 2. Remove Credential Fields

Delete these from your `fields` property/list:

```python
# DELETE these field definitions:
{
    "id": "dispatcharr_url",
    "label": "Dispatcharr URL",
    "type": "string",
    ...
},
{
    "id": "dispatcharr_username",
    "label": "Dispatcharr Admin Username",
    "type": "string",
    ...
},
{
    "id": "dispatcharr_password",
    "label": "Dispatcharr Admin Password",
    "type": "string",
    ...
},
```

### 3. Remove Token Caching

**From class constants, delete:**
```python
TOKEN_CACHE_DURATION = 1800
```

**From `__init__`, delete:**
```python
self.cached_api_token = None
self.token_cache_time = None
self.token_cache_duration = self.TOKEN_CACHE_DURATION
```

### 4. Remove HTTP Helper Methods

Delete these methods entirely:
- `_get_api_token(self, settings, logger)` — JWT authentication
- `_get_api_data(self, endpoint, token, settings, logger, paginated=False)` — GET requests
- `_patch_api_data(self, endpoint, token, payload, settings, logger)` — PATCH requests
- `_post_api_data(self, endpoint, token, payload, settings, logger)` — POST requests

### 5. Add ORM Helper Methods

Replace the removed HTTP methods with ORM equivalents:

```python
def _get_all_groups(self, logger):
    """Fetch all channel groups via Django ORM."""
    return list(ChannelGroup.objects.all().values('id', 'name'))

def _get_all_channels(self, logger, group_ids=None):
    """Fetch channels via Django ORM, optionally filtered by group IDs."""
    qs = Channel.objects.select_related('channel_group', 'logo').all()
    if group_ids:
        qs = qs.filter(channel_group_id__in=group_ids)
    return list(qs.values('id', 'name', 'channel_number', 'channel_group_id', 'logo_id'))

def _bulk_update_channels(self, updates, fields, logger):
    """Bulk update Channel instances.

    Args:
        updates: list of dicts with 'id' and fields to update
        fields: list of field names to update
    """
    if not updates:
        return
    channel_ids = [u['id'] for u in updates]
    channels = {ch.id: ch for ch in Channel.objects.filter(id__in=channel_ids)}
    to_update = []
    for u in updates:
        ch = channels.get(u['id'])
        if ch:
            for field in fields:
                if field in u:
                    setattr(ch, field, u[field])
            to_update.append(ch)
    if to_update:
        with transaction.atomic():
            Channel.objects.bulk_update(to_update, fields)
        logger.info(f"Bulk updated {len(to_update)} channels (fields: {', '.join(fields)})")

def _get_or_create_group(self, name, logger):
    """Get or create a channel group by name."""
    group, created = ChannelGroup.objects.get_or_create(name=name)
    if created:
        logger.info(f"Created new group '{name}' (ID: {group.id})")
    return group

def _get_all_logos(self, logger):
    """Fetch all logos via Django ORM."""
    return list(Logo.objects.all().values('id', 'name'))
```

**Key design decision:** The ORM helpers return dicts (via `.values()`) to minimize changes in downstream processing logic that uses dict-style access (`channel.get('name')`). This avoids rewriting matching/processing code.

### 6. Update Action Methods

Every action method follows the same transformation pattern:

**Before (HTTP):**
```python
def some_action(self, settings, logger):
    # Get token
    token, error = self._get_api_token(settings, logger)
    if error:
        return {"status": "error", "message": error}

    # Read data
    groups = self._get_api_data("/api/channels/groups/", token, settings, logger)
    channels = self._get_api_data("/api/channels/channels/", token, settings, logger)

    # Write data
    payload = [{'id': ch_id, 'name': new_name} for ...]
    self._patch_api_data("/api/channels/channels/edit/bulk/", token, payload, settings, logger)
```

**After (ORM):**
```python
def some_action(self, settings, logger):
    # Read data — no token needed
    groups = self._get_all_groups(logger)
    channels = self._get_all_channels(logger)

    # Write data
    updates = [{'id': ch_id, 'name': new_name} for ...]
    self._bulk_update_channels(updates, ['name'], logger)
```

### 7. Update WebSocket Notifications

**Before:**
```python
def _trigger_frontend_refresh(self, settings, logger):
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    channel_layer = get_channel_layer()
    if channel_layer:
        async_to_sync(channel_layer.group_send)(
            "dispatcharr_updates",
            {"type": "channels.updated", "message": "..."}
        )
```

**After:**
```python
def _trigger_frontend_refresh(self, settings, logger):
    try:
        send_websocket_update('updates', 'update', {
            "type": "plugin",
            "plugin": self.name,
            "message": "Channels updated"
        })
        logger.info(f"Frontend refresh triggered via WebSocket")
        return True
    except Exception as e:
        logger.warning(f"Could not trigger frontend refresh: {e}")
    return False
```

### 8. Update validate_settings_action

Remove all URL/credential validation and API connectivity tests. Replace with an ORM connectivity test:

```python
def validate_settings_action(self, settings, logger):
    validation_results = []
    error_count = 0

    # Test database connectivity directly
    try:
        channel_count = Channel.objects.count()
        group_count = ChannelGroup.objects.count()
        logo_count = Logo.objects.count()
        stream_count = Stream.objects.count()
        validation_results.append(
            f"✅ DB OK ({channel_count} channels, {group_count} groups, "
            f"{logo_count} logos, {stream_count} streams)"
        )
    except Exception as e:
        validation_results.append(f"❌ DB error: {str(e)[:50]}")
        error_count += 1

    # ... rest of validation (channel databases, filters, etc.)
```

### 9. Update Module Exports

**`__init__.py` — Before:**
```python
from .plugin import Plugin, fields, actions
```

**`__init__.py` — After:**
```python
from .plugin import Plugin
```

**Bottom of `plugin.py` — Delete entirely:**
```python
# DELETE all of these:
fields = Plugin.fields
actions = Plugin.actions
plugin = Plugin()
plugin_instance = Plugin()
channel_mapparr = Plugin()
CHANNEL_MAPPARR = Plugin()
```

The new plugin loader only looks for the `Plugin` class. These extra instantiations waste resources and `Plugin.fields` is a `@property` that can't be accessed on the class itself.

### 10. Update plugin.json

**Before:**
```json
{
  "name": "My Plugin",
  "key": "my_plugin",
  "module": "my_plugin.plugin",
  "class": "Plugin",
  "description": "...",
  "actions": ["run"]
}
```

**After:**
```json
{
  "name": "My Plugin",
  "version": "1.0.0",
  "description": "...",
  "author": "community",
  "help_url": "https://github.com/...",
  "fields": [
    {"id": "setting_id", "label": "Setting Label", "type": "string", "default": ""}
  ],
  "actions": [
    {"id": "action_id", "label": "Action Label", "description": "What it does"}
  ]
}
```

Remove `key`, `module`, `class`. Add `fields` and `actions` arrays that mirror the Plugin class definitions.

### 11. Simplify Logging

**Before:**
```python
LOGGER = logging.getLogger("plugins.my_plugin")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
```

**After:**
```python
LOGGER = logging.getLogger("plugins.my_plugin")
```

The new system provides a pre-configured logger via `context.get("logger")`. The module-level logger is kept as a fallback.

---

## ORM Patterns & Recipes

### Reading Data

```python
# Get all groups as dicts
groups = list(ChannelGroup.objects.all().values('id', 'name'))

# Get channels filtered by group, with related objects pre-loaded
channels = list(
    Channel.objects.select_related('channel_group', 'logo')
    .filter(channel_group_id__in=group_ids)
    .values('id', 'name', 'channel_number', 'channel_group_id', 'logo_id')
)

# Get logos
logos = list(Logo.objects.all().values('id', 'name'))

# Get streams from specific M3U source
streams = Stream.objects.select_related('m3u_account').filter(
    m3u_account__name="MySource"
)

# Count records
total = Channel.objects.count()

# Aggregate
from django.db.models import Max
result = Channel.objects.aggregate(max_num=Max('channel_number'))
```

### Writing Data — Bulk Update

```python
# Prepare updates as dicts
updates = [
    {'id': 1, 'name': 'New Name 1'},
    {'id': 2, 'name': 'New Name 2'},
]

# Fetch actual model instances
channel_ids = [u['id'] for u in updates]
channels = {ch.id: ch for ch in Channel.objects.filter(id__in=channel_ids)}

# Apply changes to instances
to_update = []
for u in updates:
    ch = channels.get(u['id'])
    if ch:
        ch.name = u['name']
        to_update.append(ch)

# Bulk update in a transaction
with transaction.atomic():
    Channel.objects.bulk_update(to_update, ['name'])
```

### Writing Data — Create with FK Link

```python
# Create a channel and link a stream to it
with transaction.atomic():
    channel = Channel.objects.create(
        name="Channel Name",
        channel_number=next_num,
        channel_group_id=group_id,
    )
    ChannelStream.objects.create(
        channel=channel,
        stream=stream_obj,
        order=0,
    )
```

### Writing Data — Get or Create

```python
group, created = ChannelGroup.objects.get_or_create(name="Sports")
# group.id is available immediately
# created is True if the group was just created
```

---

## Common Pitfalls

### 1. `__init__.py` Must Match Exports

If your old `__init__.py` imports `fields` and `actions`:
```python
from .plugin import Plugin, fields, actions  # OLD — will ImportError
```
You must update it to:
```python
from .plugin import Plugin  # NEW
```
The plugin loader catches the ImportError and reports "missing Plugin class" — a misleading error message.

### 2. Don't Assume Model Field Names

Before using `.values('field_name')`, verify the field exists on the model. If a field doesn't exist, Django raises `FieldError` at query time. Common issues:
- `Logo` may not have a `url` field — only query fields you actually use
- `Stream` may not have a `channel_group` FK — use `getattr(stream, 'channel_group_id', None)` defensively
- `Stream` may have a `group_title` text field instead of a FK — check both

### 3. `@property` Fields Can't Be Accessed on the Class

```python
# This FAILS if fields is a @property:
fields = Plugin.fields  # TypeError: 'property' object is not iterable

# This works:
plugin = Plugin()
fields = plugin.fields
```

This is why the module-level `fields = Plugin.fields` export was removed.

### 4. FK Fields in `.values()` and `bulk_update`

Django auto-appends `_id` to FK field names in `.values()`:
```python
# If Channel has: logo = ForeignKey(Logo)
# Then .values() returns: {'logo_id': 5}  (not 'logo')
# And bulk_update expects: bulk_update(instances, ['logo_id'])
# And setattr works: setattr(ch, 'logo_id', 5)
```

### 5. Token Parameter Removal — Check All Callers

When removing the `token` parameter from helper methods, grep for ALL call sites. Methods like `_fetch_streams_from_m3u_sources`, `_ensure_category_groups_exist`, `_import_matched_streams`, and `_get_next_channel_number` previously took `token` — every caller must be updated.

### 6. `plugin.json` Key Field

The old `plugin.json` had a `key` field (e.g., `"key": "channel_mapparr"`) used for plugin identification. The new system derives the plugin key from the directory name. Make sure your plugin directory name matches what the system expects.

---

## File Reference

### Files Modified in Channel Mapparr Migration

| File | Changes |
|------|---------|
| `plugin.py` | Major rewrite — ORM imports, removed HTTP methods, updated all 8 actions |
| `plugin.json` | New format — removed `key`/`module`/`class`, added `fields`/`actions` arrays |
| `__init__.py` | Changed from `from .plugin import Plugin, fields, actions` to `from .plugin import Plugin` |

### Files NOT Modified

| File | Reason |
|------|--------|
| `fuzzy_matcher.py` | Pure matching logic — no API dependency |
| `*_channels.json` | Static data files — no changes needed |
| `readme.txt` | Documentation — update separately if desired |

---

## Quick Reference: API Endpoint → ORM Mapping

| Old API Call | New ORM Equivalent |
|-------------|-------------------|
| `GET /api/channels/groups/` | `ChannelGroup.objects.all().values('id', 'name')` |
| `GET /api/channels/channels/` | `Channel.objects.select_related(...).values(...)` |
| `GET /api/channels/logos/` | `Logo.objects.all().values('id', 'name')` |
| `GET /api/channels/streams/` | `Stream.objects.select_related('m3u_account').all()` |
| `GET /api/channels/streams/?m3u_account_name=X` | `Stream.objects.filter(m3u_account__name="X")` |
| `PATCH /api/channels/channels/edit/bulk/` | `Channel.objects.bulk_update(instances, fields)` |
| `POST /api/channels/groups/` | `ChannelGroup.objects.get_or_create(name=...)` |
| `POST /api/channels/channels/` | `Channel.objects.create(...)` |
| `POST /api/channels/channels/from-stream/` | `Channel.objects.create(...)` + `ChannelStream.objects.create(...)` |
| `GET /api/accounts/token/` | *(removed — not needed)* |
