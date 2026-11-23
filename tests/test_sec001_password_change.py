"""SEC-001: password change on PATCH /auth/me requires current_password."""

from fastapi.testclient import TestClient

from tests.conftest import TEST_PASSWORD, TEST_USERS


def _patch_me(client: TestClient, cookies, payload: dict):
    return client.patch("/api/v1/auth/me", json=payload, cookies=cookies)


def test_password_change_success_with_current(client: TestClient, founder_cookies):
    spec = TEST_USERS["founder"]
    new_password = "NewSecure!2026"
    res = _patch_me(
        client,
        founder_cookies,
        {"password": new_password, "current_password": spec["password"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "password_hash" not in body
    assert "password" not in body

    # New password works
    login = client.post(
        "/api/v1/auth/login",
        json={"email": spec["email"], "password": new_password},
    )
    assert login.status_code == 200

    # Restore fixture password so later tests keep working
    restore = _patch_me(
        client,
        login.cookies,
        {"password": TEST_PASSWORD, "current_password": new_password},
    )
    assert restore.status_code == 200, restore.text


def test_password_change_incorrect_current(client: TestClient, founder_cookies):
    res = _patch_me(
        client,
        founder_cookies,
        {"password": "AnotherPass!99", "current_password": "WrongCurrent!"},
    )
    assert res.status_code == 400
    assert "current password" in res.json()["detail"].lower()


def test_password_change_missing_current(client: TestClient, founder_cookies):
    res = _patch_me(client, founder_cookies, {"password": "AnotherPass!99"})
    assert res.status_code == 422


def test_password_change_weak_new(client: TestClient, founder_cookies):
    res = _patch_me(
        client,
        founder_cookies,
        {"password": "short", "current_password": TEST_PASSWORD},
    )
    assert res.status_code == 400
    assert "8" in res.json()["detail"]


def test_profile_update_without_password(client: TestClient, founder_cookies):
    res = _patch_me(client, founder_cookies, {"full_name": "Test Founder Updated"})
    assert res.status_code == 200, res.text
    assert res.json()["full_name"] == "Test Founder Updated"
    assert "password_hash" not in res.json()

    # Restore name
    restore = _patch_me(
        client, founder_cookies, {"full_name": TEST_USERS["founder"]["full_name"]}
    )
    assert restore.status_code == 200


def test_password_change_unauthenticated(client: TestClient):
    res = client.patch(
        "/api/v1/auth/me",
        json={"password": "AnotherPass!99", "current_password": TEST_PASSWORD},
    )
    assert res.status_code == 401
