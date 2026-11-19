# Project.client_id — two-release plan

## Release 1 (this branch — code prepared, column kept)

**Goal:** Stop treating `projects.client_id` as the source of truth for membership. Keep the column **synced** from `project_clients.is_primary=true` so old readers still work.

### Done / in progress

- `set_project_clients` / `remove_client_from_project` / `create_project` write both M2M and `projects.client_id`.
- `resolve_primary_client_id(db, project_id)` — authoritative read from M2M.
- `sync_project_client_id_column` — mirror helper.
- `project_has_client` prefers loaded `clients` M2M.
- Dashboard / visibility already use `project_clients` (`project_visibility.py`).
- Create paths in leads/engagements call `set_project_clients`.

### Remaining READ sites to flip before Release 2

See FIX_REPORT Step 7b table (from full grep). Highest priority:

| File | Change |
|------|--------|
| `routers/projects.py` sanitize / list | Prefer `resolve_primary_client_id` / M2M `is_primary` |
| `services/maintenance_billing.py` | Pass primary from `resolve_primary_client_id` |
| Scripts / cleanup | Use M2M |
| UI `client_id` field | Keep as “primary client id” API field (not DB-coupled) |

### Do not drop the column in Release 1.

## Release 2 (after one clean production release)

1. Confirm zero code paths read `Project.client_id` / `projects.client_id` (re-grep).
2. Alembic: drop FK/index/column `projects.client_id`.
3. Remove sync writes from `set_project_clients` / remove / create.
4. API may still expose `client_id` as alias of primary from M2M.

**Draft migration filename (DO NOT APPLY):** `alembic/versions/_DRAFT_024_drop_projects_client_id.py` — create only when Release 2 starts.
