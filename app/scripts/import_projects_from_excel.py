"""
Excel bulk import: Clients → Projects → Invoices (multi-invoice / multi-line).

Template:
  python -m app.cli write-projects-import-template

Import (dry-run first):
  python -m app.cli import-projects-from-excel --file templates/completed_projects_import.xlsx --i-know-this-is-remote
  python -m app.cli import-projects-from-excel --file templates/completed_projects_import.xlsx --i-know-this-is-remote --apply
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..core.database import AsyncSessionLocal, not_deleted
from ..models import (
    Client,
    Invoice,
    InvoiceItem,
    InvoicePayment,
    Project,
    RecurringInvoice,
    Settings,
)
from ..services.client_project import create_project
from ..services.invoice_numbers import assign_unique_invoice_number
from ..services.invoice_snapshot import freeze_document_snapshot
from ..services.invoice_terms import resolve_invoice_terms
from ..services.invoice_typing import normalize_line_kind, resolve_invoice_typing
from ..services.project_delivery import ensure_project_checklist
from ..services.tax_payroll import (
    compute_tax_paise,
    normalize_state_code,
    split_gst_paise,
)

SHEET_CLIENTS = "Clients"
SHEET_PROJECTS = "Projects"
SHEET_INVOICES = "Invoices"
SHEET_INSTRUCTIONS = "Instructions"

PROJECT_TYPES = ("website", "web_app", "mobile_app", "ecommerce", "amc", "custom")
PROJECT_STATUSES = ("lead", "running", "on_hold", "stopped", "completed")
INVOICE_TYPES = (
    "custom_dev",
    "project",
    "advance",
    "balance",
    "hosting",
    "services",
    "licensing",
    "amc",
    "maintenance",
    "mixed",
)
INVOICE_STATUSES = ("draft", "sent", "paid", "overdue")
YES_NO = ("yes", "no")

HEADER_FILL = PatternFill("solid", fgColor="1E3A5F")
HEADER_FONT = Font(color="FFFFFF", bold=True)
REQUIRED_HEADER = PatternFill("solid", fgColor="B45309")
EXAMPLE_FILL = PatternFill("solid", fgColor="F0FDF4")
REQUIRED_CELL = PatternFill("solid", fgColor="FEF3C7")

CLIENT_COLUMNS: List[Tuple[str, bool, str]] = [
    ("client_name", True, "Aster Retail Pvt Ltd"),
    ("contact_name", False, "Ravi Kumar"),
    ("designation", False, "Director"),
    ("contact_email", False, "ravi@asterretail.com"),
    ("contact_phone", False, "9876543210"),
    ("email", False, "accounts@asterretail.com"),
    ("phone", False, "0422-1234567"),
    ("website", False, "https://asterretail.com"),
    ("address", False, "12 Avinashi Road"),
    ("city", False, "Coimbatore"),
    ("state", False, "Tamil Nadu"),
    ("pincode", False, "641014"),
    ("gstin", False, "33AAAAA0000A1Z5"),
    ("pan", False, "AAAAA0000A"),
    ("state_code", False, "33"),
    ("notes", False, ""),
]

PROJECT_COLUMNS: List[Tuple[str, bool, str]] = [
    ("project_name", True, "Aster Retail ERP"),
    ("client_name", True, "Aster Retail Pvt Ltd"),
    ("description", False, "Custom inventory + billing web app"),
    ("project_type", False, "web_app"),
    ("status", False, "completed"),
    ("start_date", False, "2024-01-15"),
    ("end_date", False, "2024-06-30"),
    ("budget_inr", False, "250000"),
    ("development_days", False, "90"),
    ("maintenance_amount_inr", False, "3000"),
    ("maintenance_send_day", False, "1"),
    ("maintenance_due_after_days", False, "5"),
]

INVOICE_COLUMNS: List[Tuple[str, bool, str]] = [
    ("invoice_key", True, "ASTER-ADV"),
    ("client_name", True, "Aster Retail Pvt Ltd"),
    ("project_name", True, "Aster Retail ERP"),
    ("invoice_type", False, "advance"),
    ("status", False, "paid"),
    ("gst_enabled", False, "yes"),
    ("issue_date", True, "2024-01-20"),
    ("due_date", True, "2024-02-05"),
    ("paid_at", False, "2024-02-01"),
    ("tax_rate_percent", False, "18"),
    ("discount_inr", False, "0"),
    ("po_number", False, ""),
    ("notes", False, "Advance 40%"),
    ("line_description", True, "Project advance — Aster Retail ERP"),
    ("quantity", True, "1"),
    ("unit_rate_inr", True, "100000"),
    ("line_kind", False, "advance"),
    ("hsn_sac", False, "998314"),
    ("unit", False, "Nos"),
    ("covers_from", False, ""),
    ("covers_to", False, ""),
    ("period_months", False, ""),
    ("payment_method", False, "upi"),
    ("payment_reference", False, "UTR123456"),
    ("payment_note", False, "Historical import"),
    ("invoice_number", False, ""),
]


def default_template_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "templates" / "completed_projects_import.xlsx"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _cell_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and value == int(value):
        value = int(value)
    s = str(value).strip()
    return s or None


def _parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # Spreadsheet typo: YYYY-DD-MM (middle > 12) → treat as day/month swapped
    parts = s.replace("/", "-").split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        a, b, c = (int(parts[0]), int(parts[1]), int(parts[2]))
        if a > 31 and b > 12 and 1 <= c <= 12:
            try:
                return date(a, c, b)
            except ValueError:
                pass
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date '{value}' (use YYYY-MM-DD)")


def _parse_datetime(value: Any) -> Optional[datetime]:
    d = _parse_date(value)
    if not d:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _parse_inr_to_paise(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    s = str(value).strip().replace(",", "").replace("₹", "").replace("Rs", "").replace("rs", "")
    if not s:
        return 0
    return int(round(float(s) * 100))


def _parse_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).strip().replace(",", ""))


def _parse_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, float) and value == int(value):
        return int(value)
    return int(str(value).strip())


def _parse_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    raise ValueError(f"Expected yes/no, got '{value}'")


def _norm_project_type(value: Optional[str]) -> str:
    raw = (value or "custom").strip().lower().replace(" ", "_").replace("-", "_")
    # Long free-text package blurbs → custom
    if "\n" in (value or "") or len(raw) > 40:
        return "custom"
    aliases = {
        "web": "website",
        "website": "website",
        "webapp": "web_app",
        "web_application": "web_app",
        "mobile": "mobile_app",
        "app": "mobile_app",
        "e_commerce": "ecommerce",
        "ecom": "ecommerce",
        "retainer": "amc",
        "digital_marketing": "custom",
        "marketing": "custom",
        "services": "custom",
    }
    code = aliases.get(raw, raw)
    if code not in PROJECT_TYPES:
        return "custom"
    return code


def _norm_project_status(value: Optional[str]) -> str:
    raw = (value or "completed").strip().lower().replace(" ", "_")
    if raw not in PROJECT_STATUSES:
        raise ValueError(f"Invalid status '{value}'. Use: {', '.join(PROJECT_STATUSES)}")
    return raw


def _norm_invoice_status(value: Optional[str]) -> str:
    raw = (value or "paid").strip().lower().replace(" ", "_")
    if raw not in INVOICE_STATUSES:
        raise ValueError(f"Invalid invoice status '{value}'. Use: {', '.join(INVOICE_STATUSES)}")
    return raw


def _norm_payment_method(value: Optional[str]) -> str:
    raw = (value or "bank_transfer").strip().lower()
    if "neft" in raw or "bank" in raw or "rtgs" in raw or "imps" in raw:
        return "bank_transfer"
    if "upi" in raw:
        return "upi"
    if "cash" in raw:
        return "cash"
    if "card" in raw:
        return "card"
    return raw[:40] or "bank_transfer"


def _parse_partial_paid_paise(note: Optional[str], total_paise: int) -> int:
    """Pull advance/partial amount from payment notes (e.g. 'advance of rs 7000')."""
    if not note:
        return 0
    import re

    m = re.search(
        r"(?:advance|paid|received)\s+(?:of\s+)?(?:rs\.?|₹)?\s*([0-9][0-9,]*)",
        note,
        flags=re.I,
    )
    if not m:
        return 0
    amt = int(str(m.group(1)).replace(",", "")) * 100
    return max(0, min(amt, total_paise))


# ---------------------------------------------------------------------------
# Template writer
# ---------------------------------------------------------------------------

def _style_header(ws, columns: List[Tuple[str, bool, str]]) -> None:
    for col_idx, (header, required, _) in enumerate(columns, start=1):
        cell = ws.cell(1, col_idx, header)
        cell.font = HEADER_FONT
        cell.fill = REQUIRED_HEADER if required else HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        width = max(14, len(header) + 2)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(width, 30)
    ws.row_dimensions[1].height = 28
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}1"


def _write_example_row(ws, row_idx: int, columns: List[Tuple[str, bool, str]], data: Dict[str, Any]) -> None:
    for col_idx, (header, _, _) in enumerate(columns, start=1):
        cell = ws.cell(row_idx, col_idx, data.get(header, ""))
        cell.fill = EXAMPLE_FILL


def _blank_required_rows(ws, columns: List[Tuple[str, bool, str]], start: int, end: int) -> None:
    for row_idx in range(start, end + 1):
        for col_idx, (_, required, _) in enumerate(columns, start=1):
            cell = ws.cell(row_idx, col_idx, "")
            if required:
                cell.fill = REQUIRED_CELL


def _add_list_validation(ws, columns: List[Tuple[str, bool, str]], header: str, choices: tuple) -> None:
    headers = [c[0] for c in columns]
    if header not in headers:
        return
    col = headers.index(header) + 1
    dv = DataValidation(type="list", formula1='"' + ",".join(choices) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    letter = get_column_letter(col)
    dv.add(f"{letter}2:{letter}500")


def write_template(path: Optional[Path] = None) -> Path:
    out = Path(path) if path else default_template_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # Instructions first
    info = wb.active
    info.title = SHEET_INSTRUCTIONS
    info["A1"] = "KR ERP — Clients + Projects + Invoices Import"
    info["A1"].font = Font(bold=True, size=14)
    lines = [
        "",
        "Fill 3 sheets. Delete green EXAMPLE rows before import (or keep them only if you want those demos).",
        "",
        "1) Clients  — one row per client. Required: client_name",
        "2) Projects — one row per project. Required: project_name + client_name (must match Clients)",
        "3) Invoices — one row per LINE ITEM. Required: invoice_key, client_name, project_name, issue_date, due_date, line_description, quantity, unit_rate_inr",
        "",
        "Multiple invoices for one project:",
        "  Use a different invoice_key per invoice (e.g. ASTER-ADV, ASTER-BAL, ASTER-AMC-2024).",
        "Multiple line items on one invoice:",
        "  Repeat the SAME invoice_key on several rows; only the first row's header fields are used.",
        "",
        "Money columns (*_inr) are RUPEES, not paise. Example: 100000 = ₹1,00,000.",
        "gst_enabled: yes / no. status: draft | sent | paid | overdue (default paid).",
        "invoice_number: leave blank to auto-generate (KRWS-DEV-2024-…), or put a unique historical number.",
        "",
        "Dry-run (PowerShell):",
        '  $env:CONFIRM_REMOTE_DB="YES"; $env:CONFIRM_DB_NAME="kr-web-solution-erp"',
        "  python -m app.cli import-projects-from-excel --file templates/completed_projects_import.xlsx --i-know-this-is-remote",
        "Write to DB:",
        "  python -m app.cli import-projects-from-excel --file templates/completed_projects_import.xlsx --i-know-this-is-remote --apply",
        "",
        "Import order: Clients → Projects → Invoices. Skips existing project (same name+client) and existing invoice_number.",
    ]
    for i, line in enumerate(lines, start=2):
        info[f"A{i}"] = line
    info.column_dimensions["A"].width = 118

    # Clients
    ws_c = wb.create_sheet(SHEET_CLIENTS)
    _style_header(ws_c, CLIENT_COLUMNS)
    _write_example_row(
        ws_c,
        2,
        CLIENT_COLUMNS,
        {
            "client_name": "Aster Retail Pvt Ltd",
            "contact_name": "Ravi Kumar",
            "designation": "Director",
            "contact_email": "ravi@asterretail.com",
            "contact_phone": "9876543210",
            "email": "accounts@asterretail.com",
            "city": "Coimbatore",
            "state": "Tamil Nadu",
            "pincode": "641014",
            "gstin": "33AAAAA0000A1Z5",
            "pan": "AAAAA0000A",
            "state_code": "33",
        },
    )
    _write_example_row(
        ws_c,
        3,
        CLIENT_COLUMNS,
        {
            "client_name": "GreenLeaf Organics",
            "contact_name": "Priya S",
            "contact_phone": "9000000001",
            "city": "Chennai",
            "state": "Tamil Nadu",
            "state_code": "33",
        },
    )
    _blank_required_rows(ws_c, CLIENT_COLUMNS, 4, 53)

    # Projects
    ws_p = wb.create_sheet(SHEET_PROJECTS)
    _style_header(ws_p, PROJECT_COLUMNS)
    _write_example_row(
        ws_p,
        2,
        PROJECT_COLUMNS,
        {
            "project_name": "Aster Retail ERP",
            "client_name": "Aster Retail Pvt Ltd",
            "description": "Custom inventory + billing web app",
            "project_type": "web_app",
            "status": "completed",
            "start_date": "2024-01-15",
            "end_date": "2024-06-30",
            "budget_inr": 250000,
            "development_days": 90,
            "maintenance_amount_inr": 3000,
            "maintenance_send_day": 1,
            "maintenance_due_after_days": 5,
        },
    )
    _write_example_row(
        ws_p,
        3,
        PROJECT_COLUMNS,
        {
            "project_name": "GreenLeaf Corporate Website",
            "client_name": "GreenLeaf Organics",
            "description": "Marketing website with blog",
            "project_type": "website",
            "status": "completed",
            "start_date": "2023-08-01",
            "end_date": "2023-10-15",
            "budget_inr": 75000,
            "development_days": 45,
        },
    )
    _blank_required_rows(ws_p, PROJECT_COLUMNS, 4, 53)
    _add_list_validation(ws_p, PROJECT_COLUMNS, "project_type", PROJECT_TYPES)
    _add_list_validation(ws_p, PROJECT_COLUMNS, "status", PROJECT_STATUSES)

    # Invoices — multiple for Aster, one for GreenLeaf
    ws_i = wb.create_sheet(SHEET_INVOICES)
    _style_header(ws_i, INVOICE_COLUMNS)
    invoice_examples = [
        {
            "invoice_key": "ASTER-ADV",
            "client_name": "Aster Retail Pvt Ltd",
            "project_name": "Aster Retail ERP",
            "invoice_type": "advance",
            "status": "paid",
            "gst_enabled": "yes",
            "issue_date": "2024-01-20",
            "due_date": "2024-02-05",
            "paid_at": "2024-02-01",
            "tax_rate_percent": 18,
            "line_description": "Project advance 40% — Aster Retail ERP",
            "quantity": 1,
            "unit_rate_inr": 100000,
            "line_kind": "advance",
            "hsn_sac": "998314",
            "unit": "Nos",
            "payment_method": "upi",
            "payment_reference": "UTRADV001",
            "notes": "Advance payment",
        },
        {
            "invoice_key": "ASTER-BAL",
            "client_name": "Aster Retail Pvt Ltd",
            "project_name": "Aster Retail ERP",
            "invoice_type": "balance",
            "status": "paid",
            "gst_enabled": "yes",
            "issue_date": "2024-06-28",
            "due_date": "2024-07-12",
            "paid_at": "2024-07-10",
            "tax_rate_percent": 18,
            "line_description": "Final balance 60% — Aster Retail ERP",
            "quantity": 1,
            "unit_rate_inr": 150000,
            "line_kind": "balance",
            "hsn_sac": "998314",
            "unit": "Nos",
            "payment_method": "bank_transfer",
            "payment_reference": "UTR BAL002",
            "notes": "Balance on delivery",
        },
        {
            "invoice_key": "ASTER-AMC-2024",
            "client_name": "Aster Retail Pvt Ltd",
            "project_name": "Aster Retail ERP",
            "invoice_type": "amc",
            "status": "paid",
            "gst_enabled": "yes",
            "issue_date": "2024-07-01",
            "due_date": "2024-07-10",
            "paid_at": "2024-07-08",
            "tax_rate_percent": 18,
            "line_description": "AMC Jul–Dec 2024",
            "quantity": 6,
            "unit_rate_inr": 3000,
            "line_kind": "amc",
            "hsn_sac": "998313",
            "unit": "Months",
            "covers_from": "2024-07-01",
            "covers_to": "2024-12-31",
            "period_months": 6,
            "payment_method": "upi",
            "payment_reference": "UTRAMC003",
        },
        {
            "invoice_key": "GREEN-1",
            "client_name": "GreenLeaf Organics",
            "project_name": "GreenLeaf Corporate Website",
            "invoice_type": "custom_dev",
            "status": "paid",
            "gst_enabled": "no",
            "issue_date": "2023-10-15",
            "due_date": "2023-10-30",
            "paid_at": "2023-10-20",
            "line_description": "Website design & build",
            "quantity": 1,
            "unit_rate_inr": 75000,
            "line_kind": "custom_dev",
            "unit": "Nos",
            "payment_method": "upi",
            "payment_reference": "UTRGRN001",
        },
    ]
    for idx, row in enumerate(invoice_examples, start=2):
        _write_example_row(ws_i, idx, INVOICE_COLUMNS, row)
    _blank_required_rows(ws_i, INVOICE_COLUMNS, 6, 105)
    _add_list_validation(ws_i, INVOICE_COLUMNS, "invoice_type", INVOICE_TYPES)
    _add_list_validation(ws_i, INVOICE_COLUMNS, "status", INVOICE_STATUSES)
    _add_list_validation(ws_i, INVOICE_COLUMNS, "gst_enabled", YES_NO)
    _add_list_validation(
        ws_i,
        INVOICE_COLUMNS,
        "line_kind",
        ("custom_dev", "advance", "balance", "amc", "hosting", "services", "licensing"),
    )

    wb.save(out)
    return out


# ---------------------------------------------------------------------------
# Excel readers
# ---------------------------------------------------------------------------

def _read_sheet_dicts(path: Path, sheet: str, required_headers: List[str]) -> List[Dict[str, Any]]:
    wb = load_workbook(path, data_only=True)
    if sheet not in wb.sheetnames:
        raise ValueError(f"Sheet '{sheet}' not found. Sheets: {wb.sheetnames}")
    ws = wb[sheet]
    header_cells = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    headers = [_cell_str(h) for h in header_cells]
    missing = [h for h in required_headers if h not in headers]
    if missing:
        raise ValueError(f"Sheet '{sheet}' missing columns: {missing}")

    rows: List[Dict[str, Any]] = []
    for excel_row, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(v is not None and str(v).strip() != "" for v in values):
            continue
        raw = {headers[i]: values[i] for i in range(len(headers)) if headers[i]}
        raw["_excel_row"] = excel_row
        rows.append(raw)
    return rows


def read_clients(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in _read_sheet_dicts(path, SHEET_CLIENTS, ["client_name"]):
        name = _cell_str(raw.get("client_name"))
        if not name:
            raise ValueError(f"Clients row {raw['_excel_row']}: client_name is required")
        out.append(
            {
                "excel_row": raw["_excel_row"],
                "client_name": name,
                "contact_name": _cell_str(raw.get("contact_name")),
                "designation": _cell_str(raw.get("designation")),
                "contact_email": _cell_str(raw.get("contact_email")),
                "contact_phone": _cell_str(raw.get("contact_phone")),
                "email": _cell_str(raw.get("email")),
                "phone": _cell_str(raw.get("phone")),
                "website": _cell_str(raw.get("website")),
                "address": _cell_str(raw.get("address")),
                "city": _cell_str(raw.get("city")),
                "state": _cell_str(raw.get("state")),
                "pincode": _cell_str(raw.get("pincode")),
                "gstin": _cell_str(raw.get("gstin")),
                "pan": _cell_str(raw.get("pan")),
                "state_code": _cell_str(raw.get("state_code")),
                "notes": _cell_str(raw.get("notes")),
            }
        )
    return out


def read_projects(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in _read_sheet_dicts(path, SHEET_PROJECTS, ["project_name", "client_name"]):
        project_name = _cell_str(raw.get("project_name"))
        client_name = _cell_str(raw.get("client_name"))
        if not project_name or not client_name:
            raise ValueError(
                f"Projects row {raw['_excel_row']}: project_name and client_name are required"
            )
        out.append(
            {
                "excel_row": raw["_excel_row"],
                "project_name": project_name,
                "client_name": client_name,
                "description": _cell_str(raw.get("description")),
                "project_type": _norm_project_type(_cell_str(raw.get("project_type"))),
                "status": _norm_project_status(_cell_str(raw.get("status"))),
                "start_date": _parse_date(raw.get("start_date")),
                "end_date": _parse_date(raw.get("end_date")),
                "budget_paise": _parse_inr_to_paise(raw.get("budget_inr")),
                "development_days": _parse_int(raw.get("development_days")),
                "maintenance_amount_paise": _parse_inr_to_paise(raw.get("maintenance_amount_inr")),
                "maintenance_send_day": _parse_int(raw.get("maintenance_send_day")) or 1,
                "maintenance_due_after_days": _parse_int(raw.get("maintenance_due_after_days")) or 5,
            }
        )
    return out


def read_invoices(path: Path) -> List[Dict[str, Any]]:
    """Group invoice line rows by invoice_key (first row wins for header fields)."""
    raw_rows = _read_sheet_dicts(
        path,
        SHEET_INVOICES,
        [
            "invoice_key",
            "client_name",
            "project_name",
            "issue_date",
            "due_date",
            "line_description",
            "quantity",
            "unit_rate_inr",
        ],
    )
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for raw in raw_rows:
        key = _cell_str(raw.get("invoice_key"))
        client_name = _cell_str(raw.get("client_name"))
        project_name = _cell_str(raw.get("project_name"))
        line_description = _cell_str(raw.get("line_description"))
        if not key or not client_name or not project_name or not line_description:
            raise ValueError(
                f"Invoices row {raw['_excel_row']}: invoice_key, client_name, "
                "project_name, line_description are required"
            )
        issue_date = _parse_date(raw.get("issue_date"))
        due_date = _parse_date(raw.get("due_date"))
        if not issue_date or not due_date:
            raise ValueError(f"Invoices row {raw['_excel_row']}: issue_date and due_date required")

        qty = _parse_float(raw.get("quantity"), 1.0)
        rate_paise = _parse_inr_to_paise(raw.get("unit_rate_inr"))
        line = {
            "description": line_description[:255],
            "quantity": qty,
            "unit_rate_paise": rate_paise,
            "amount_paise": int(round(qty * rate_paise)),
            "line_kind": _cell_str(raw.get("line_kind")),
            "hsn_sac": _cell_str(raw.get("hsn_sac")),
            "unit": _cell_str(raw.get("unit")),
            "excel_row": raw["_excel_row"],
        }

        if key not in grouped:
            order.append(key)
            grouped[key] = {
                "invoice_key": key,
                "excel_row": raw["_excel_row"],
                "client_name": client_name,
                "project_name": project_name,
                "invoice_type": _cell_str(raw.get("invoice_type")) or "custom_dev",
                "status": _norm_invoice_status(_cell_str(raw.get("status"))),
                "gst_enabled": _parse_bool(raw.get("gst_enabled"), True),
                "issue_date": issue_date,
                "due_date": due_date,
                "paid_at": _parse_datetime(raw.get("paid_at")),
                "tax_rate_percent": _parse_float(raw.get("tax_rate_percent"), 18.0),
                "discount_paise": _parse_inr_to_paise(raw.get("discount_inr")),
                "po_number": _cell_str(raw.get("po_number")),
                "notes": _cell_str(raw.get("notes")),
                "covers_from": _parse_date(raw.get("covers_from")),
                "covers_to": _parse_date(raw.get("covers_to")),
                "period_months": _parse_int(raw.get("period_months")),
                "payment_method": _norm_payment_method(
                    _cell_str(raw.get("payment_method")) or "bank_transfer"
                ),
                "payment_reference": (_cell_str(raw.get("payment_reference")) or "")[:120] or None,
                "payment_note": _cell_str(raw.get("payment_note")) or "Historical Excel import",
                "invoice_number": _cell_str(raw.get("invoice_number")),
                "lines": [line],
            }
        else:
            grouped[key]["lines"].append(line)

    return [grouped[k] for k in order]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _find_client(db: AsyncSession, name: str) -> Optional[Client]:
    result = await db.execute(
        select(Client).where(func.lower(Client.name) == name.lower(), not_deleted(Client))
    )
    return result.scalars().first()


async def _find_project(db: AsyncSession, name: str, client_id: str) -> Optional[Project]:
    result = await db.execute(
        select(Project).where(
            func.lower(Project.name) == name.lower(),
            Project.client_id == client_id,
            not_deleted(Project),
        )
    )
    return result.scalars().first()


async def _find_invoice_by_number(db: AsyncSession, number: str) -> Optional[Invoice]:
    result = await db.execute(
        select(Invoice).where(Invoice.invoice_number == number, not_deleted(Invoice))
    )
    return result.scalars().first()


async def _upsert_client(db: AsyncSession, row: Dict[str, Any], *, apply: bool) -> Tuple[Client, bool]:
    existing = await _find_client(db, row["client_name"])
    if existing:
        if apply:
            mapping = {
                "primary_contact_name": row["contact_name"],
                "designation": row["designation"],
                "contact_email": row["contact_email"],
                "contact_phone": row["contact_phone"],
                "email": row["email"],
                "phone": row["phone"],
                "website": row["website"],
                "address": row["address"],
                "city": row["city"],
                "state": row["state"],
                "pincode": row["pincode"],
                "gstin": row["gstin"],
                "pan": row["pan"],
                "state_code": row["state_code"],
                "notes": row["notes"],
            }
            for attr, val in mapping.items():
                if val and not getattr(existing, attr, None):
                    setattr(existing, attr, val)
        return existing, False

    client = Client(
        name=row["client_name"],
        primary_contact_name=row["contact_name"],
        designation=row["designation"],
        contact_email=row["contact_email"],
        contact_phone=row["contact_phone"],
        email=row["email"],
        phone=row["phone"],
        website=row["website"],
        address=row["address"],
        city=row["city"],
        state=row["state"],
        pincode=row["pincode"],
        gstin=row["gstin"],
        pan=row["pan"],
        state_code=row["state_code"],
        notes=row["notes"],
    )
    if apply:
        db.add(client)
        await db.flush()
    return client, True


def _compute_invoice_money(
    *,
    lines: List[Dict[str, Any]],
    discount_paise: int,
    gst_on: bool,
    tax_rate_percent: float,
    settings: Settings,
    client: Client,
) -> Dict[str, Any]:
    subtotal = sum(int(ln["amount_paise"]) for ln in lines)
    taxable = max(0, subtotal - max(0, discount_paise))
    if gst_on:
        rate = float(tax_rate_percent or getattr(settings, "default_tax_rate", 18) or 18)
        tax_paise = compute_tax_paise(taxable, rate)
        supplier_code = normalize_state_code(
            getattr(settings, "company_state_code", None)
        ) or normalize_state_code(getattr(settings, "company_gstin", None))
        customer_code = normalize_state_code(
            getattr(client, "state_code", None)
        ) or normalize_state_code(getattr(client, "gstin", None))
        split = split_gst_paise(
            tax_paise,
            supplier_state_code=supplier_code,
            customer_state_code=customer_code,
            gst_enabled=True,
        )
        return {
            "subtotal_paise": subtotal,
            "tax_rate_percent": rate,
            "tax_paise": tax_paise,
            "total_paise": taxable + tax_paise,
            "place_of_supply": (getattr(client, "state", None) or "").strip() or None,
            "supply_type": split.get("supply_type"),
            "cgst_paise": int(split.get("cgst_paise") or 0),
            "sgst_paise": int(split.get("sgst_paise") or 0),
            "igst_paise": int(split.get("igst_paise") or 0),
        }

    return {
        "subtotal_paise": subtotal,
        "tax_rate_percent": 0.0,
        "tax_paise": 0,
        "total_paise": taxable,
        "place_of_supply": None,
        "supply_type": None,
        "cgst_paise": 0,
        "sgst_paise": 0,
        "igst_paise": 0,
    }


async def _create_invoice(
    db: AsyncSession,
    *,
    row: Dict[str, Any],
    client: Client,
    project: Project,
    settings: Settings,
) -> Invoice:
    header_type, header_ledger, _ = resolve_invoice_typing(
        invoice_type=row["invoice_type"],
        items=[
            {
                "line_kind": ln.get("line_kind"),
                "description": ln["description"],
                "quantity": ln["quantity"],
                "unit_rate_paise": ln["unit_rate_paise"],
            }
            for ln in row["lines"]
        ],
    )
    money = _compute_invoice_money(
        lines=row["lines"],
        discount_paise=row["discount_paise"],
        gst_on=row["gst_enabled"],
        tax_rate_percent=row["tax_rate_percent"],
        settings=settings,
        client=client,
    )
    status = row["status"]
    paid_at = row["paid_at"]
    if status == "paid" and not paid_at:
        paid_at = datetime(
            row["issue_date"].year,
            row["issue_date"].month,
            row["issue_date"].day,
            tzinfo=timezone.utc,
        )

    inv = Invoice(
        invoice_number=row["invoice_number"] or "",
        invoice_type=header_type,
        invoice_type_id=f"itp_{header_type}"[:36],
        ledger=header_ledger,
        ledger_id=f"ild_{header_ledger}"[:36] if header_ledger in ("development", "maintenance") else None,
        client_id=client.id,
        project_id=project.id,
        status=status,
        status_id=f"ist_{status}"[:36],
        issue_date=row["issue_date"],
        due_date=row["due_date"],
        subtotal_paise=money["subtotal_paise"],
        tax_rate_percent=money["tax_rate_percent"],
        tax_paise=money["tax_paise"],
        total_paise=money["total_paise"],
        gst_enabled=row["gst_enabled"],
        place_of_supply=money["place_of_supply"],
        supply_type=money["supply_type"],
        cgst_paise=money["cgst_paise"],
        sgst_paise=money["sgst_paise"],
        igst_paise=money["igst_paise"],
        discount_paise=row["discount_paise"],
        round_off_paise=0,
        po_number=row["po_number"],
        notes=row["notes"] or f"Imported ({row['invoice_key']})",
        covers_from=row["covers_from"],
        covers_to=row["covers_to"],
        period_months=row["period_months"] or 1,
        email_status="pending",
        amount_paid_paise=0,
        paid_at=None,
    )

    if row["invoice_number"]:
        db.add(inv)
        await db.flush()
    else:
        await assign_unique_invoice_number(
            db,
            inv,
            invoice_type=header_type,
            issue_date=row["issue_date"],
            settings_row=settings,
            gst_enabled=row["gst_enabled"],
        )

    for ln in row["lines"]:
        kind = normalize_line_kind(
            ln.get("line_kind"),
            header_type if header_type != "mixed" else "custom_dev",
        )
        db.add(
            InvoiceItem(
                invoice_id=inv.id,
                description=ln["description"],
                quantity=ln["quantity"],
                unit_rate_paise=ln["unit_rate_paise"],
                amount_paise=ln["amount_paise"],
                line_kind=kind,
                hsn_sac=(None if not row["gst_enabled"] else (ln.get("hsn_sac") or settings.default_sac_code)),
                unit=ln.get("unit"),
            )
        )

    pay_amount = 0
    if status == "paid":
        pay_amount = money["total_paise"]
    elif status in ("overdue", "sent") and (row.get("payment_reference") or row.get("payment_note")):
        pay_amount = _parse_partial_paid_paise(row.get("payment_note"), money["total_paise"])

    if pay_amount > 0:
        pay_at = paid_at or datetime(
            row["issue_date"].year,
            row["issue_date"].month,
            row["issue_date"].day,
            tzinfo=timezone.utc,
        )
        db.add(
            InvoicePayment(
                invoice_id=inv.id,
                amount_paise=pay_amount,
                method=row["payment_method"],
                reference=row["payment_reference"],
                paid_at=pay_at,
                note=row["payment_note"],
                status="verified",
                verified_at=pay_at,
            )
        )
        inv.amount_paid_paise = pay_amount
        if pay_amount >= money["total_paise"] and status == "paid":
            inv.paid_at = pay_at
        elif status == "paid":
            inv.paid_at = pay_at

    await db.flush()

    if status in ("sent", "paid", "overdue"):
        freeze_document_snapshot(
            inv,
            client=client,
            settings=settings,
            project=project,
            force=True,
        )
        terms = resolve_invoice_terms(
            invoice_type=inv.invoice_type,
            invoice_notes=None,
            settings=settings,
            status=inv.status,
        )
        snap = dict(inv.document_snapshot or {})
        snap["terms"] = terms
        company = dict(snap.get("company") or {})
        company["gst_enabled"] = bool(row["gst_enabled"])
        if not row["gst_enabled"]:
            company["gstin"] = None
            company["pan"] = None
        snap["company"] = company
        inv.document_snapshot = snap
        flag_modified(inv, "document_snapshot")

    return inv


# ---------------------------------------------------------------------------
# Main import
# ---------------------------------------------------------------------------

async def import_projects_from_excel_async(
    file_path: Path,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    clients_rows = read_clients(path)
    projects_rows = read_projects(path)
    invoices_rows = read_invoices(path)

    summary: Dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "file": str(path),
        "clients_rows": len(clients_rows),
        "projects_rows": len(projects_rows),
        "invoice_groups": len(invoices_rows),
        "clients_created": 0,
        "clients_reused": 0,
        "projects_created": 0,
        "projects_skipped": 0,
        "maintenance_created": 0,
        "invoices_created": 0,
        "invoices_skipped": 0,
        "actions": [],
    }

    apply = not dry_run
    client_map: Dict[str, Client] = {}  # lower name → Client
    project_map: Dict[Tuple[str, str], Project] = {}  # (client_lower, project_lower) → Project

    async with AsyncSessionLocal() as db:
        settings = (
            await db.execute(select(Settings).where(Settings.id == "default"))
        ).scalars().first()
        if not settings:
            raise RuntimeError("Settings row id='default' is missing — seed the app first")

        # --- Clients ---
        for row in clients_rows:
            client, created = await _upsert_client(db, row, apply=apply)
            client_map[row["client_name"].lower()] = client
            if created:
                summary["clients_created"] += 1
            else:
                summary["clients_reused"] += 1
            summary["actions"].append(
                {
                    "sheet": SHEET_CLIENTS,
                    "row": row["excel_row"],
                    "action": "create_client" if created else "reuse_client",
                    "client": row["client_name"],
                    "client_id": getattr(client, "id", None),
                }
            )

        # Ensure invoice/project clients exist even if omitted from Clients sheet
        needed_clients = {r["client_name"] for r in projects_rows} | {
            r["client_name"] for r in invoices_rows
        }
        for name in needed_clients:
            if name.lower() in client_map:
                continue
            stub = {
                "client_name": name,
                "contact_name": None,
                "designation": None,
                "contact_email": None,
                "contact_phone": None,
                "email": None,
                "phone": None,
                "website": None,
                "address": None,
                "city": None,
                "state": None,
                "pincode": None,
                "gstin": None,
                "pan": None,
                "state_code": None,
                "notes": None,
            }
            client, created = await _upsert_client(db, stub, apply=apply)
            client_map[name.lower()] = client
            if created:
                summary["clients_created"] += 1
            else:
                summary["clients_reused"] += 1

        # --- Projects ---
        for row in projects_rows:
            client = client_map.get(row["client_name"].lower())
            if not client or not getattr(client, "id", None):
                if apply:
                    raise ValueError(f"Client '{row['client_name']}' missing for project '{row['project_name']}'")
                # dry-run placeholder
                summary["projects_created"] += 1
                summary["actions"].append(
                    {
                        "sheet": SHEET_PROJECTS,
                        "row": row["excel_row"],
                        "action": "create_project",
                        "project": row["project_name"],
                        "client": row["client_name"],
                        "status": row["status"],
                        "budget_inr": row["budget_paise"] / 100,
                    }
                )
                continue

            existing = await _find_project(db, row["project_name"], client.id)
            if existing:
                project_map[(row["client_name"].lower(), row["project_name"].lower())] = existing
                summary["projects_skipped"] += 1
                summary["actions"].append(
                    {
                        "sheet": SHEET_PROJECTS,
                        "row": row["excel_row"],
                        "action": "skip_project",
                        "project": row["project_name"],
                        "client": row["client_name"],
                        "reason": f"already exists as {existing.id}",
                    }
                )
                continue

            if apply:
                delivery_phase = 14 if row["status"] == "completed" else 5
                project = await create_project(
                    db,
                    name=row["project_name"],
                    client_ids=[client.id],
                    description=row["description"],
                    budget_paise=row["budget_paise"],
                    development_days=row["development_days"],
                    status=row["status"],
                    delivery_phase=delivery_phase,
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                )
                project.project_type = row["project_type"]
                project.project_type_id = f"ptp_{row['project_type']}"[:36]
                project.status_id = f"pst_{row['status']}"[:36]
                if row["status"] == "completed":
                    project.go_live_at = row["end_date"] or row["start_date"] or date.today()
                await ensure_project_checklist(
                    db,
                    project,
                    done_through_stage=14 if row["status"] == "completed" else 0,
                )
                if row["maintenance_amount_paise"] > 0:
                    send_day = min(max(1, int(row["maintenance_send_day"])), 28)
                    starts = row["end_date"] or date.today()
                    db.add(
                        RecurringInvoice(
                            client_id=client.id,
                            project_id=project.id,
                            amount_paise=row["maintenance_amount_paise"],
                            send_day=send_day,
                            due_after_days=int(row["maintenance_due_after_days"]),
                            reminder_every_days=2,
                            starts_on=starts,
                            ends_on=None,
                            day_of_month=send_day,
                            next_run_date=date(2099, 1, 1),
                            active=True,
                            notes=f"Monthly maintenance for {row['project_name']}",
                            line_items_template=[
                                {
                                    "description": f"{row['project_name']} - Monthly Maintenance",
                                    "quantity": 1,
                                    "unit_rate_paise": row["maintenance_amount_paise"],
                                }
                            ],
                        )
                    )
                    summary["maintenance_created"] += 1
                project_map[(row["client_name"].lower(), row["project_name"].lower())] = project
                summary["projects_created"] += 1
                summary["actions"].append(
                    {
                        "sheet": SHEET_PROJECTS,
                        "row": row["excel_row"],
                        "action": "create_project",
                        "project": row["project_name"],
                        "project_id": project.id,
                        "client": row["client_name"],
                        "status": row["status"],
                        "budget_inr": row["budget_paise"] / 100,
                    }
                )
            else:
                summary["projects_created"] += 1
                if row["maintenance_amount_paise"] > 0:
                    summary["maintenance_created"] += 1
                summary["actions"].append(
                    {
                        "sheet": SHEET_PROJECTS,
                        "row": row["excel_row"],
                        "action": "create_project",
                        "project": row["project_name"],
                        "client": row["client_name"],
                        "status": row["status"],
                        "budget_inr": row["budget_paise"] / 100,
                    }
                )

        # Resolve projects referenced only from invoices (already in DB)
        for inv_row in invoices_rows:
            key = (inv_row["client_name"].lower(), inv_row["project_name"].lower())
            if key in project_map:
                continue
            client = client_map.get(inv_row["client_name"].lower())
            if client and getattr(client, "id", None):
                existing = await _find_project(db, inv_row["project_name"], client.id)
                if existing:
                    project_map[key] = existing

        # --- Invoices ---
        for row in invoices_rows:
            client = client_map.get(row["client_name"].lower())
            project = project_map.get((row["client_name"].lower(), row["project_name"].lower()))
            line_total_inr = sum(ln["amount_paise"] for ln in row["lines"]) / 100

            if row["invoice_number"]:
                existing_inv = await _find_invoice_by_number(db, row["invoice_number"])
                if existing_inv:
                    summary["invoices_skipped"] += 1
                    summary["actions"].append(
                        {
                            "sheet": SHEET_INVOICES,
                            "row": row["excel_row"],
                            "action": "skip_invoice",
                            "invoice_key": row["invoice_key"],
                            "reason": f"invoice_number {row['invoice_number']} already exists",
                        }
                    )
                    continue

            if apply:
                if not client or not getattr(client, "id", None):
                    raise ValueError(
                        f"Invoice '{row['invoice_key']}': client '{row['client_name']}' not found"
                    )
                if not project or not getattr(project, "id", None):
                    raise ValueError(
                        f"Invoice '{row['invoice_key']}': project '{row['project_name']}' "
                        f"for client '{row['client_name']}' not found — add it on Projects sheet"
                    )
                inv = await _create_invoice(
                    db,
                    row=row,
                    client=client,
                    project=project,
                    settings=settings,
                )
                summary["invoices_created"] += 1
                summary["actions"].append(
                    {
                        "sheet": SHEET_INVOICES,
                        "row": row["excel_row"],
                        "action": "create_invoice",
                        "invoice_key": row["invoice_key"],
                        "invoice_number": inv.invoice_number,
                        "invoice_id": inv.id,
                        "client": row["client_name"],
                        "project": row["project_name"],
                        "status": row["status"],
                        "gst": row["gst_enabled"],
                        "lines": len(row["lines"]),
                        "subtotal_inr": line_total_inr,
                        "total_paise": inv.total_paise,
                        "total_inr": inv.total_paise / 100,
                    }
                )
            else:
                summary["invoices_created"] += 1
                summary["actions"].append(
                    {
                        "sheet": SHEET_INVOICES,
                        "row": row["excel_row"],
                        "action": "create_invoice",
                        "invoice_key": row["invoice_key"],
                        "client": row["client_name"],
                        "project": row["project_name"],
                        "status": row["status"],
                        "gst": row["gst_enabled"],
                        "lines": len(row["lines"]),
                        "subtotal_inr": line_total_inr,
                        "invoice_number": row["invoice_number"] or "(auto)",
                    }
                )

        if apply:
            await db.commit()

    return summary
