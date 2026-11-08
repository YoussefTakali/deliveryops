"""Regression tests for the production configuration guard — the last line of defense against
deploying with dev-mode auth, SQLite, demo seed data, or a wildcard/localhost CORS origin."""

import pytest
from pydantic import ValidationError

from app.config import Settings

VALID_PROD = dict(
    environment="production", auth_mode="keycloak", database_url="postgresql+psycopg://u:p@host/db",
    seed_demo_data=False, cors_origins="https://delivery.example.com", role="api",
)


def test_valid_production_config_passes():
    Settings(**VALID_PROD)


@pytest.mark.parametrize(
    "override",
    [
        {"auth_mode": "dev"},
        {"database_url": "sqlite:///./deliveryops.db"},
        {"seed_demo_data": True},
        {"cors_origins": "*"},
        {"cors_origins": "http://localhost:4300"},
        {"role": "all"},
        {"llm_mode": "api", "llm_api_key": None},
    ],
)
def test_invalid_production_config_rejected(override):
    with pytest.raises(ValidationError):
        Settings(**{**VALID_PROD, **override})


def test_development_defaults_are_permissive():
    # the same settings that would be rejected in production must be fine for local dev
    Settings(environment="development")
