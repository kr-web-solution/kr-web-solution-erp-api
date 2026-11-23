"""Payslip Jinja context — amounts are precomputed paise formatted in Python."""

from __future__ import annotations

from typing import Any, Optional

from ...pdf import format_inr
from .. import asset_manager
from ..renderer import apply_page_defaults


def build_payslip_context(
    *,
    employee_name: str,
    employee_email: str,
    job_title: Optional[str],
    department: Optional[str],
    month_key: str,
    gross_paise: int,
    deductions_paise: int,
    net_paise: int,
    note: Optional[str],
    company_name: str,
    company_address: Optional[str] = None,
    company_logo_url: Optional[str] = None,
    paper_size: Optional[str] = "a4",
    orientation: Optional[str] = "portrait",
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "doc_title": "PAYSLIP",
        "company_name": company_name or "KR Web Solution",
        "company_address": (company_address or "").strip() or None,
        "logo_uri": asset_manager.file_data_uri(
            asset_manager.logo_path(company_logo_url)
        ),
        "employee_name": employee_name,
        "employee_email": employee_email or "",
        "job_title": job_title,
        "department": department,
        "month_key": month_key,
        "gross_fmt": format_inr(int(gross_paise or 0)),
        "deductions_fmt": format_inr(int(deductions_paise or 0)),
        "net_fmt": format_inr(int(net_paise or 0)),
        "note": (note or "").strip() or None,
        "footer_meta": company_name or "KR Web Solution",
        "watermark": None,
        "is_preview": False,
    }
    return apply_page_defaults(ctx, paper_size=paper_size, orientation=orientation)
