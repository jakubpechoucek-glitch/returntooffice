"""Load test: managers browsing team/report pages and employees filing requests.

    pip install locust
    RTO_AUTH_MODE=dev (non-production) ... then:
    locust -f tests/load/locustfile.py --host http://localhost:8000 -u 400 -r 20

Uses the dev bearer token (``Bearer dev:<email>``), so it only works against non-production
environments. Against staging with Entra ID, replace ``_auth`` with a client-credentials token.
"""

import random

from locust import HttpUser, between, task

MANAGERS = ["nina.cabrera@example.com", "marco.valdez@example.com", "daphne.lim@example.com",
            "andrea.agustin@example.com", "benjo.herrera@example.com"]
EMPLOYEES = ["hazel.ramos@example.com", "ivan.domingo@example.com", "julia.aquino@example.com",
             "kevin.mercado@example.com", "lara.bautista@example.com"]


class Manager(HttpUser):
    weight = 1
    wait_time = between(1, 4)

    def on_start(self):
        self.h = {"Authorization": f"Bearer dev:{random.choice(MANAGERS)}"}

    @task(3)
    def team(self):
        self.client.get("/api/v1/team?month=2026-03", headers=self.h)

    @task(2)
    def report(self):
        self.client.get("/api/v1/results/monthly?month=2026-03", headers=self.h)

    @task(1)
    def inbox(self):
        self.client.get("/api/v1/requests?scope=inbox", headers=self.h)


class Employee(HttpUser):
    weight = 4
    wait_time = between(2, 6)

    def on_start(self):
        self.h = {"Authorization": f"Bearer dev:{random.choice(EMPLOYEES)}"}
        self.me = self.client.get("/api/v1/me", headers=self.h).json()

    @task(4)
    def my_results(self):
        self.client.get(f"/api/v1/employees/{self.me['id']}/years/2026", headers=self.h)

    @task(1)
    def file_exemption(self):
        self.client.post("/api/v1/requests", headers=self.h, json={
            "employee_id": self.me["id"], "type": "EXEMPTION", "start_date": "2026-03-16",
            "end_date": "2026-03-22", "days_per_week": 1, "category": "Other", "reason": "load test"})
