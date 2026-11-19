"""Generate docs/ENDPOINT_AUTH_MATRIX.md from FastAPI routes (local, no DB needed)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTERS = ROOT / "app" / "routers"
OUT = ROOT / "docs" / "ENDPOINT_AUTH_MATRIX.md"


def deps_from_source(src: str, func_name: str) -> str:
    # crude: find Depends(...) near def
    m = re.search(rf"async def {re.escape(func_name)}\([\s\S]*?\n\):", src)
    if not m:
        m = re.search(rf"def {re.escape(func_name)}\([\s\S]*?\n\):", src)
    if not m:
        return "?"
    block = m.group(0)
    found = re.findall(r"Depends\(([^)]+)\)", block)
    return ", ".join(found) if found else "(none)"


def main() -> int:
    rows = []
    for path in sorted(ROUTERS.glob("*.py")):
        if path.name.startswith("_"):
            continue
        src = path.read_text(encoding="utf-8")
        prefix_m = re.search(r'APIRouter\(\s*prefix\s*=\s*["\']([^"\']+)["\']', src)
        prefix = prefix_m.group(1) if prefix_m else ""
        for m in re.finditer(
            r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)["\']',
            src,
        ):
            method, sub = m.group(1).upper(), m.group(2)
            # next def
            rest = src[m.end() :]
            dm = re.search(r"async def (\w+)\(|def (\w+)\(", rest)
            if not dm:
                continue
            fname = dm.group(1) or dm.group(2)
            deps = deps_from_source(src, fname)
            roles = ""
            if "require_founder" in deps:
                roles = "founder_ceo"
            elif "require_admin" in deps:
                roles = "admin+"
            elif "get_current_user" in deps:
                roles = "authenticated (role checks in body)"
            elif deps == "(none)":
                roles = "public"
            else:
                roles = deps
            rows.append((method, f"/api/v1{prefix}{sub}", deps, roles, path.name))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Endpoint auth matrix (generated)",
        "",
        "Regenerate: `python scripts/_gen_endpoint_auth_matrix.py`",
        "",
        "| Method | Path | Depends | Allowed roles (summary) | Router |",
        "|--------|------|---------|-------------------------|--------|",
    ]
    for method, path, deps, roles, fname in rows:
        lines.append(f"| {method} | `{path}` | `{deps}` | {roles} | {fname} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), "rows=", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
