"""Shared list pagination helpers (Stage 3 contract)."""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
DEFAULT_OPTIONS_LIMIT = 50
MAX_OPTIONS_LIMIT = 100


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class PaginatedEnvelope(BaseModel):
    items: list[Any]
    pagination: PaginationMeta


class OptionItem(BaseModel):
    id: str
    label: str
    meta: dict[str, Any] = Field(default_factory=dict)


class OptionsEnvelope(BaseModel):
    items: list[OptionItem]


def clamp_page(page: Optional[int]) -> int:
    try:
        p = int(page or 1)
    except (TypeError, ValueError):
        p = 1
    return max(1, p)


def clamp_page_size(page_size: Optional[int], *, default: int = DEFAULT_PAGE_SIZE) -> int:
    try:
        size = int(page_size if page_size is not None else default)
    except (TypeError, ValueError):
        size = default
    return max(1, min(MAX_PAGE_SIZE, size))


def clamp_options_limit(limit: Optional[int]) -> int:
    try:
        n = int(limit if limit is not None else DEFAULT_OPTIONS_LIMIT)
    except (TypeError, ValueError):
        n = DEFAULT_OPTIONS_LIMIT
    return max(1, min(MAX_OPTIONS_LIMIT, n))


def pagination_meta(*, page: int, page_size: int, total_items: int) -> PaginationMeta:
    total_items = max(0, int(total_items))
    total_pages = max(1, math.ceil(total_items / page_size)) if total_items else 1
    page = min(page, total_pages) if total_items else 1
    return PaginationMeta(
        page=page,
        page_size=page_size,
        total_items=total_items,
        total_pages=total_pages,
        has_next=page < total_pages and total_items > 0,
        has_previous=page > 1 and total_items > 0,
    )


def offset_for(page: int, page_size: int) -> int:
    return (max(1, page) - 1) * page_size


def build_envelope(items: Sequence[T], *, page: int, page_size: int, total_items: int) -> dict:
    meta = pagination_meta(page=page, page_size=page_size, total_items=total_items)
    return {"items": list(items), "pagination": meta.model_dump()}
