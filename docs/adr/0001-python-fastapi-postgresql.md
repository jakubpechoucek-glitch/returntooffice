# ADR-0001: Python (FastAPI) on PostgreSQL

**Status**: accepted (2026-11-08) · **Decided by**: Jakub Pechoucek

## Context
The prototype must be ready for hardening by IT, with enterprise requirements: PostgreSQL, Entra ID SSO,
OAuth 2.0 and Kubernetes. The team asked for Python.

## Decision
- Python 3.12 with FastAPI (ASGI), SQLAlchemy 2 ORM, Alembic migrations, Pydantic settings.
- PostgreSQL 16 as the only datastore. The code stays portable to SQLite, used only for fast tests.
- Served by gunicorn with uvicorn workers in a container; deployed with Helm on Kubernetes.

## Consequences
- One language for UI, API, batch and tests. A large hiring pool and mainstream libraries.
- CPU-bound recompute runs in-process today. It can move to a worker queue without changing the domain code.
