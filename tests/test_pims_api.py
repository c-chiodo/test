"""End-to-end API behaviour, including authentication and permissions."""

from __future__ import annotations

from pims.seed import DEMO_PASSWORD


def test_health_is_public(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["service"] == "pims"


def test_api_requires_a_token(client):
    assert client.get("/api/orders").status_code == 401


def test_bad_credentials_are_rejected(client):
    response = client.post(
        "/api/auth/login", json={"username": "cchiodo", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


def test_login_returns_the_user_and_their_plants(client):
    response = client.post(
        "/api/auth/login", json={"username": "rprice", "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "qc"
    assert {p["code"] for p in body["user"]["plants"]} == {"DM", "SC", "PJ"}
    assert "password_hash" not in body["user"]


def test_reference_bundle_has_everything_the_ui_needs(client, auth):
    body = client.get("/api/reference", headers=auth).json()
    for key in ("plants", "materials", "locations", "customers", "order_types", "analytes"):
        assert body[key], key


def test_order_lifecycle_over_http(client, auth):
    materials = client.get("/api/materials?plant_id=1", headers=auth).json()
    av4000 = next(m for m in materials if m["number"] == "05001")

    created = client.post(
        "/api/orders",
        headers=auth,
        json={
            "order_type_id": 2,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 3,
            "order_date": "2026-08-17",
            "due_date": "2026-08-19",
            "material_one_id": av4000["material_id"],
            "material_one_quantity": 12_000,
            "comments": "api lifecycle test",
        },
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["created"][0]["order_id"]

    detail = client.get(f"/api/orders/{order_id}", headers=auth).json()
    assert detail["material_one_number"] == "05001"
    assert detail["status"] == "Open"

    patched = client.patch(
        f"/api/orders/{order_id}", headers=auth, json={"material_one_quantity": 15_000}
    )
    assert patched.status_code == 200
    assert patched.json()["material_one_quantity"] == 15_000

    trail = client.get(f"/api/orders/{order_id}/audit", headers=auth).json()
    assert [entry["action"] for entry in trail][-1] == "create"
    assert trail[0]["detail"]["changes"]["material_one_quantity"]["to"] == 15_000

    closed = client.post(
        "/api/orders/close", headers=auth, json={"order_ids": [order_id]}
    ).json()
    assert closed["closed"] == [order_id]


def test_invalid_order_returns_field_level_errors(client, auth):
    response = client.post(
        "/api/orders",
        headers=auth,
        json={"order_type_id": 1, "plant_id": 1, "company_id": 1, "due_date": "2026-08-19"},
    )
    assert response.status_code == 422
    fields = response.json()["detail"]["fields"]
    assert "material_one_id" in fields
    assert "customer_id" in fields          # a sales order needs a customer
    assert response.json()["correlation_id"]


def test_qc_validate_endpoint_reflects_the_product(client, auth):
    materials = client.get("/api/materials?plant_id=1", headers=auth).json()
    cattle = next(m for m in materials if m["number"] == "01020")

    order_id = client.post(
        "/api/orders",
        headers=auth,
        json={
            "order_type_id": 3,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 1,
            "order_date": "2026-08-17",
            "due_date": "2026-08-19",
            "vendor_id": 1,
            "material_one_id": cattle["material_id"],
            "material_one_quantity": 9_000,
        },
    ).json()["created"][0]["order_id"]

    check = client.post(
        f"/api/orders/{order_id}/qc/validate",
        headers=auth,
        json={"sample_number": "DM9D260817Q9", "moisture": 45, "ph": 3.1, "tfa": 14},
    ).json()
    assert check["required_tests"] == ["moisture", "ph", "tfa"]
    assert check["warnings"] == []
    assert check["summary"]["status"] == "in_spec"

    saved = client.post(
        f"/api/orders/{order_id}/qc",
        headers=auth,
        json={"sample_number": "DM9D260817Q9", "moisture": 45, "ph": 3.1, "tfa": 14},
    )
    assert saved.status_code == 201
    assert saved.json()["spec_summary"]["status"] == "in_spec"


def test_out_of_spec_result_is_flagged_not_blocked(client, auth):
    materials = client.get("/api/materials?plant_id=1", headers=auth).json()
    tallow = next(m for m in materials if m["number"] == "05005")   # FFA max 30

    order_id = client.post(
        "/api/orders",
        headers=auth,
        json={
            "order_type_id": 3,
            "plant_id": 1,
            "company_id": 1,
            "department_id": 1,
            "order_date": "2026-08-17",
            "due_date": "2026-08-19",
            "vendor_id": 2,
            "material_one_id": tallow["material_id"],
            "material_one_quantity": 8_000,
        },
    ).json()["created"][0]["order_id"]

    saved = client.post(
        f"/api/orders/{order_id}/qc",
        headers=auth,
        json={
            "sample_number": "DM8D260817Q8",
            "moisture": 1.2,
            "temp": 120,
            "spintest_fallout": 0.4,
            "ffa": 44,
            "tfa": 95,
            "acknowledge_warnings": True,
        },
    )
    assert saved.status_code == 201
    assert "ffa" in saved.json()["spec_summary"]["out_of_spec"]


def test_operator_cannot_edit_orders_but_can_post_movements(client):
    login = client.post(
        "/api/auth/login", json={"username": "toperator", "password": DEMO_PASSWORD}
    ).json()
    headers = {"Authorization": f"Bearer {login['token']}"}

    denied = client.post(
        "/api/orders",
        headers=headers,
        json={
            "order_type_id": 2,
            "plant_id": 1,
            "company_id": 1,
            "due_date": "2026-08-19",
            "material_one_id": 1,
            "material_one_quantity": 100,
        },
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "forbidden"

    balances = client.get("/api/balances?plant_id=1", headers=headers)
    assert balances.status_code == 200


def test_support_endpoints_are_role_gated(client, auth):
    operator = client.post(
        "/api/auth/login", json={"username": "toperator", "password": DEMO_PASSWORD}
    ).json()
    operator_headers = {"Authorization": f"Bearer {operator['token']}"}
    assert client.get("/api/support/diagnostics", headers=operator_headers).status_code == 403

    diagnostics = client.get("/api/support/diagnostics", headers=auth)
    assert diagnostics.status_code == 200
    body = diagnostics.json()
    assert set(body["checks"]) == {
        "database",
        "lims",
        "sessions",
        "errors",
        "product_setup",
    }
    assert body["status"] in {"ok", "degraded", "failed"}


def test_diagnostics_never_leaks_a_password(client, auth, monkeypatch):
    from pims import health

    assert health._redact("mssql://pims:s3cret@FESQLPROD01/ProductionData") == (
        "mssql://pims:***@FESQLPROD01/ProductionData"
    )
    body = client.get("/api/support/diagnostics", headers=auth).json()
    assert "s3cret" not in str(body)


def test_data_quality_reports_actionable_findings(client, auth):
    body = client.get("/api/support/data-quality", headers=auth).json()
    keys = {f["key"] for f in body["findings"]}
    assert {"negative_balance", "stale_loads", "unmatched_samples"} <= keys
    assert all("action" in f for f in body["findings"])


def test_inquiry_tabs_and_csv_export(client, auth):
    result = client.post(
        "/api/inquiry/balance", headers=auth, json={"plant_id": 1}
    ).json()
    assert result["row_count"] > 0
    assert result["columns"][0]["name"] == "plant_code"

    csv = client.post("/api/inquiry/balance/csv", headers=auth, json={"plant_id": 1})
    assert csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0].startswith("plant_code,")

    assert client.post("/api/inquiry/nonsense", headers=auth, json={}).status_code == 422


def test_correlation_id_is_returned_on_every_response(client, auth):
    response = client.get("/api/health", headers={"X-Correlation-Id": "trace-me-123"})
    assert response.headers["X-Correlation-Id"] == "trace-me-123"


def test_openapi_documents_the_api(client):
    spec = client.get("/openapi.json").json()
    assert "/api/orders" in spec["paths"]
    assert "/api/transactions/{operation}" in spec["paths"]
