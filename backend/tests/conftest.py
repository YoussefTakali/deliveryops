import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LLM_MODE"] = "mock"
os.environ["AUTH_MODE"] = "dev"
os.environ["WATCH_ENABLED"] = "false"
# Keep retrieval offline and deterministic in tests (never call the embeddings API).
os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["EMBEDDING_DIM"] = "384"
# The whole suite runs as a handful of dev personas within a minute: the per-user API cap (240/min in
# production) would throttle later tests. The limiter itself is covered by tests/test_middleware.py.
os.environ["RATE_LIMIT_PER_MINUTE"] = "100000"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.seed.seed import main as seed

    seed(reset=True)
    with TestClient(app) as c:
        yield c


def as_user(username: str) -> dict:
    return {"X-Dev-User": username}
