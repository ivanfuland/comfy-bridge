import importlib
import pytest
from app import config as config_mod


def _load(monkeypatch, **env):
    for k in list(env):
        monkeypatch.setenv(k, env[k])
    importlib.reload(config_mod)
    return config_mod.load_config()


def test_defaults_and_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = _load(
        monkeypatch,
        BRIDGE_PORT="9999",
        BRIDGE_GATING="off",
        BRIDGE_CORS_ORIGINS="http://a:1,http://b:2",
    )
    assert cfg.host == "127.0.0.1"            # default
    assert cfg.port == 9999                   # overridden
    assert cfg.gating_enabled is False        # "off" -> False
    assert cfg.cors_origins == ["http://a:1", "http://b:2"]


def test_provider_key_lookup(monkeypatch):
    cfg = _load(monkeypatch, OPENAI_API_KEY="sk-xyz", OPENAI_BASE_URL="https://llm.example.com")
    assert cfg.require_key("openai") == "sk-xyz"
    assert cfg.base_url("openai") == "https://llm.example.com"


def test_missing_key_raises_missingconfig(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = _load(monkeypatch)
    with pytest.raises(config_mod.MissingConfig) as ei:
        cfg.require_key("openai")
    assert "openai" in str(ei.value)


import importlib
from app import config as cfg_mod


def _reload_with(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(cfg_mod)
    return cfg_mod


def test_csv_env_unset_uses_default(monkeypatch):
    monkeypatch.delenv("BRIDGE_ALLOWED_VENDORS", raising=False)
    m = _reload_with(monkeypatch)
    assert m._csv_env("BRIDGE_ALLOWED_VENDORS", ["openai", "tripo"]) == ["openai", "tripo"]


def test_csv_env_explicit_empty_is_empty(monkeypatch):
    m = _reload_with(monkeypatch)
    monkeypatch.setenv("BRIDGE_ALLOWED_VENDORS", "")
    assert m._csv_env("BRIDGE_ALLOWED_VENDORS", ["openai"]) == []
    monkeypatch.setenv("BRIDGE_ALLOWED_VENDORS", " , ")
    assert m._csv_env("BRIDGE_ALLOWED_VENDORS", ["openai"]) == []


def test_csv_env_present_value_parsed(monkeypatch):
    m = _reload_with(monkeypatch)
    monkeypatch.setenv("BRIDGE_ALLOWED_VENDORS", "openai, gemini ,tripo")
    assert m._csv_env("BRIDGE_ALLOWED_VENDORS", ["x"]) == ["openai", "gemini", "tripo"]
