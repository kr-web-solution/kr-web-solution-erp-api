# Invoice Engine Upgrade — Completion Notes

**Revisions:** `028` → `029` → **`030` (`m0n1o2p3q4r5` document_snapshot)**  
**Money:** integer paise kept (best exact arithmetic).

## Core logic shipped

1. **Issued invoice freeze** — on send / create-as-sent / paid: stores `document_snapshot` (company, client, bank/UPI/wire, terms, project). PDF + API prefer snapshot so settings changes do not rewrite history.
2. **Edit lock** — after issue, amounts/items/tax/type locked; only soft fields (`notes`, `due_date`, UPI overrides) on unpaid issued invoices. Corrections → credit note.
3. **Tax** — CGST/SGST vs IGST from state codes; no invent when unknown.
4. **Mail** — To company email, CC contact (+ settings CC).
5. **Bill To** — company details only.
6. **Bank / UPI / Wire** — on balance due; paid shows confirmation + UTR history.
7. **Terms by type** — notes → settings → defaults.

## Demo data (11 invoices)

```text
python -m app.cli seed-invoice-demos
# with CONFIRM_REMOTE_DB=YES CONFIRM_DB_NAME=<db>
```

| Number | Type | Status |
|--------|------|--------|
| KRWS-SVC-2026-1001 | GST ON — company / type / calendar year / seq |
| KRWS-SVC-2026-G-1001 | GST OFF (Bill of Supply) |

Format: `KRWS` (company) · `SVC` (type) · `2026` (calendar year) · `1001` (starts at 1001). Re-run:

```bash
python -m app.cli seed-invoice-demos
```

Client: **Demo Technologies Pvt Ltd** (`cli_demo_invoice`).

## Still out of scope (intentionally)

- DECIMAL money migration (paise is preferable)
- IRN / e-invoice / reverse charge / SEZ
- Separate React InvoiceHeader package split

## Verify

- Migration 030 executed
- seed-invoice-demos: `{created: 11}`
- Snapshot + content lock + PDF smoke OK
