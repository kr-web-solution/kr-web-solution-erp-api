# KR Workspace — Backend API (`kr-erp-api`)

Production-ready, asynchronous modular backend for **KR Web Solution** internal ERP and CRM, built with FastAPI, PostgreSQL, SQLAlchemy 2.0, Alembic, and ReportLab.

---

## 1. Technical Stack

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/) 0.110+ (Python 3.12+ / 3.14)
- **Database Engine:** [PostgreSQL](https://www.postgresql.org/) with [asyncpg](https://github.com/MagicStack/asyncpg) driver
- **ORM:** [SQLAlchemy 2.0](https://www.sqlalchemy.org/) (`AsyncSession`, `Mapped`, `mapped_column`)
- **Database Migrations:** [Alembic](https://alembic.sqlalchemy.org/) (Async environment)
- **PDF Generation:** [ReportLab](https://www.reportlab.com/) (Pure Python vector PDF generation)
- **Authentication & Security:**
  - Password hashing: `bcrypt`
  - Token handling: `PyJWT` (HS256)
  - At-rest encryption: `cryptography.fernet` (AES-256 for SMTP secrets)
  - Transport: `httpOnly` secure cookies (`kr_access_token`) + optional `Authorization: Bearer`
- **Email Dispatch:** `aiosmtplib` (Async SMTP with STARTTLS support & attachments)
- **Background Scheduler:** `APScheduler` (Protected by PostgreSQL advisory locks)
- **Testing Suite:** `pytest`, `pytest-asyncio`, `httpx` (ASGI TestClient)

---

## 2. Directory Structure

```text
kr-erp-api/
├── alembic/                      # Database migration scripts
│   ├── versions/                 # Versioned migration files
│   │   └── aadf61f35e83_001_core_schema.py
│   └── env.py                    # Async migration runner with app metadata
├── app/
│   ├── api/
│   │   ├── v1/
│   │   │   └── router.py         # Sub-routers mount point (/api/v1)
│   │   └── router.py             # Root router & health endpoints
│   ├── common/
│   │   └── enums.py              # UserRole, ProjectStatus, InvoiceStatus, LeadStatus
│   ├── core/
│   │   ├── config.py             # Pydantic BaseSettings (.env loading)
│   │   ├── database.py           # Async engine, AsyncSessionLocal, Base declarative
│   │   ├── dependencies.py       # get_db, get_current_user, require_role
│   │   ├── exceptions.py         # Custom AppException handlers
│   │   ├── logging.py            # Standard Python logging configuration
│   │   └── security.py           # Password hashing, JWT creation/verification, Fernet AES
│   ├── models.py                 # Unified async SQLAlchemy models (11 tables)
│   ├── routers/
│   │   ├── auth.py               # Login, logout, me, password reset
│   │   ├── clients.py            # Client directory & accounts
│   │   ├── dashboard.py          # Role-aware executive metrics & recent activity
│   │   ├── invoices.py           # Invoices CRUD, atomic counter, PDF, SMTP, recurring
│   │   ├── leads.py              # Leads CRUD & transactional conversion
│   │   ├── notes.py              # Shared notes for clients & leads
│   │   ├── projects.py           # Projects CRUD & worker assignments
│   │   ├── settings.py           # Company profile, SMTP, test email, templates
│   │   └── users.py              # User account administration (Founder only)
│   ├── schemas.py                # Pydantic v2 validation & response models
│   ├── scripts/
│   │   └── seed.py               # Settings, templates, SUPER_ADMIN bootstrap (no dummy logins)
│   └── services/
│       ├── email.py              # Async SMTP sender with decrypted credentials
│       ├── pdf.py                # ReportLab vector tax invoice PDF generator
│       └── scheduler.py          # Daily recurring invoice processor with advisory lock
├── tests/
│   └── test_api.py               # Full test suite covering all 3 roles & endpoints
├── alembic.ini                   # Alembic configuration
├── main.py                       # FastAPI application factory & lifespan context
├── pytest.ini                    # Pytest configuration (pythonpath = .)
└── requirements.txt              # Production Python dependencies
```

---

## 3. Database Schema (PostgreSQL)

The database schema is managed via Alembic and consists of 11 normalized tables:

1. **`users`**: User authentication accounts. Roles: `founder_ceo`, `worker`, `client`.
2. **`clients`**: Consolidated client organizations and billing accounts (replaces separate companies/contacts).
3. **`leads`**: Incoming sales prospects with status lifecycle (`new`, `contacted`, `proposal`, `won`, `lost`).
4. **`projects`**: Technical engagements and deliverables (`lead`, `running`, `on_hold`, `stopped`, `completed`).
5. **`project_workers`**: Many-to-many relationship mapping workers to assigned projects.
6. **`invoices`**: Tax invoices with atomic sequence numbering (`INV-YYYY-XXXX`). Types: `project` or `maintenance`.
7. **`invoice_items`**: Line items per invoice (Description, Qty, Unit Rate in paise, Amount in paise).
8. **`recurring_invoices`**: Monthly recurring maintenance templates with `day_of_month` and `next_run_date`.
9. **`notes`**: Multi-tenant shared activity notes for `client` and `lead` entities.
10. **`settings`**: Singleton record (`id="default"`) for company profile, tax defaults, and encrypted SMTP credentials.
11. **`email_templates`**: Customizable templates (`invoice_sent`, `payment_reminder`, `lead_welcome`, `password_reset`).

---

## 4. Environment Variables (`.env`)

Create `kr-erp-api/.env` (modeled after `.env.example`):

```env
# Database (PostgreSQL with asyncpg driver)
DATABASE_URL=postgresql+asyncpg://postgres:udhaya@localhost:5432/kr-web-solution-erp

# Application & Environment
PROJECT_NAME="KR Workspace CRM + ERP API"
ENVIRONMENT=development
PRODUCTION=false
API_V1_STR=/api/v1
FRONTEND_URL=http://localhost:3000

# Security & Tokens
SECRET_KEY=5678402f1e2851e0d04db3f7f6117e188360cb31c07262cf4f6722eeaa0510d7
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ALGORITHM=HS256
COOKIE_NAME=kr_access_token

# Fernet Key for AES-256 SMTP Password Encryption
ENCRYPTION_KEY=Qlhs7DPpurqcfoT6JENui-kFxgVjkywqzwC-iEFcWwE=

# Bootstrap SUPER_ADMIN (created only when users table is empty)
# Gmail (any domain) allowed for this bootstrap account only.
SUPER_ADMIN_EMAIL=krwebsolutiondev@gmail.com
SUPER_ADMIN_NAME=KR WEB SOLUTION ADMIN
SUPER_ADMIN_PASSWORD=ChangeThisStrongPassword123!

# Google Sign-In (invite-only — no auto-signup)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# Microsoft Entra ID / Outlook (invite-only)
# Azure Portal → App registrations → SPA platform, redirect = FRONTEND_URL
MICROSOFT_CLIENT_ID=
MICROSOFT_TENANT_ID=common
MICROSOFT_CLIENT_SECRET=

# CORS Allowed Origins
BACKEND_CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]
```

**Bootstrap login**: when the `users` table has no active rows, seed creates one `admin` from `SUPER_ADMIN_*`. There are **no** dummy seeded logins. Additional people are invited from Settings → People (set-password email). Staff emails must be `@krwebsolution.com`; client/lead emails must not use that domain.

UI does **not** need OAuth env vars. It calls `GET /api/v1/auth/oauth-providers` for public client IDs. Keep `GOOGLE_*` / `MICROSOFT_*` on the API only.

To soft-delete legacy dummy logins and bootstrap if empty: `python -c "import asyncio; from app.scripts.seed import wipe_and_reseed_logins_async; asyncio.run(wipe_and_reseed_logins_async())"`

> [!IMPORTANT]
> Never commit `.env` to source control. It is explicitly ignored in `.gitignore`.

---

## 5. Setup & Execution

### 1. Database Creation
Ensure PostgreSQL is running locally and create the database:
```sql
CREATE DATABASE "kr-web-solution-erp";
```

### 2. Apply Migrations
Execute Alembic to construct all tables, constraints, and indexes:
```bash
alembic upgrade head
```

### 3. Start the API Server
Run Uvicorn with auto-reload:
```bash
python -m uvicorn main:app --reload --port 8000
```
- API Base URL: `http://127.0.0.1:8000`
- Interactive Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc Documentation: `http://127.0.0.1:8000/redoc`
- Health Check: `http://127.0.0.1:8000/health`

---

## 6. Role-Based Access Control (RBAC) Matrix

| Endpoint Route | HTTP Method | `founder_ceo` | `worker` | `client` |
| :--- | :---: | :---: | :---: | :---: |
| `/api/v1/auth/login` | POST | Allow | Allow | Allow |
| `/api/v1/auth/me` | GET | Allow | Allow | Allow |
| `/api/v1/dashboard` | GET | Full Overview | Assigned Projects Only | Own Projects & Bills |
| `/api/v1/clients` | GET / POST | Full CRUD | Assigned Clients Only | Own Profile Only |
| `/api/v1/projects` | GET | All Projects | Assigned Only (Budget Hidden) | Own Projects Only |
| `/api/v1/projects` | POST / DELETE | Allow | 403 Forbidden | 403 Forbidden |
| `/api/v1/projects/{id}` | PATCH | Full Edit | Status Update Only | 403 Forbidden |
| `/api/v1/leads` | ALL | Full CRUD | 403 Forbidden | 403 Forbidden |
| `/api/v1/leads/{id}/convert` | POST | Transactional Convert | 403 Forbidden | 403 Forbidden |
| `/api/v1/invoices` | GET | All Invoices | 403 Forbidden | Own Sent/Paid (No Drafts) |
| `/api/v1/invoices` | POST / PATCH / DELETE | Full CRUD | 403 Forbidden | 403 Forbidden |
| `/api/v1/invoices/{id}/pdf` | GET | Download PDF | 403 Forbidden | Download Own PDF |
| `/api/v1/invoices/{id}/send` | POST | Send via SMTP | 403 Forbidden | 403 Forbidden |
| `/api/v1/invoices/{id}/pay` | POST | Mark Paid | 403 Forbidden | 403 Forbidden |
| `/api/v1/invoices/recurring`| ALL | Full Schedule CRUD | 403 Forbidden | 403 Forbidden |
| `/api/v1/notes` | GET / POST | Full Access | Assigned Projects Only | Own Notes Only |
| `/api/v1/settings` | ALL | Full Admin | 403 Forbidden | 403 Forbidden |
| `/api/v1/users` | ALL | Full Admin | 403 Forbidden | 403 Forbidden |

---

## 7. Technical Implementation Details

### Atomic Invoice Numbering
Invoice numbers (e.g., `INV-2026-1001`) are generated sequentially inside an active database transaction using `SELECT ... FOR UPDATE` on the singleton `settings` record:
```python
settings_stmt = select(Settings).where(Settings.id == "default").with_for_update()
st = (await db.execute(settings_stmt)).scalar_one()
invoice_number = f"{st.invoice_prefix}{st.invoice_next_number}"
st.invoice_next_number += 1
```
This guarantees no race conditions or duplicate invoice numbers under high concurrency.

### Advisory Locks for Background Scheduler
The recurring invoice background job runs daily at 00:05 UTC. It uses PostgreSQL advisory locks to guarantee execution occurs strictly once, even when multiple Uvicorn worker processes are running:
```python
lock_res = await session.execute(text("SELECT pg_try_advisory_lock(987654321)"))
if not lock_res.scalar():
    return 0  # Lock held by another worker; gracefully skip
```

### ReportLab Vector PDF Invoice Generator
Located at [`app/services/pdf.py`](file:///c:/Users/Udhay/OneDrive/Desktop/MY%20PROJECTS/KR%20Web%20Solution/kr-web-solution-erp-crm/kr-erp-api/app/services/pdf.py):
- Generates high-resolution, GST-compliant A4 invoices.
- Formatted with Indian currency notation (`Rs. 1,50,000.00`).
- Features branded typography, itemized service lines, GST tax component, payment instructions, and bank/UPI coordinates.

---

## 8. Running Automated Tests

The backend test suite verifies all three roles, row-level data segregation, PDF generation, and atomic sequence increments.

Execute tests via:
```bash
python -m pytest -v
```

Expected output:
```text
tests/test_api.py::test_health PASSED                     [ 20%]
tests/test_api.py::test_auth_login_and_logout PASSED      [ 40%]
tests/test_api.py::test_founder_ceo_full_crud PASSED      [ 60%]
tests/test_api.py::test_worker_role_restrictions PASSED   [ 80%]
tests/test_api.py::test_client_role_restrictions PASSED   [100%]
======================= 5 passed in 4.30s =======================
```
