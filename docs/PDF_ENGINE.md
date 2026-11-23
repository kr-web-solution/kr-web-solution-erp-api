# PDF Engine

Reusable document PDF generation and manipulation inside `kr-erp-api`.

## Stack

| Layer | Library |
| --- | --- |
| HTML templates | Jinja2 |
| HTML → PDF | WeasyPrint |
| Fallback (invoices/payslips) | ReportLab |
| Merge / split / encrypt / watermark / info | pypdf |
| QR generation | qrcode + Pillow |

Existing invoice download/email/scheduler paths keep using `generate_invoice_pdf()` — they are unchanged for callers.

## Ubuntu 24.04 native dependencies

```bash
sudo apt-get update
sudo apt-get install -y \
  libcairo2 \
  libpango-1.0-0 \
  libpangocairo-1.0-0 \
  libgdk-pixbuf-2.0-0 \
  libffi-dev \
  shared-mime-info \
  fonts-dejavu-core \
  fonts-liberation \
  fonts-noto-core
```

Docker image already installs these (see `Dockerfile`).

### Windows (local)

WeasyPrint needs a GTK runtime. Without it, invoice and payslip generation **fall back to ReportLab**. Preview endpoints that only use WeasyPrint will fail until GTK is installed.

## Python packages

```text
weasyprint>=62.0
jinja2>=3.1.0
pypdf>=5.0.0
Pillow>=10.0.0
qrcode>=7.4.0
reportlab>=4.1.0
```

Install: `pip install -r requirements.txt`

## Layout

```text
app/services/pdf_engine/
  renderer.py           # Jinja + WeasyPrint (restricted URL fetcher)
  template_manager.py   # filesystem designs + resolution
  asset_manager.py      # logos, QR, safe paths, temp dir
  pdf_operations.py     # pypdf ops
  service.py            # concurrency gate, preview, temp cleanup
  context/invoice.py
  context/payslip.py
  schemas.py

app/templates/pdf/
  invoices/v1/
  payslips/v1/
  quotations/v1/
  purchase_orders/v1/
  reports/v1/

app/routers/pdf.py      # /api/v1/pdf/*
```

## Integrity rules

- Templates receive **precomputed** amounts (paise already calculated in services).
- Do not compute GST or totals in Jinja.
- Issued invoices use `document_snapshot` / `ensure_issued_snapshot`.
- Preview endpoints never allocate invoice numbers or mutate financial rows.
- Template registry stores **metadata only**; HTML stays on disk.

## API (`/api/v1/pdf`)

Auth: founder/admin (`require_founder_ceo`) unless noted.

### Generation

```http
POST /api/v1/pdf/generate/invoice
Content-Type: application/json

{ "invoice_id": "inv_...", "paper_size": "a4", "orientation": "portrait" }
```

```http
POST /api/v1/pdf/generate/receipt
{ "invoice_id": "inv_..." }
```

Requires `status=paid`.

```http
POST /api/v1/pdf/generate/payslip
{ "payslip_id": "pay_..." }
```

Responses: `application/pdf` stream.

Existing endpoints remain:

- `GET /api/v1/invoices/{id}/pdf`
- `GET /api/v1/staff/payslips/{id}/pdf`

### Preview (no financial side effects)

```http
POST /api/v1/pdf/preview/quotation
Content-Type: application/json

{
  "paper_size": "a4",
  "payload": {
    "company_name": "KR Web Solution",
    "quote_number": "Q-1001",
    "quote_date": "23 Nov 2026",
    "client_name": "Acme",
    "items": [
      { "title": "Website", "qty": "1", "unit": "Nos", "rate": "₹50,000.00", "amount": "₹50,000.00" }
    ],
    "total_fmt": "₹50,000.00",
    "watermark": "PREVIEW"
  }
}
```

Supported `doc_type`: `invoice`, `receipt`, `payslip`, `quotation`, `purchase_order`, `report`.

### Templates

```http
GET  /api/v1/pdf/templates
GET  /api/v1/pdf/templates/disk
GET  /api/v1/pdf/templates/{id}
POST /api/v1/pdf/templates
PUT  /api/v1/pdf/templates/{id}
DELETE /api/v1/pdf/templates/{id}
```

`POST` registers metadata for an **existing** filesystem design (`design_key` must resolve under `app/templates/pdf/`). Raw HTML upload is not allowed.

### pypdf operations

```http
POST /api/v1/pdf/merge          # multipart files[]
POST /api/v1/pdf/split          # file + form ranges=1-2,4
POST /api/v1/pdf/watermark      # file + form text, opacity
POST /api/v1/pdf/encrypt        # file + form user_password
POST /api/v1/pdf/info           # file → JSON page_count / encrypted / metadata
POST /api/v1/pdf/cleanup-temp
```

Limits: 25 MB per PDF, 200 pages, 20 files per merge.

## Page sizes

`a3`, `a4`, `a5`, `a6`, `letter`, `legal`, `tabloid`, `executive`, `b4`, `b5`  
Orientation: `portrait` | `landscape`

## Creating a new template design

1. Add `app/templates/pdf/<folder>/v2/document.html` (prefer inline CSS; reuse brand colors `#172554` / `#0F172A`).
2. Register via `POST /api/v1/pdf/templates` with `design_key` like `invoices/v2`.
3. Set `is_default` only when you want new renders to prefer it — **issued invoices keep their snapshot content** regardless.
4. Never put financial formulas in the template — pass formatted strings from Python context builders.

## Production

1. Run migration `043` (`z3a4b5c6d7e8_043_pdf_templates`).
2. Deploy API image (native Weasy libs already in Dockerfile).
3. Temp previews live under `uploads/tmp/pdf/` and are cleaned on startup/shutdown and via `/pdf/cleanup-temp`.

## Logging

Logger name: `kr.pdf`

Events: `pdf_render_start`, `pdf_render_complete`, merge/split/encrypt, temp cleanup.
