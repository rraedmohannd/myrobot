from app.core.config import Settings


def test_defaults_do_not_contain_api_keys(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.openai_api_key is None
    assert s.anthropic_api_key is None


def test_api_key_is_secret(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    s = Settings(_env_file=None)
    assert "sk-test-not-real" not in repr(s)
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == "sk-test-not-real"


def test_cors_origins_comma_separated(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.local, http://b.local")
    assert Settings(_env_file=None).cors_origin_list == ["http://a.local", "http://b.local"]
