"""HTTP API.

Thin: parse, authenticate, call a service, return JSON. Every rule lives in
``pims.services`` so it can be tested without a client, and every response
error carries a code and a correlation id that appears in the logs and in the
support console.

Run with ``uvicorn pims.app:app``; interactive docs at ``/docs``.
"""

from __future__ import annotations

import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, audit, db, health, observability, security
from .config import get_settings
from .errors import AuthError, PimsError
from .integrations import gp_sync, lims_ingest
from .integrations import scale as scale_integration
from .services import (
    alerts, inquiry, inventory, jobs, lims, numbering, orders, prefill, qc, query,
    reference, scan, specs,
)
from .util import utc_now

settings = get_settings()
observability.configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db(settings)
    security.purge_expired_sessions()
    yield
    db.close_connection()


app = FastAPI(
    lifespan=lifespan,
    title="PIMS",
    version=__version__,
    description=(
        "Production Inventory Management System — orders, inventory movement, "
        "quality control, inquiry and reporting for Feed Energy plants. "
        "Replaces the VB.NET WinForms client of the same name."
    ),
)


@app.middleware("http")
async def _observe(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-Id") or uuid.uuid4().hex[:12]
    request.state.correlation_id = correlation_id
    started = utc_now()
    try:
        response = await call_next(request)
    except Exception as exc:                       # pragma: no cover - safety net
        observability.record_error(
            method=request.method,
            path=request.url.path,
            code="unhandled",
            message=str(exc),
            correlation_id=correlation_id,
            traceback_text=traceback.format_exc(),
        )
        raise
    duration_ms = (utc_now() - started).total_seconds() * 1000
    response.headers["X-Correlation-Id"] = correlation_id
    if request.url.path.startswith("/api"):
        observability.record_request(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
        )
    return response


@app.exception_handler(PimsError)
async def _domain_error(request: Request, exc: PimsError) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", "-")
    if exc.status >= 500 or isinstance(exc, PimsError) and exc.status not in (401, 403, 404):
        observability.record_error(
            method=request.method,
            path=request.url.path,
            code=exc.code,
            message=exc.message,
            correlation_id=correlation_id,
        )
    body = exc.as_dict()
    body["correlation_id"] = correlation_id
    return JSONResponse(status_code=exc.status, content=body)


# ------------------------------------------------------------- dependencies


def current_user(
    authorization: str | None = Header(default=None),
    x_pims_token: str | None = Header(default=None),
) -> dict:
    token = x_pims_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise AuthError("Sign in to continue.")
    return security.user_from_token(token)


User = Depends(current_user)


# -------------------------------------------------------------------- auth


@app.post("/api/auth/login", tags=["auth"])
def login(payload: dict = Body(...)) -> dict:
    return security.login(
        (payload.get("username") or "").strip(), payload.get("password") or ""
    )


@app.post("/api/auth/logout", tags=["auth"])
def logout(
    authorization: str | None = Header(default=None),
    x_pims_token: str | None = Header(default=None),
) -> dict:
    token = x_pims_token or (
        authorization[7:].strip()
        if authorization and authorization.lower().startswith("bearer ")
        else None
    )
    if token:
        security.logout(token)
    return {"ok": True}


@app.post("/api/auth/pin", tags=["auth"])
def login_pin(payload: dict = Body(...)) -> dict:
    """Kiosk sign-in with a PIN. Sessions are short by design."""

    return security.login_with_pin(
        (payload.get("username") or "").strip(),
        str(payload.get("pin") or ""),
        payload.get("plant_id"),
    )


@app.get("/api/kiosk/plants", tags=["auth"])
def kiosk_plants() -> list[dict]:
    """Plants a terminal can be assigned to. Unauthenticated: this is what a
    kiosk asks before anyone has signed in, and it is only codes and names."""

    return reference.plants()


@app.get("/api/auth/kiosk-users", tags=["auth"])
def kiosk_users(plant_id: int) -> list[dict]:
    """Operators who can sign in at this plant's terminal. Names only."""

    return security.kiosk_users(plant_id)


@app.get("/api/auth/me", tags=["auth"])
def me(user: dict = User) -> dict:
    return security.public_user(user)


# --------------------------------------------------------------- reference


@app.get("/api/reference", tags=["reference"])
def reference_bundle(plant_id: int | None = None, user: dict = User) -> dict:
    return reference.bundle(plant_id)


@app.get("/api/materials", tags=["reference"])
def materials(plant_id: int | None = None, user: dict = User) -> list[dict]:
    return reference.materials(plant_id)


@app.get("/api/materials/{material_id}", tags=["reference"])
def material(material_id: int, user: dict = User) -> dict:
    return reference.material(material_id)


@app.get("/api/locations", tags=["reference"])
def locations(plant_id: int | None = None, user: dict = User) -> list[dict]:
    return reference.locations(plant_id)


@app.get("/api/requirements/{party_type}/{party_id}", tags=["reference"])
def requirements(party_type: str, party_id: int, user: dict = User) -> list[dict]:
    return reference.requirements(party_type, party_id)


# ------------------------------------------------------------------ orders


@app.get("/api/dashboard", tags=["orders"])
def dashboard(plant_id: int | None = None, user: dict = User) -> dict:
    data = orders.dashboard(plant_id)
    data["out_of_spec_recent"] = len(qc.out_of_spec_report(plant_id, days=14, limit=50))
    data["lims"] = lims.freshness()
    return data


@app.get("/api/orders", tags=["orders"])
def list_orders(
    plant_id: int | None = None,
    order_type_id: int | None = None,
    department_id: int | None = None,
    status_id: int | None = None,
    material_id: int | None = None,
    customer_id: int | None = None,
    vendor_id: int | None = None,
    due_from: str | None = None,
    due_to: str | None = None,
    text: str | None = None,
    open_only: bool = False,
    limit: int = Query(default=200, le=1000),
    offset: int = 0,
    user: dict = User,
) -> dict:
    return orders.search(
        plant_id=plant_id,
        order_type_id=order_type_id,
        department_id=department_id,
        status_id=status_id,
        material_id=material_id,
        customer_id=customer_id,
        vendor_id=vendor_id,
        due_from=due_from,
        due_to=due_to,
        text=text,
        open_only=open_only,
        limit=limit,
        offset=offset,
    )


@app.post("/api/orders", tags=["orders"], status_code=201)
def create_order(payload: dict = Body(...), user: dict = User) -> dict:
    created = orders.create(payload, user)
    return {"created": created, "count": len(created)}


@app.get("/api/orders/{order_id}", tags=["orders"])
def get_order(order_id: int, user: dict = User) -> dict:
    order = orders.get(order_id)
    order["transactions"] = inventory.activity(order_id=order_id, limit=200)
    order["qc"] = qc.list_for_order(order_id)
    order["qa_checklists"] = qc.qa_checklists(order_id)
    order["in_process"] = qc.in_process(order_id)
    order["pending_shipments"] = inventory.pending_shipments(order_id=order_id)
    if order.get("customer_id"):
        order["customer_requirements"] = reference.requirements(
            "customer", order["customer_id"]
        )
    if order.get("vendor_id"):
        order["vendor_requirements"] = reference.requirements("vendor", order["vendor_id"])
    return order


@app.patch("/api/orders/{order_id}", tags=["orders"])
def update_order(order_id: int, payload: dict = Body(...), user: dict = User) -> dict:
    return orders.update(order_id, payload, user)


@app.post("/api/orders/close", tags=["orders"])
def close_orders(payload: dict = Body(...), user: dict = User) -> dict:
    return orders.close(
        [int(i) for i in payload.get("order_ids", [])],
        user,
        force=bool(payload.get("force")),
    )


@app.get("/api/orders/{order_id}/audit", tags=["orders"])
def order_audit(order_id: int, user: dict = User) -> list[dict]:
    return audit.for_entity("order", order_id)


@app.get("/api/orders/{order_id}/bol", tags=["shipping"])
def bill_of_lading(order_id: int, user: dict = User) -> dict:
    return inventory.bill_of_lading(order_id)


# --------------------------------------------------------------- inventory


@app.post("/api/transactions/{operation}", tags=["inventory"], status_code=201)
def post_transaction(
    operation: str, payload: dict = Body(...), user: dict = User
) -> dict:
    return inventory.post(operation, payload, user)


@app.post("/api/transactions/{transaction_id}/void", tags=["inventory"])
def void_transaction(
    transaction_id: int, payload: dict = Body(default={}), user: dict = User
) -> dict:
    return inventory.void(transaction_id, payload.get("reason", ""), user)


@app.get("/api/balances", tags=["inventory"])
def balances(
    plant_id: int | None = None,
    location_id: int | None = None,
    material_id: int | None = None,
    as_of: str | None = None,
    include_zero: bool = False,
    user: dict = User,
) -> list[dict]:
    return inventory.location_balance(
        plant_id=plant_id,
        location_id=location_id,
        material_id=material_id,
        as_of=as_of,
        include_zero=include_zero,
    )


@app.get("/api/activity", tags=["inventory"])
def activity(
    plant_id: int | None = None,
    order_id: int | None = None,
    location_id: int | None = None,
    material_id: int | None = None,
    operation: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_voided: bool = False,
    limit: int = Query(default=500, le=5000),
    user: dict = User,
) -> list[dict]:
    return inventory.activity(
        plant_id=plant_id,
        order_id=order_id,
        location_id=location_id,
        material_id=material_id,
        operation=operation,
        date_from=date_from,
        date_to=date_to,
        include_voided=include_voided,
        limit=limit,
    )


@app.get("/api/shipments/pending", tags=["shipping"])
def pending_shipments(
    plant_id: int | None = None, order_id: int | None = None, user: dict = User
) -> list[dict]:
    return inventory.pending_shipments(plant_id=plant_id, order_id=order_id)


@app.post("/api/shipments/{stage_id}/ship", tags=["shipping"])
def ship(stage_id: int, payload: dict = Body(default={}), user: dict = User) -> dict:
    return inventory.ship(stage_id, user, user_date=payload.get("user_date"))


# ------------------------------------------------- prefill, numbering, scan


@app.get("/api/prefill/qc/{order_id}", tags=["operator"])
def prefill_qc(order_id: int, user: dict = User) -> dict:
    """What the QC form should open with for this order."""

    return prefill.for_qc(order_id)


@app.get("/api/prefill/{operation}", tags=["operator"])
def prefill_operation(
    operation: str,
    order_id: int | None = None,
    plant_id: int | None = None,
    trailer_number: str | None = None,
    user: dict = User,
) -> dict:
    """Suggested values for a plant-floor screen, with why each was suggested."""

    return prefill.for_operation(
        operation, order_id=order_id, plant_id=plant_id, trailer_number=trailer_number
    )


@app.get("/api/trailers/{trailer_number}/history", tags=["operator"])
def trailer_history(trailer_number: str, user: dict = User) -> dict:
    return {
        "trailer_number": trailer_number,
        "last_material_hauled": prefill.last_material_hauled(trailer_number),
        "history": prefill.trailer_history(trailer_number),
    }


@app.get("/api/numbering/preview", tags=["operator"])
def numbering_preview(order_id: int = 0, user: dict = User) -> dict:
    return numbering.preview(order_id)


@app.post("/api/numbering/sample", tags=["operator"])
def numbering_sample(payload: dict = Body(...), user: dict = User) -> dict:
    """Mint a sample number. Deliberately an explicit action — see the runbook."""

    security.require_permission(user, "qc.write")
    order_id = int(payload["order_id"])
    sample_number = numbering.next_sample_number(order_id, payload.get("when"))
    audit.record(
        username=user["username"],
        action="numbering.sample",
        entity="order",
        entity_id=order_id,
        summary=f"Generated sample number {sample_number}",
    )
    return {"order_id": order_id, "sample_number": sample_number}


@app.get("/api/scan", tags=["operator"])
def scan_code(code: str, plant_id: int | None = None, user: dict = User) -> dict:
    """Resolve a scanned barcode (or typed code) to what it refers to."""

    return scan.resolve(code, plant_id)


# ---------------------------------------------------------------------- QC


@app.get("/api/orders/{order_id}/qc", tags=["qc"])
def order_qc(order_id: int, user: dict = User) -> list[dict]:
    return qc.list_for_order(order_id)


@app.post("/api/orders/{order_id}/qc/validate", tags=["qc"])
def validate_qc(order_id: int, payload: dict = Body(default={}), user: dict = User) -> dict:
    return qc.validate(order_id, payload)


@app.post("/api/orders/{order_id}/qc", tags=["qc"], status_code=201)
def create_qc(order_id: int, payload: dict = Body(...), user: dict = User) -> dict:
    return qc.save(
        order_id,
        payload,
        user,
        acknowledge_warnings=bool(payload.get("acknowledge_warnings")),
    )


@app.put("/api/qc/{qc_id}", tags=["qc"])
def update_qc(qc_id: int, payload: dict = Body(...), user: dict = User) -> dict:
    record = qc.get(qc_id)
    return qc.save(
        record["order_id"],
        payload,
        user,
        qc_id=qc_id,
        acknowledge_warnings=bool(payload.get("acknowledge_warnings")),
    )


@app.post("/api/qc/{qc_id}/void", tags=["qc"])
def void_qc(qc_id: int, payload: dict = Body(default={}), user: dict = User) -> dict:
    return qc.void(qc_id, payload.get("reason", ""), user)


@app.get("/api/qc/out-of-spec", tags=["qc"])
def out_of_spec(
    plant_id: int | None = None,
    days: int = 30,
    limit: int = Query(default=100, le=500),
    user: dict = User,
) -> list[dict]:
    return qc.out_of_spec_report(plant_id, days=days, limit=limit)


@app.get("/api/orders/{order_id}/in-process", tags=["qc"])
def in_process(order_id: int, user: dict = User) -> list[dict]:
    return qc.in_process(order_id)


@app.post("/api/orders/{order_id}/in-process", tags=["qc"], status_code=201)
def add_in_process(order_id: int, payload: dict = Body(...), user: dict = User) -> dict:
    return qc.add_in_process(order_id, payload, user)


@app.get("/api/orders/{order_id}/qa-checklist", tags=["qc"])
def qa_checklists(order_id: int, user: dict = User) -> list[dict]:
    return qc.qa_checklists(order_id)


@app.post("/api/orders/{order_id}/qa-checklist", tags=["qc"], status_code=201)
def save_qa_checklist(order_id: int, payload: dict = Body(...), user: dict = User) -> dict:
    return qc.save_qa_checklist(order_id, payload, user)


# ------------------------------------------------------------------- specs


@app.get("/api/specs", tags=["specs"])
def list_specs(
    family: str | None = None, needs_review: bool = False, user: dict = User
) -> list[dict]:
    return specs.list_specs(family=family, needs_review=needs_review or None)


@app.put("/api/specs", tags=["specs"])
def upsert_spec(payload: dict = Body(...), user: dict = User) -> dict:
    security.require_permission(user, "spec.read")
    result = specs.upsert_spec(
        material_id=int(payload["material_id"]),
        analyte=payload["analyte"],
        min_value=payload.get("min_value"),
        max_value=payload.get("max_value"),
        note=payload.get("note", ""),
        source=payload.get("source", "PIMS"),
        needs_review=bool(payload.get("needs_review")),
    )
    audit.record(
        username=user["username"],
        action="spec.update",
        entity="material_spec",
        entity_id=result["spec_id"],
        summary=f"Limit updated for material {payload['material_id']} / {payload['analyte']}",
        detail=payload,
    )
    return result


@app.put("/api/materials/{material_id}/tests", tags=["specs"])
def set_tests(material_id: int, payload: dict = Body(...), user: dict = User) -> dict:
    security.require_permission(user, "spec.read")
    analytes = specs.set_required_tests(material_id, payload.get("analytes", []))
    audit.record(
        username=user["username"],
        action="spec.tests",
        entity="material",
        entity_id=material_id,
        summary=f"Test list set for material {material_id}",
        detail={"analytes": analytes},
    )
    return {"material_id": material_id, "analytes": analytes}


# -------------------------------------------------------------------- LIMS


@app.get("/api/lims/tests", tags=["lims"])
def lims_tests(user: dict = User) -> list[dict]:
    return lims.test_codes()


@app.get("/api/lims/components", tags=["lims"])
def lims_components(test_code: str | None = None, user: dict = User) -> list[dict]:
    return lims.component_names(test_code)


@app.get("/api/lims/sample/{sample_code}", tags=["lims"])
def lims_sample(sample_code: str, user: dict = User) -> dict:
    return {
        "sample_code": sample_code,
        "results": lims.results_for_sample(sample_code),
        "freshness": lims.freshness(),
    }


@app.post("/api/lims/matrix", tags=["lims"])
def lims_matrix(payload: dict = Body(...), user: dict = User) -> dict:
    return lims.matrix(payload.get("sample_codes", []), payload.get("selections"))


@app.post("/api/lims/ingest", tags=["lims"])
def lims_ingest(payload: dict = Body(...), user: dict = User) -> dict:
    security.require_permission(user, "support.read")
    return lims.ingest(payload.get("rows", []), payload.get("source"))


@app.get("/api/lims/freshness", tags=["lims"])
def lims_freshness(user: dict = User) -> dict:
    return lims.freshness()


# ----------------------------------------------------------------- inquiry


@app.post("/api/inquiry/{tab}", tags=["inquiry"])
def run_inquiry(tab: str, payload: dict = Body(default={}), user: dict = User) -> dict:
    return inquiry.run(tab, payload)


@app.post("/api/inquiry/{tab}/csv", tags=["inquiry"])
def inquiry_csv(tab: str, payload: dict = Body(default={}), user: dict = User) -> Response:
    result = inquiry.run(tab, payload)
    return PlainTextResponse(
        inquiry.to_csv(result),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="pims-{tab}.csv"'},
    )


# ------------------------------------------------------------ custom query


@app.get("/api/query/catalogue", tags=["query"])
def query_catalogue(user: dict = User) -> dict:
    return query.catalogue()


@app.post("/api/query/run", tags=["query"])
def query_run(payload: dict = Body(...), user: dict = User) -> dict:
    definition = payload.get("definition", payload)
    return query.run(definition, user, payload.get("prompts"))


@app.post("/api/query/run/csv", tags=["query"])
def query_run_csv(payload: dict = Body(...), user: dict = User) -> Response:
    definition = payload.get("definition", payload)
    result = query.run(definition, user, payload.get("prompts"))
    return PlainTextResponse(
        query.to_csv(result),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="pims-query.csv"'},
    )


@app.get("/api/query/saved", tags=["query"])
def saved_queries(include_inactive: bool = False, user: dict = User) -> list[dict]:
    return query.list_saved(include_inactive)


@app.get("/api/query/saved/{query_id}", tags=["query"])
def saved_query(query_id: int, user: dict = User) -> dict:
    return query.get_saved(query_id)


@app.post("/api/query/saved", tags=["query"], status_code=201)
def save_query(payload: dict = Body(...), user: dict = User) -> dict:
    return query.save(
        payload.get("name", ""),
        payload.get("description", ""),
        payload.get("definition", {}),
        user,
        query_id=payload.get("query_id"),
    )


@app.delete("/api/query/saved/{query_id}", tags=["query"])
def delete_saved_query(query_id: int, user: dict = User) -> dict:
    query.deactivate(query_id, user)
    return {"ok": True}


# ------------------------------------------------- alerts, jobs, integrations


@app.get("/api/alerts", tags=["automation"])
def list_alerts(limit: int = Query(default=50, le=500), user: dict = User) -> dict:
    return {"alerts": alerts.recent(limit), "settings": alerts.settings_summary()}


@app.post("/api/alerts/run", tags=["automation"])
def run_alerts(payload: dict = Body(default={}), user: dict = User) -> dict:
    """Evaluate the alert rules now. ``send=false`` is a dry run."""

    security.require_permission(user, "support.read")
    return alerts.run(payload.get("plant_id"), send=bool(payload.get("send", False)))


@app.post("/api/alerts/{alert_id}/acknowledge", tags=["automation"])
def acknowledge_alert(alert_id: int, user: dict = User) -> dict:
    security.require_permission(user, "support.read")
    return alerts.acknowledge(alert_id, user["username"])


@app.get("/api/jobs", tags=["automation"])
def job_status(user: dict = User) -> dict:
    security.require_permission(user, "support.read")
    return {"jobs": jobs.health_summary(), "recent": jobs.last_runs(20)}


@app.post("/api/jobs/{job}/run", tags=["automation"])
def run_job(job: str, payload: dict = Body(default={}), user: dict = User) -> dict:
    """Run a scheduled job on demand — the same code cron calls."""

    security.require_permission(user, "support.read")
    plant_id = payload.get("plant_id")
    dry_run = bool(payload.get("dry_run", False))
    if job == "daily":
        return jobs.daily(plant_id, send=not dry_run)
    if job == "alerts":
        return alerts.run(plant_id, send=not dry_run)
    if job == "auto-close":
        return jobs.auto_close(plant_id, dry_run=dry_run)
    if job == "recurring":
        return jobs.run_recurring(dry_run=dry_run)
    if job == "lims-sync":
        return lims_ingest.sync(
            since_days=int(payload.get("since_days", 2)), dry_run=dry_run
        )
    if job == "gp-sync":
        return gp_sync.sync(dry_run=dry_run)
    raise PimsError(f"Unknown job {job!r}.", allowed=[
        "daily", "alerts", "auto-close", "recurring", "lims-sync", "gp-sync",
    ])


@app.get("/api/recurring", tags=["automation"])
def list_recurring(include_inactive: bool = False, user: dict = User) -> list[dict]:
    return jobs.list_recurring(include_inactive)


@app.post("/api/recurring", tags=["automation"], status_code=201)
def create_recurring(payload: dict = Body(...), user: dict = User) -> dict:
    return jobs.create_recurring(payload, user)


@app.delete("/api/recurring/{recurring_id}", tags=["automation"])
def stop_recurring(recurring_id: int, user: dict = User) -> dict:
    security.require_permission(user, "order.write")
    jobs.deactivate_recurring(recurring_id, user)
    return {"ok": True}


@app.post("/api/scale/readings", tags=["automation"], status_code=201)
def post_scale_reading(payload: dict = Body(...), user: dict = User) -> dict:
    """Where the plant scale agent posts a weight."""

    security.require_permission(user, "txn.post")
    return scale_integration.record(payload, user)


@app.get("/api/scale/latest", tags=["automation"])
def latest_scale_reading(
    plant_id: int,
    trailer_number: str | None = None,
    max_age_minutes: int = 120,
    user: dict = User,
) -> dict:
    reading = scale_integration.latest(plant_id, trailer_number, max_age_minutes)
    return {"reading": reading, "recent": scale_integration.recent(plant_id, 10)}


# ----------------------------------------------------------------- support


@app.get("/api/health", tags=["support"])
def health_endpoint() -> dict:
    """Unauthenticated liveness probe — safe for a load balancer."""

    return health.liveness()


@app.get("/api/support/diagnostics", tags=["support"])
def diagnostics(user: dict = User) -> dict:
    security.require_permission(user, "support.read")
    report = health.diagnostics()
    report["jobs"] = jobs.health_summary()
    report["alerting"] = alerts.settings_summary()
    return report


@app.get("/api/support/data-quality", tags=["support"])
def data_quality(plant_id: int | None = None, user: dict = User) -> dict:
    security.require_permission(user, "support.read")
    return health.data_quality(plant_id)


@app.get("/api/support/audit", tags=["support"])
def audit_trail(
    entity: str | None = None,
    entity_id: str | None = None,
    username: str | None = None,
    limit: int = Query(default=100, le=1000),
    user: dict = User,
) -> list[dict]:
    security.require_permission(user, "support.read")
    if entity and entity_id:
        return audit.for_entity(entity, entity_id, limit)
    return audit.recent(limit, username)


@app.get("/api/support/errors", tags=["support"])
def recent_errors(limit: int = 50, user: dict = User) -> dict:
    security.require_permission(user, "support.read")
    return {
        "errors": observability.recent_errors(limit),
        "slow_requests": observability.slow_requests(20),
        "counters": observability.counters(),
    }


# ------------------------------------------------------------- static site


def _mount_web() -> None:
    dist = settings.web_dist
    if not dist.exists():
        return

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> Any:
        return FileResponse(dist / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Any:
        candidate = dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


_mount_web()
