"""API surface contract: routing, auth guards and the error envelope.

These run without a database. Anything that needs real persistence lives in
tests marked ``integration`` and runs against the pgvector service in CI.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def no_startup(monkeypatch):
    """Neutralise lifespan work that would need live services."""
    import app.main as main

    async def ok():
        return True

    monkeypatch.setattr(main, "check_database", ok)
    monkeypatch.setattr(main, "check_redis", ok)
    monkeypatch.setattr(main, "init_redis", lambda: ok())


class _UnusableSession:
    """Stands in for AsyncSession.

    FastAPI resolves every dependency before invoking the handler, so even a
    request that will be rejected by the auth guard still constructs the `db`
    dependency. These tests must not need a live database, so this raises the
    moment anything actually touches it — which keeps a test silently
    depending on real persistence from ever passing.
    """

    def __getattr__(self, name):
        raise AssertionError(
            f"Test touched the database (session.{name}); it should not need one."
        )


@pytest.fixture
def client():
    from app.core.database import get_db

    async def _fake_db():
        yield _UnusableSession()

    app.dependency_overrides[get_db] = _fake_db
    # raise_server_exceptions=False so the registered handlers produce a
    # response instead of the exception propagating into the test.
    with TestClient(app, raise_server_exceptions=False) as _client:
        yield _client
    app.dependency_overrides.clear()


class TestOpenAPI:
    def test_schema_generates(self):
        """A broken response_model only surfaces when the schema is built."""
        assert app.openapi()["info"]["title"] == "Booktunes API"

    def test_every_documented_endpoint_exists(self):
        paths = set(app.openapi()["paths"])
        required = {
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/auth/refresh",
            "/api/v1/auth/logout",
            "/api/v1/auth/preferences",
            "/api/v1/auth/me",
            "/api/v1/books",
            "/api/v1/books/{book_id}",
            "/api/v1/books/trending",
            "/api/v1/books/mood/{mood}",
            "/api/v1/books/genre/{genre}",
            "/api/v1/recommendations/personalized",
            "/api/v1/recommendations/book/{book_id}/summary",
            "/api/v1/recommendations/feedback",
            "/api/v1/recommendations/mood/{mood}",
            "/api/v1/reading/currently",
            "/api/v1/reading/progress",
            "/api/v1/reading/progress/{book_id}",
            "/api/v1/reading/batch-sync",
            "/api/v1/reading/stats",
            "/api/v1/playlists/book/{book_id}",
            "/api/v1/playlists/generate",
            "/api/v1/playlists/save",
            "/api/v1/playlists/user",
            "/api/v1/playlists/preview",
            "/api/v1/library",
            "/api/v1/library/{book_id}",
        }
        assert required <= paths, f"missing: {sorted(required - paths)}"

    @pytest.mark.parametrize("literal", ["trending", "genres", "moods"])
    def test_literal_book_routes_are_not_swallowed_by_the_id_route(
        self, client, literal
    ):
        """`/books/trending` must reach its own handler.

        If `/books/{book_id}` were matched first, FastAPI would try to parse
        "trending" as a UUID and return 422. Asserted behaviourally rather
        than by inspecting route order, which is a FastAPI internal.
        """
        assert client.get(f"/api/v1/books/{literal}").status_code != 422


class TestMeta:
    def test_root(self, client):
        body = client.get("/").json()
        assert body["service"] == "Booktunes API"

    def test_metrics_exposed(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "booktunes_requests_total" in response.text

    def test_request_id_header_added(self, client):
        assert client.get("/").headers["X-Request-ID"]

    def test_supplied_request_id_is_echoed(self, client):
        response = client.get("/", headers={"X-Request-ID": "trace-me-123"})
        assert response.headers["X-Request-ID"] == "trace-me-123"

    def test_response_time_header(self, client):
        assert float(client.get("/").headers["X-Response-Time-Ms"]) >= 0


class TestAuthGuards:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/v1/auth/me"),
            ("get", "/api/v1/recommendations/personalized"),
            ("get", "/api/v1/reading/currently"),
            ("get", "/api/v1/reading/stats"),
            ("get", "/api/v1/library"),
            ("get", "/api/v1/playlists/user"),
            ("get", "/api/v1/users/me"),
        ],
    )
    def test_protected_endpoints_reject_anonymous(self, client, method, path):
        response = getattr(client, method)(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "missing_token"

    def test_garbage_token_rejected(self, client):
        response = client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_token"

    def test_public_endpoints_allow_anonymous(self, client):
        # Reaches the handler rather than being turned away at the door.
        assert client.get("/api/v1/books/genres").status_code == 200
        assert client.get("/api/v1/books/moods").status_code == 200


class TestErrorEnvelope:
    def test_unknown_route_uses_the_envelope(self, client):
        body = client.get("/api/v1/does-not-exist").json()
        assert "error" in body
        assert {"code", "message", "details"} <= set(body["error"])
        assert "request_id" in body

    def test_validation_errors_list_the_fields(self, client):
        response = client.post("/api/v1/auth/register", json={"username": "ab"})
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["details"]["fields"]

    def test_weak_password_rejected_before_any_db_access(self, client):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "validuser",
                "email": "user@example.com",
                "password": "alllettersnodigits",
            },
        )
        assert response.status_code == 422

    def test_bad_uuid_in_path_is_a_422(self, client):
        response = client.get("/api/v1/books/not-a-uuid")
        assert response.status_code == 422


class TestSyncConfig:
    def test_advertises_the_polling_contract(self, client):
        body = client.get("/api/v1/reading/sync-config").json()
        assert body["poll_interval_seconds"] == 30
        assert body["max_batch_size"] == 10
        assert body["conflict_strategy"] == "version"
