"""SEC-003: localhost CORS regex only outside production."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.middleware import LOCALHOST_CORS_ORIGIN_REGEX, cors_allow_origin_regex


def test_cors_regex_disabled_in_production():
    assert cors_allow_origin_regex(production=True) is None


def test_cors_regex_enabled_non_production():
    assert (
        cors_allow_origin_regex(production=False, environment="development")
        == LOCALHOST_CORS_ORIGIN_REGEX
    )


def test_cors_regex_disabled_when_environment_production_even_if_production_flag_false():
    """ENVIRONMENT=production must win over accidental PRODUCTION=false."""
    assert (
        cors_allow_origin_regex(production=False, environment="production") is None
    )


def test_cors_regex_disabled_when_both_production_signals_true():
    assert (
        cors_allow_origin_regex(production=True, environment="production") is None
    )


def test_cors_regex_enabled_development_with_production_false():
    assert (
        cors_allow_origin_regex(production=False, environment="development")
        == LOCALHOST_CORS_ORIGIN_REGEX
    )


def _cors_app(
    *,
    production: bool,
    allow_origins: list[str],
    environment: str = "development",
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=cors_allow_origin_regex(
            production=production,
            environment=environment,
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_preflight_localhost_allowed_non_production():
    client = TestClient(
        _cors_app(
            production=False,
            environment="development",
            allow_origins=["https://app.example.com"],
        )
    )
    res = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert res.headers.get("access-control-allow-credentials") == "true"


def test_preflight_localhost_rejected_environment_production_production_flag_false():
    client = TestClient(
        _cors_app(
            production=False,
            environment="production",
            allow_origins=["https://workspace.krwebsolution.com"],
        )
    )
    res = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") is None


def test_preflight_localhost_rejected_environment_production_production_flag_true():
    client = TestClient(
        _cors_app(
            production=True,
            environment="production",
            allow_origins=["https://workspace.krwebsolution.com"],
        )
    )
    res = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") is None


def test_preflight_localhost_allowed_development_production_false():
    client = TestClient(
        _cors_app(
            production=False,
            environment="development",
            allow_origins=["https://app.example.com"],
        )
    )
    res = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_preflight_localhost_rejected_in_production_unless_allowlisted():
    client = TestClient(
        _cors_app(
            production=True,
            environment="production",
            allow_origins=["https://workspace.krwebsolution.com"],
        )
    )
    res = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") is None


def test_preflight_explicit_localhost_allowlisted_in_production():
    client = TestClient(
        _cors_app(
            production=True,
            environment="production",
            allow_origins=[
                "https://workspace.krwebsolution.com",
                "http://localhost:3000",
            ],
        )
    )
    res = client.options(
        "/ping",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_preflight_configured_production_origin():
    origin = "https://workspace.krwebsolution.com"
    client = TestClient(
        _cors_app(
            production=True,
            environment="production",
            allow_origins=[origin],
        )
    )
    res = client.options(
        "/ping",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.headers.get("access-control-allow-origin") == origin
