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
     "/reports/export.xlsx", "/reports/export.csv", "/api/v1/me", "/api/v1/results/monthly?month=2026-03"],
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
