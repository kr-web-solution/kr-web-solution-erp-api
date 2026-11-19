"""F-034: SECRET_KEY / ENCRYPTION_KEY startup validation."""

from cryptography.fernet import Fernet

from app.core.config import validate_security_keys


def _valid_fernet() -> str:
    return Fernet.generate_key().decode()


def test_production_rejects_missing_keys():
    errs = validate_security_keys(
        secret_key="",
        encryption_key="",
        environment="production",
        production=True,
    )
    assert any("SECRET_KEY is missing" in e for e in errs)
    assert any("ENCRYPTION_KEY is missing" in e for e in errs)


def test_production_rejects_known_insecure_defaults():
    errs = validate_security_keys(
        secret_key="5678402f1e2851e0d04db3f7f6117e188360cb31c07262cf4f6722eeaa0510d7",
        encryption_key="Qlhs7DPpurqcfoT6JENui-kFxgVjkywqzwC-iEFcWwE=",
        environment="production",
        production=True,
    )
    assert any("SECRET_KEY" in e and "insecure default" in e for e in errs)
    assert any("ENCRYPTION_KEY" in e and "insecure default" in e for e in errs)
    # Never echo the key material
    joined = " ".join(errs)
    assert "5678402f" not in joined
    assert "Qlhs7DPpurqcfoT6" not in joined


def test_production_rejects_short_secret():
    errs = validate_security_keys(
        secret_key="too-short",
        encryption_key=_valid_fernet(),
        environment="staging",
        production=False,
    )
    assert any("SECRET_KEY is too short" in e for e in errs)


def test_valid_keys_pass_in_production():
    errs = validate_security_keys(
        secret_key="a" * 32,
        encryption_key=_valid_fernet(),
        environment="production",
        production=True,
    )
    assert errs == []


def test_development_allows_empty_keys():
    errs = validate_security_keys(
        secret_key="",
        encryption_key="",
        environment="development",
        production=False,
    )
    assert errs == []


def test_development_still_rejects_known_defaults():
    errs = validate_security_keys(
        secret_key="changeme",
        encryption_key="",
        environment="development",
        production=False,
    )
    assert any("SECRET_KEY" in e and "insecure default" in e for e in errs)
