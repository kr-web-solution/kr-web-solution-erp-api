# Deploy environment & operator commands

Names only — never commit real secrets. Assumes Dokploy (or similar) builds the API image from GitHub and runs `uvicorn main:app` (see `Dockerfile`). **Migrations and seed are not part of container start.**

## Required env var names

### API (Dokploy API app)

| Name | Notes |
|------|--------|
| `DATABASE_URL` | Async Postgres URL (`postgresql+asyncpg://…`) |
| `SECRET_KEY` | JWT signing; **must not** be a README/example placeholder — app refuses known insecure defaults in production. Generate: `openssl rand -hex 32` |
| `ENCRYPTION_KEY` | Fernet key for SMTP secrets. Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### UI (Dokploy UI app)

| Name | Notes |
|------|--------|
| `API_INTERNAL_URL` | **Required in production.** Docker DNS name of the API service + port `8000`, e.g. `http://<dokploy-api-service-name>:8000`. Never `127.0.0.1` / `localhost` (that is inside the UI container). |
| `NEXT_PUBLIC_API_URL` | Leave empty so the browser calls same-origin `/api` (Next proxies). |

### Shared / API continued

| Name | Notes |
|------|--------|
| `SECRET_KEY` / `ENCRYPTION_KEY` | (see API table above) |
| `ENVIRONMENT` | `production` for live; `development` / `local` / `test` for non-prod |
| `PRODUCTION` | `true` on live |
| `FRONTEND_URL` | UI origin |
| `BACKEND_CORS_ORIGINS` | JSON list of allowed origins. In production, **only** this allowlist is used (no automatic localhost regex). Non-production also allows `http(s)://localhost` / `127.0.0.1` on any port via regex. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT / cookie lifetime (default `1440` = 24h) |
| `COOKIE_NAME` | Auth cookie name (default `kr_access_token`) |
| `ENABLE_SCHEDULER` | Optional `true`/`false`; default **on only when** `ENVIRONMENT=production` |
| `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` / `SUPER_ADMIN_NAME` | First-boot admin via CLI only |
| `SMTP_*` / `BILLING_*` | Optional outbound mail |
| `STORAGE_BACKEND` / `S3_*` | Object storage |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth (IDs public; secrets private) |
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_TENANT_ID` / `MICROSOFT_CLIENT_SECRET` | OAuth |

## What runs on app start

1. `init_db` (create missing tables if any — **not** a substitute for Alembic)
2. One log line: `Scheduler startup: RUNNING|NOT RUNNING — …` (`ENVIRONMENT` + `ENABLE_SCHEDULER`)
3. Scheduler start (or skip)

**Does not run:** `seed_database_async`, demo user soft-delete, Alembic migrations.

## Deploy order (production)

1. Set env vars in the platform (including `ENVIRONMENT=production`, `PRODUCTION=true`).
2. Deploy / restart the API container (uvicorn only).
3. **One-off migrate** (platform job or SSH one-shot), with explicit confirmation against the real DB:

```bash
CONFIRM_REMOTE_DB=YES CONFIRM_DB_NAME=<exact-db-name> \
  python -m app.cli migrate --i-know-this-is-remote upgrade head
```

(`db_target_guard` also accepts the flag on `sys.argv`; Alembic is invoked under the same process after the guard.)

4. **One-off bootstrap admin** (only if no admin exists yet):

```bash
CONFIRM_REMOTE_DB=YES CONFIRM_DB_NAME=<exact-db-name> \
  python -m app.cli bootstrap-admin --i-know-this-is-remote
```

5. **Do not** run `python -m app.cli seed` against production unless you intentionally want local/dev-style settings/templates/demo soft-delete — and then only with the same confirmation flags.

## Local / dev commands

```bash
# Schema
python -m app.cli migrate upgrade head

# Settings + email templates + soft-delete legacy demo logins + bootstrap if needed
python -m app.cli seed

# Admin only (idempotent; never modifies other users)
python -m app.cli bootstrap-admin
```

Local DBs must be localhost (or `.local`) **and** either a `*test*` / `*pytest*` name or listed in `ALLOWED_LOCAL_DB_NAMES`.

## Scheduler refuse / off conditions

| Condition | Result |
|-----------|--------|
| `ENVIRONMENT=test` | Off |
| `ENABLE_SCHEDULER=false` | Off |
| `ENABLE_SCHEDULER` unset and `ENVIRONMENT` ≠ `production` | Off |
| Non-production + non-local `DATABASE_URL` host | Off (even if `ENABLE_SCHEDULER=true`) |
| `ENVIRONMENT=production` (or explicit `ENABLE_SCHEDULER=true` on local) | On |

## Auth: logout vs JWT validity

Login issues a signed JWT (`ACCESS_TOKEN_EXPIRE_MINUTES`, default 1440 minutes) and sets it as an httpOnly cookie (`COOKIE_NAME`). Clients may also send the same value as `Authorization: Bearer <token>`.

| Action | Effect |
|--------|--------|
| `POST /api/v1/auth/logout` | Deletes the auth **cookie** on the browser (same path / SameSite / Secure flags as login). |
| Copied Bearer JWT | **Not** invalidated. Anyone who already copied the token can keep using it until it expires. |
| Token denylist / rotation | **Not** implemented. Server-side revocation would be a separate architectural decision (e.g. Redis denylist, refresh-token rotation). |

**Why this is acceptable for Stage 1:** HttpOnly cookies prevent JavaScript from directly reading the authentication cookie, reducing the risk of token exfiltration. However, HttpOnly does not prevent malicious scripts executing through an XSS vulnerability from making authenticated requests using the browser's cookie. Token expiry limits the lifetime of a copied JWT, but logout does not revoke previously copied Bearer tokens.

## Tests

Set `TEST_DATABASE_URL` (required; no baked-in password). Example:

```bash
$env:TEST_DATABASE_URL="postgresql+asyncpg://USER:PASSWORD@localhost:5432/kr-web-solution-erp-test"
pytest
```
