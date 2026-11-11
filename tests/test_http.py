import re

import pytest
from fastapi.testclient import TestClient

from rto.demo import seed_demo


@pytest.fixture()
def client(db):
    seed_demo(db)
    db.commit()
    from rto.main import create_app

    return TestClient(create_app())


def login(client, email):
    r = client.get("/login")
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    r = client.post("/login/dev", data={"csrf_token": tok, "email": email, "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    # the session (and its CSRF token) is renewed at sign-in
    r = client.get("/requests/new")
    return re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)


def test_anonymous_is_redirected_and_api_401(client):
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/healthz").text == "ok"
    assert client.get("/readyz").status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/", "/team", "/reports", "/requests", "/requests/new", "/admin", "/admin/thresholds", "/admin/people",
     "/admin/org", "/admin/roles", "/admin/calendar", "/admin/imports", "/admin/months", "/admin/audit",
     "/reports/export.xlsx", "/reports/export.csv", "/help", "/reports?view=flat", "/reports?month=2026-03&failed=1", "/org", "/org?month=2026-01", "/admin/imports/samples/synthetic/masterlist_risk.csv", "/api/v1/me", "/api/v1/results/monthly?month=2026-03"],
)
def test_hr_admin_pages(client, path):
    login(client, "hr.admin@example.com")
    assert client.get(path).status_code == 200


def test_employee_cannot_open_admin_or_other_people(client):
    login(client, "hazel.ramos@example.com")
    assert client.get("/admin").status_code == 403
    assert client.get("/admin/thresholds").status_code == 403
    r = client.get("/api/v1/results/monthly?month=2026-03")
    assert [x["employee_number"] for x in r.json()] == ["900008"]
    other = client.get("/api/v1/me").json()["id"] + 1
    assert client.get(f"/employees/{other}").status_code in (403, 404)


def test_post_without_csrf_is_rejected(client):
    login(client, "hazel.ramos@example.com")
    r = client.post("/requests", data={"employee_id": 1, "type": "EXEMPTION", "start_date": "2026-03-02",
                                       "category": "Other"})
    assert r.status_code == 403


def test_create_request_via_form_and_api(client):
    tok = login(client, "hazel.ramos@example.com")
    me = client.get("/api/v1/me").json()
    r = client.post("/requests", data={"csrf_token": tok, "employee_id": me["id"], "type": "EXEMPTION",
                                       "start_date": "2026-03-02", "end_date": "2026-03-08",
                                       "days_per_week": "1", "category": "Medical", "reason": ""},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/requests/")
    r = client.post("/api/v1/requests", headers={"X-CSRF-Token": tok},
                    json={"employee_id": me["id"], "type": "DISPUTE", "start_date": "2026-03-10",
                          "credited_dates": ["2026-03-10"], "category": "Door access / system issue"})
    assert r.status_code == 201, r.text
    assert r.json()["steps"][0]["decision"] == "PENDING"


def test_dev_bearer_token_for_api(client):
    r = client.get("/api/v1/me", headers={"Authorization": "Bearer dev:pp.risk@example.com"})
    assert r.status_code == 200 and "PEOPLE_PARTNER" in r.json()["roles"]


def test_security_headers(client):
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_demo_gate(db, monkeypatch):
    from rto.config import get_settings

    monkeypatch.setenv("RTO_DEMO_PASSWORD", "open-sesame-42")
    get_settings.cache_clear()
    try:
        from rto.main import create_app

        c = TestClient(create_app())
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/demo-gate")
        assert c.get("/api/v1/me").status_code == 401
        assert c.get("/healthz").status_code == 200
        page = c.get("/demo-gate")
        tok = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        bad = c.post("/demo-gate", data={"csrf_token": tok, "password": "wrong", "next": "/"}, follow_redirects=False)
        assert "error=1" in bad.headers["location"]
        ok = c.post("/demo-gate", data={"csrf_token": tok, "password": "open-sesame-42", "next": "/"}, follow_redirects=False)
        assert ok.headers["location"] == "/"
        # past the gate: normal sign-in flow, and the gate stays open after sign-in
        assert c.get("/", follow_redirects=False).headers["location"].startswith("/login")
        assert c.get("/login").status_code == 200
    finally:
        get_settings.cache_clear()


def test_render_style_database_url_is_normalised():
    from rto.config import Settings

    s = Settings(database_url="postgres://u:p@h:5432/db")
    assert s.database_url == "postgresql+psycopg://u:p@h:5432/db"


def test_staging_dev_auth_requires_demo_password():
    from rto.config import Settings

    with pytest.raises(ValueError):
        Settings(environment="staging", auth_mode="dev", demo_password="")


def test_sample_download_is_whitelisted(client):
    login(client, "hr.admin@example.com")
    assert client.get("/admin/imports/samples/../pyproject.toml").status_code in (400, 404)
    login(client, "hazel.ramos@example.com")
    assert client.get("/admin/imports/samples/synthetic/masterlist_risk.csv").status_code == 403
    assert client.get("/help").status_code == 200


def test_untracked_exco_has_no_results(client, db):
    from sqlalchemy import select

    from rto.models import Employee, MonthlyResult

    paolo = db.scalar(select(Employee).where(Employee.email == "paolo.reyes@example.com"))
    assert paolo.in_scope is False
    assert db.scalar(select(MonthlyResult).where(MonthlyResult.employee_id == paolo.id)) is None
