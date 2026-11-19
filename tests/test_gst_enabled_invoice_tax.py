"""GST enabled: invoice create must persist the real computed tax amount."""

from fastapi.testclient import TestClient


def test_invoice_tax_when_gst_enabled(client: TestClient, founder_cookies):
    cookies = founder_cookies

    # Enable GST at 18%
    st = client.put(
        "/api/v1/settings",
        json={"gst_enabled": True, "default_tax_rate": 18},
        cookies=cookies,
    )
    assert st.status_code == 200
    body = st.json()
    assert body["gst_enabled"] is True

    # Minimal client + project
    c = client.post(
        "/api/v1/clients",
        json={"name": "GST Tax Client", "email": "gst-tax@example.com"},
        cookies=cookies,
    )
    assert c.status_code == 201
    cid = c.json()["id"]

    p = client.post(
        "/api/v1/projects",
        json={"name": "GST Tax Project", "client_ids": [cid], "budget_paise": 0},
        cookies=cookies,
    )
    assert p.status_code == 201
    pid = p.json()["id"]

    # 10_000_00 paise @ 18% → tax 1_800_00
    inv = client.post(
        "/api/v1/invoices",
        json={
            "invoice_type": "project",
            "client_id": cid,
            "project_id": pid,
            "issue_date": "2026-11-19",
            "due_date": "2026-12-03",
            "tax_rate_percent": 18.0,
            "items": [
                {
                    "description": "Taxable line",
                    "quantity": 1.0,
                    "unit_rate_paise": 10_000_00,
                }
            ],
        },
        cookies=cookies,
    )
    assert inv.status_code == 201, inv.text
    data = inv.json()
    assert data["subtotal_paise"] == 10_000_00
    assert data["tax_rate_percent"] == 18.0
    assert data["tax_paise"] == 1_800_00
    assert data["total_paise"] == 11_800_00

    # Restore GST off so other tests keep default behaviour
    client.put(
        "/api/v1/settings",
        json={"gst_enabled": False},
        cookies=cookies,
    )
