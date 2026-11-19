"""F-029: UPI QR file requires authentication."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.models import User
from main import app


def _ensure_dummy_qr() -> str:
    brand = Path(__file__).resolve().parents[1] / "uploads" / "brand"
    brand.mkdir(parents=True, exist_ok=True)
    name = "upi-qr-testfixture.png"
    path = brand / name
    if not path.exists():
        path.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
                "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
            )
        )
    return name


@pytest.fixture
def authed_client():
    """TestClient with get_current_user overridden (avoids seed password coupling)."""

    async def _fake_user() -> User:
        return User(
            id="usr_test_f029",
            email="tester@example.com",
            password_hash="x",
            full_name="Test User",
            role="founder_ceo",
            party_type="staff",
            is_active=True,
        )

    app.dependency_overrides[get_current_user] = _fake_user
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_current_user, None)


def test_upi_qr_requires_auth(client: TestClient):
    name = _ensure_dummy_qr()
    res = client.get(f"/api/v1/settings/upi-qr-file/{name}")
    assert res.status_code in (401, 403)


def test_upi_qr_ok_with_auth(authed_client: TestClient):
    name = _ensure_dummy_qr()
    res = authed_client.get(f"/api/v1/settings/upi-qr-file/{name}")
    assert res.status_code == 200
    cc = res.headers.get("cache-control", "").lower()
    assert "private" in cc
    assert "no-store" in cc


def test_upi_qr_path_traversal_is_404(authed_client: TestClient):
    res = authed_client.get("/api/v1/settings/upi-qr-file/../settings.py")
    assert res.status_code == 404


def test_upi_qr_missing_file_is_404(authed_client: TestClient):
    res = authed_client.get("/api/v1/settings/upi-qr-file/upi-qr-does-not-exist.png")
    assert res.status_code == 404
