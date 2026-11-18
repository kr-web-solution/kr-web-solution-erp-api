"""Canonical roles + party types for KR Workspace."""

from typing import Any

# party_type on users / roles: who this person is in the business
PARTY_STAFF = "staff"
PARTY_CLIENT = "client"
PARTY_LEAD = "lead"

PARTY_TYPES = (PARTY_STAFF, PARTY_CLIENT, PARTY_LEAD)

# Access role codes (roles.code / users.role)
ROLE_ADMIN = "admin"
ROLE_FOUNDER = "founder_ceo"
ROLE_WORKER = "worker"
ROLE_CLIENT = "client"
ROLE_LEAD = "lead"

ROLE_CODES = (ROLE_ADMIN, ROLE_FOUNDER, ROLE_WORKER, ROLE_CLIENT, ROLE_LEAD)

# Seed rows for roles table (stable IDs)
ROLE_SEED: list[dict[str, Any]] = [
    {
        "id": "rol_admin",
        "code": ROLE_ADMIN,
        "name": "Admin",
        "description": "Full workspace access (same menus as Founder).",
        "party_type": PARTY_STAFF,
        "is_system": True,
        "sort_order": 10,
    },
    {
        "id": "rol_founder",
        "code": ROLE_FOUNDER,
        "name": "Founder CEO",
        "description": "Owner / founder — full administration.",
        "party_type": PARTY_STAFF,
        "is_system": True,
        "sort_order": 20,
    },
    {
        "id": "rol_worker",
        "code": ROLE_WORKER,
        "name": "Worker",
        "description": "Internal staff — assigned projects and own work.",
        "party_type": PARTY_STAFF,
        "is_system": True,
        "sort_order": 30,
    },
    {
        "id": "rol_client",
        "code": ROLE_CLIENT,
        "name": "Client",
        "description": "Verified client portal login — linked to a client company.",
        "party_type": PARTY_CLIENT,
        "is_system": True,
        "sort_order": 40,
    },
    {
        "id": "rol_lead",
        "code": ROLE_LEAD,
        "name": "Lead",
        "description": "Lead contact — optional portal; linked to a lead record.",
        "party_type": PARTY_LEAD,
        "is_system": True,
        "sort_order": 50,
    },
]

ROLE_ID_BY_CODE = {r["code"]: r["id"] for r in ROLE_SEED}
PARTY_BY_ROLE = {r["code"]: r["party_type"] for r in ROLE_SEED}


def party_type_for_role(role_code: str) -> str:
    return PARTY_BY_ROLE.get(role_code, PARTY_STAFF)


def role_id_for_code(role_code: str) -> str:
    if role_code not in ROLE_ID_BY_CODE:
        raise ValueError(f"Unknown role code: {role_code}")
    return ROLE_ID_BY_CODE[role_code]
