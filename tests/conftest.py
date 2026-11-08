import os
import pathlib
import tempfile

# Tests run against PostgreSQL when RTO_TEST_DATABASE_URL is set (CI does this), otherwise SQLite.
_tmp = tempfile.mkdtemp()
os.environ["RTO_DATABASE_URL"] = os.environ.get(
    "RTO_TEST_DATABASE_URL", f"sqlite+pysqlite:///{_tmp}/test.db"
)
os.environ["RTO_ENVIRONMENT"] = "test"
os.environ["RTO_AUTH_MODE"] = "dev"

import pytest  # noqa: E402

from rto.db import Base, get_engine, get_sessionmaker  # noqa: E402

SAMPLE = pathlib.Path(__file__).resolve().parents[1] / "sample_data" / "onsite_report_2026_jan_mar_anonymized.xlsx"


@pytest.fixture()
def db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def sample_bytes():
    return SAMPLE.read_bytes()
