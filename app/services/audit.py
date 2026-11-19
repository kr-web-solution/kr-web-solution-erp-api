"""Global audit log — write diffs; read restricted to founder/admin."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog

logger = logging.getLogger("kr.audit")

SECRET_KEYS = {
    "password",
    "password_hash",
    "smtp_password_encrypted",
    "smtp_password",
    "ENCRYPTION_KEY",
    "SECRET_KEY",
    "access_token",
}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k not in SECRET_KEYS}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def snapshot_entity(obj: Any, fields: Optional[list[str]] = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    table = getattr(obj, "__table__", None)
    if table is None:
        return data
    cols = fields or [c.name for c in table.columns]
    for name in cols:
        if name in SECRET_KEYS:
            continue
        if hasattr(obj, name):
            data[name] = _jsonable(getattr(obj, name))
    return data


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> tuple[list[str], dict, dict]:
    changed: list[str] = []
    b_out: dict[str, Any] = {}
    a_out: dict[str, Any] = {}
    keys = set(before) | set(after)
    for k in sorted(keys):
        if k in SECRET_KEYS:
            continue
        bv, av = before.get(k), after.get(k)
        if bv != av:
            changed.append(k)
            b_out[k] = bv
            a_out[k] = av
    return changed, b_out, a_out


def _resolve_actor_id(actor: Any = None, actor_id: Optional[str] = None) -> Optional[str]:
    if actor_id:
        return str(actor_id)
    if actor is None:
        return None
    if isinstance(actor, str):
        return actor
    ident = getattr(actor, "id", None)
    return str(ident) if ident else None


async def write_audit(
    db: AsyncSession,
    actor: Any = None,
    entity_type: str = "",
    entity_id: str = "",
    action: str = "",
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    request: Optional[Request] = None,
    *,
    actor_id: Optional[str] = None,
    changed_fields: Optional[list[str]] = None,
    note: Optional[str] = None,
) -> Optional[AuditLog]:
    """Best-effort audit row. Nested savepoint so failures never abort the main txn."""
    if note:
        after = dict(after or {})
        after.setdefault("note", note)
    if changed_fields is None and isinstance(after, dict) and isinstance(before, dict):
        changed_fields = [k for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]
    elif changed_fields is None and isinstance(after, dict) and not before:
        changed_fields = list(after.keys())

    ip = None
    ua = None
    if request is not None:
        ip = request.client.host if request.client else None
        ua = (request.headers.get("user-agent") or "")[:255] or None

    row = AuditLog(
        actor_id=_resolve_actor_id(actor, actor_id),
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else "",
        action=(action or "update")[:20],
        changed_fields=changed_fields or [],
        before=_jsonable(before) if before is not None else None,
        after=_jsonable(after) if after is not None else None,
        ip=ip,
        user_agent=ua,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return row
    except Exception:
        logger.exception(
            "audit write failed entity=%s id=%s action=%s",
            entity_type,
            entity_id,
            action,
        )
        return None


async def audit_create(
    db: AsyncSession,
    obj: Any,
    *,
    actor_id: Optional[str] = None,
    request: Optional[Request] = None,
) -> AuditLog:
    after = snapshot_entity(obj)
    return await write_audit(
        db,
        entity_type=obj.__tablename__,
        entity_id=str(obj.id),
        action="create",
        actor_id=actor_id,
        after=after,
        changed_fields=list(after.keys()),
        request=request,
    )


async def audit_update(
    db: AsyncSession,
    obj: Any,
    before: dict[str, Any],
    *,
    actor_id: Optional[str] = None,
    request: Optional[Request] = None,
) -> Optional[AuditLog]:
    after_full = snapshot_entity(obj)
    changed, b, a = diff_snapshots(before, after_full)
    if not changed:
        return None
    return await write_audit(
        db,
        entity_type=obj.__tablename__,
        entity_id=str(obj.id),
        action="update",
        actor_id=actor_id,
        before=b,
        after=a,
        changed_fields=changed,
        request=request,
    )


async def audit_soft_delete(
    db: AsyncSession,
    obj: Any,
    *,
    actor_id: Optional[str] = None,
    request: Optional[Request] = None,
) -> AuditLog:
    return await write_audit(
        db,
        entity_type=obj.__tablename__,
        entity_id=str(obj.id),
        action="soft_delete",
        actor_id=actor_id,
        before=snapshot_entity(obj, ["deleted_at", "id"]),
        after={"deleted_at": _jsonable(getattr(obj, "deleted_at", None))},
        changed_fields=["deleted_at"],
        request=request,
    )
