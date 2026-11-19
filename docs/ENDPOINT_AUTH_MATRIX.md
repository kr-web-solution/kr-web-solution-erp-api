# Endpoint auth matrix

Generated from live `APIRouter` mounts under `app/api/` and `app/routers/`.

- `require_founder_ceo` = roles `founder_ceo`, `admin` (see `app/core/dependencies.py`).
- `require_staff` = roles `founder_ceo`, `admin`, `worker`.
- `get_current_user` = any active authenticated user; extra role/IDOR checks noted in **notes**.
- Client project visibility uses `project_clients` (`client_is_on_project` / `projects_visible_to_client`), not primary `Project.client_id` alone.
- Invoice client isolation uses `Invoice.client_id` (billing party). Drafts are never shown to clients.

| method | path | auth dependency | allowed roles | notes |
| --- | --- | --- | --- | --- |
| GET | `/api/health` | none | public | No auth |
| GET | `/api/v1/audit-logs` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/auth/forgot-password` | none | public | Always generic message |
| POST | `/api/v1/auth/google` | none | public | Invite-only; no auto-register |
| POST | `/api/v1/auth/login` | none | public | Sets httpOnly cookie |
| POST | `/api/v1/auth/logout` | none | public | Clears cookie even if unauthenticated |
| GET | `/api/v1/auth/me` | get_current_user | any authenticated | Self profile |
| PATCH | `/api/v1/auth/me` | get_current_user | any authenticated | Self name/password |
| PATCH | `/api/v1/auth/me/profile` | get_current_user | any authenticated | Self onboarding fields |
| POST | `/api/v1/auth/microsoft` | none | public | Invite-only; no auto-register |
| GET | `/api/v1/auth/oauth-providers` | none | public | Exposes OAuth client IDs only; secrets stay server-side |
| POST | `/api/v1/auth/reset-password` | none | public | Reset token |
| POST | `/api/v1/auth/set-password` | none | public | Invite token |
| GET | `/api/v1/clients` | get_current_user | founder_ceo, admin (all); worker (assigned projects); client (own org) | Unknown roles 403 |
| POST | `/api/v1/clients` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/clients/{client_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/clients/{client_id}` | get_current_user | founder_ceo, admin; worker if assigned; client own org only | IDOR: worker/client cannot fetch unrelated orgs |
| PATCH | `/api/v1/clients/{client_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/company-expenses` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/company-expenses` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/company-expenses/{expense_id}` | require_founder_ceo | founder_ceo, admin |  |
| PATCH | `/api/v1/company-expenses/{expense_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/company-expenses/{expense_id}/receipt` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/company-expenses/{expense_id}/renew` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/dashboard` | get_current_user + inline roles | founder_ceo, admin (full); worker (assigned projects); client (own org) | Unknown roles 403; client revenue scoped by invoice.client_id |
| GET | `/api/v1/engagements` | get_current_user + inline roles | founder_ceo, admin (all); worker (assigned ≥ stage 5); client (own, ≥ stage 5) |  |
| POST | `/api/v1/engagements` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/engagements/catalog/stages` | get_current_user | any authenticated |  |
| GET | `/api/v1/engagements/pipeline` | get_current_user + inline roles | founder_ceo, admin (all); worker (assigned ≥ stage 5); client (own, ≥ stage 5) |  |
| GET | `/api/v1/engagements/{engagement_id}` | get_current_user + _assert_can_view | founder_ceo, admin; assigned worker (stage ≥ 5); client matching engagement.client_id (stage ≥ 5) |  |
| PATCH | `/api/v1/engagements/{engagement_id}` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/advance` | get_current_user + _assert_can_view | founder_ceo, admin | Client/worker 403 |
| POST | `/api/v1/engagements/{engagement_id}/advance-invoice` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/checklist/{item_id}/toggle` | get_current_user + _assert_can_view | founder_ceo, admin | Client/worker 403 for pipeline checklist |
| POST | `/api/v1/engagements/{engagement_id}/documents` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/engagements/{engagement_id}/documents/{doc_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/engagements/{engagement_id}/documents/{doc_id}/file` | get_current_user + _assert_can_view | founder_ceo, admin; assigned worker (stage ≥ 5); client matching engagement.client_id (stage ≥ 5) |  |
| POST | `/api/v1/engagements/{engagement_id}/ensure-project` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/hold` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/jump` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/link-client` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/link-invoice` | require_founder_ceo | founder_ceo, admin |  |
| PATCH | `/api/v1/engagements/{engagement_id}/maintenance` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/maintenance/enable` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/maintenance/pause` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/maintenance/resume` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/mark-lost` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/regress` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/engagements/{engagement_id}/resume` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/invoices` | get_current_user + inline roles | founder_ceo, admin (all); client (own client_id, never drafts) | Worker 403; unknown roles 403 |
| POST | `/api/v1/invoices` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/invoices/late-fee-actions/pending` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/invoices/late-fee-actions/{action_id}/decide` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/invoices/recurring` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/invoices/recurring` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/invoices/recurring/{recurring_id}` | require_founder_ceo | founder_ceo, admin |  |
| PATCH | `/api/v1/invoices/recurring/{recurring_id}` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/invoices/{invoice_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/invoices/{invoice_id}` | get_current_user + _assert_invoice_access | founder_ceo, admin; client matching invoice.client_id (non-draft) | Worker 403; other client 403; unknown roles 403 |
| PATCH | `/api/v1/invoices/{invoice_id}` | require_founder_ceo | founder_ceo, admin | Paid invoices locked |
| POST | `/api/v1/invoices/{invoice_id}/cancel` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/invoices/{invoice_id}/credit-note` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/invoices/{invoice_id}/late-fee-action` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/invoices/{invoice_id}/mark-paid` | require_founder_ceo | founder_ceo, admin | Aliases: /pay and /mark-paid |
| POST | `/api/v1/invoices/{invoice_id}/pay` | require_founder_ceo | founder_ceo, admin | Aliases: /pay and /mark-paid |
| GET | `/api/v1/invoices/{invoice_id}/pdf` | get_current_user + _assert_invoice_access | founder_ceo, admin; client matching invoice.client_id (non-draft) | Same IDOR guard as GET invoice |
| POST | `/api/v1/invoices/{invoice_id}/send` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/leads` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/leads` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/leads/{lead_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/leads/{lead_id}` | require_founder_ceo | founder_ceo, admin |  |
| PATCH | `/api/v1/leads/{lead_id}` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/leads/{lead_id}/convert` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/notes` | get_current_user + inline roles | founder_ceo, admin; worker (client entities); client (own org only) | Lead notes: privileged only; unknown roles 403 |
| POST | `/api/v1/notes` | get_current_user + inline roles | founder_ceo, admin; worker (client entities); client (own org only) | Client cannot write notes on another client |
| DELETE | `/api/v1/notes/{note_id}` | get_current_user + inline roles | founder_ceo, admin; author of the note |  |
| GET | `/api/v1/projects` | get_current_user | founder_ceo, admin (all); worker (assigned); client (project_clients M2M) | Unknown roles 403 |
| POST | `/api/v1/projects` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/projects/{project_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/projects/{project_id}` | get_current_user + _assert_project_access | founder_ceo, admin; assigned worker; client on project_clients | Load-by-id then M2M visibility |
| PATCH | `/api/v1/projects/{project_id}` | get_current_user + inline roles | founder_ceo, admin (full); assigned worker (status only) | Client 403; unknown roles 403 |
| POST | `/api/v1/projects/{project_id}/checklist/{item_id}/toggle` | get_current_user + _assert_project_access | founder_ceo, admin; assigned worker | Client 403 |
| POST | `/api/v1/projects/{project_id}/clients` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/projects/{project_id}/clients/{client_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/projects/{project_id}/delivery` | get_current_user + _assert_project_access | founder_ceo, admin; assigned worker; client on project_clients | Client sees phase progress, not checklist ticks |
| POST | `/api/v1/projects/{project_id}/delivery/advance` | get_current_user + _assert_project_access | founder_ceo, admin; assigned worker | Client 403 |
| POST | `/api/v1/projects/{project_id}/delivery/regress` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/projects/{project_id}/documents` | get_current_user + _assert_project_access | founder_ceo, admin; assigned worker; client on project_clients | IDOR: other client's project docs 403 |
| POST | `/api/v1/projects/{project_id}/documents` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/projects/{project_id}/documents/{doc_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/projects/{project_id}/documents/{doc_id}/file` | get_current_user + _assert_project_access | founder_ceo, admin; assigned worker; client on project_clients | File bytes; project_id + doc_id must match |
| GET | `/api/v1/projects/{project_id}/features` | get_current_user + _assert_project_access | founder_ceo, admin; assigned worker; client on project_clients | Client sees approved/in_progress/done only |
| POST | `/api/v1/projects/{project_id}/features` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/projects/{project_id}/features/{feature_id}` | require_founder_ceo | founder_ceo, admin |  |
| PATCH | `/api/v1/projects/{project_id}/features/{feature_id}` | get_current_user + _assert_project_access | founder_ceo, admin (full); assigned worker (status only) | Client 403 |
| POST | `/api/v1/projects/{project_id}/features/{feature_id}/docs` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/projects/{project_id}/features/{feature_id}/docs/{doc_id}` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/projects/{project_id}/go-live` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/projects/{project_id}/invoices/advance` | require_founder_ceo | founder_ceo, admin | Currently returns 400 (manual invoice) |
| POST | `/api/v1/projects/{project_id}/invoices/balance` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/projects/{project_id}/maintenance/toggle` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/projects/{project_id}/money` | get_current_user + _assert_project_access | founder_ceo, admin; assigned worker (figures hidden); client on project_clients | Load-by-id then M2M visibility |
| POST | `/api/v1/projects/{project_id}/start` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/settings` | require_founder_ceo | founder_ceo, admin | Admin settings |
| PUT | `/api/v1/settings` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/settings/email-templates` | require_founder_ceo | founder_ceo, admin |  |
| PUT | `/api/v1/settings/email-templates/{template_id}` | require_founder_ceo | founder_ceo, admin |  |
| PUT | `/api/v1/settings/smtp` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/settings/test-email` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/settings/upi-qr` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/settings/upi-qr-file/{filename}` | get_current_user | any authenticated | Intentional: QR is only shown in authed UI; path traversal → 404 |
| GET | `/api/v1/staff` | require_staff | founder_ceo, admin (all); worker (self / reports) |  |
| GET | `/api/v1/staff/announcements` | require_staff | founder_ceo, admin, worker |  |
| POST | `/api/v1/staff/announcements` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/staff/attendance` | require_staff | founder_ceo, admin, worker | _can_manage_user on target |
| POST | `/api/v1/staff/attendance/check-in` | require_staff | founder_ceo, admin, worker | Self only |
| POST | `/api/v1/staff/attendance/check-out` | require_staff | founder_ceo, admin, worker | Self only |
| POST | `/api/v1/staff/attendance/corrections` | require_staff | founder_ceo, admin, worker | Self only |
| POST | `/api/v1/staff/attendance/corrections/{attendance_id}/{decision}` | require_staff | founder_ceo, admin, worker | Cannot approve own unless privileged |
| GET | `/api/v1/staff/documents` | require_staff | founder_ceo, admin, worker | Worker: self only |
| POST | `/api/v1/staff/documents` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/staff/documents/{doc_id}/download` | require_staff | founder_ceo, admin, worker | Worker: own docs only |
| GET | `/api/v1/staff/expenses` | require_staff | founder_ceo, admin, worker | Non-privileged see own |
| POST | `/api/v1/staff/expenses` | require_staff | founder_ceo, admin, worker |  |
| GET | `/api/v1/staff/expenses/{expense_id}/receipt` | require_staff | founder_ceo, admin, worker | _can_manage_user |
| POST | `/api/v1/staff/expenses/{expense_id}/{decision}` | require_staff | founder_ceo, admin, worker | _can_manage_user |
| GET | `/api/v1/staff/holidays` | require_staff | founder_ceo, admin, worker |  |
| POST | `/api/v1/staff/holidays` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/staff/holidays/{holiday_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/staff/inbox` | require_staff | founder_ceo, admin, worker |  |
| GET | `/api/v1/staff/leave` | require_staff | founder_ceo, admin, worker | scope=all is privileged |
| POST | `/api/v1/staff/leave` | require_staff | founder_ceo, admin, worker | Self only |
| GET | `/api/v1/staff/leave/calendar` | require_staff | founder_ceo, admin, worker | Visible staff ids |
| POST | `/api/v1/staff/leave/{leave_id}/{decision}` | require_staff | founder_ceo, admin, worker | Cannot approve own unless privileged |
| GET | `/api/v1/staff/me` | require_staff | founder_ceo, admin, worker | Client 403 |
| PATCH | `/api/v1/staff/me` | require_staff | founder_ceo, admin, worker | Job fields ignored for workers |
| GET | `/api/v1/staff/me/profile` | require_staff | founder_ceo, admin, worker |  |
| GET | `/api/v1/staff/payslips` | require_staff | founder_ceo, admin, worker | Worker: self only |
| POST | `/api/v1/staff/payslips` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/staff/payslips/preview` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/staff/payslips/{payslip_id}/pdf` | require_staff | founder_ceo, admin, worker | Worker: own payslip only |
| GET | `/api/v1/staff/tasks` | require_staff | founder_ceo, admin, worker | Non-managers see own tasks |
| POST | `/api/v1/staff/tasks` | require_staff | founder_ceo, admin, worker | Assignee must be self or report |
| PATCH | `/api/v1/staff/tasks/{task_id}` | require_staff | founder_ceo, admin, worker | _can_manage_user on assignee |
| PATCH | `/api/v1/staff/{user_id}` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/staff/{user_id}/bank/approve` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/staff/{user_id}/bank/reject` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/api/v1/users` | require_founder_ceo | founder_ceo, admin |  |
| POST | `/api/v1/users` | require_founder_ceo | founder_ceo, admin | Invite; no founder-typed password |
| GET | `/api/v1/users/roles` | require_founder_ceo | founder_ceo, admin |  |
| DELETE | `/api/v1/users/{user_id}` | require_founder_ceo | founder_ceo, admin | Cannot delete self |
| PATCH | `/api/v1/users/{user_id}` | require_founder_ceo | founder_ceo, admin |  |
| GET | `/health` | none | public | No auth |
