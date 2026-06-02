import os

TEMPLATE = os.path.join("packaging", ".env.example.kit")
GATEWAY = "https://ai.leihuo.netease.com"
PROVIDERS = ["OPENAI", "ANTHROPIC", "GEMINI", "TRIPO", "BYTEPLUS"]


def _parse(path):
    """Return {KEY: VALUE} from active (non-comment, non-blank) `K=V` lines."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def test_all_base_urls_prefilled_to_gateway():
    env = _parse(TEMPLATE)
    for p in PROVIDERS:
        assert env.get(f"{p}_BASE_URL") == GATEWAY, f"{p}_BASE_URL must be pre-filled to gateway"


def test_all_api_keys_present_and_blank():
    env = _parse(TEMPLATE)
    for p in PROVIDERS:
        assert f"{p}_API_KEY" in env, f"{p}_API_KEY line must exist"
        assert env[f"{p}_API_KEY"] == "", f"{p}_API_KEY must ship blank (bring-your-own-key)"


def test_log_io_defaults_off():
    assert _parse(TEMPLATE).get("BRIDGE_LOG_IO") == "off"


def test_port_not_actively_exposed():
    # 套件锁死 8190：不应有 active 的 BRIDGE_PORT= 行（注释说明可以有）
    assert "BRIDGE_PORT" not in _parse(TEMPLATE)


def test_gating_prefilled_correctly():
    env = _parse(TEMPLATE)
    # 5 adapted vendors active so the menu is scoped correctly out of the box
    assert set(env.get("BRIDGE_ALLOWED_VENDORS", "").split(",")) == {
        "openai", "anthropic", "gemini", "tripo", "bytedance"
    }
    # DALL·E + deprecated Seedance 1.x hidden by default
    hidden = env.get("BRIDGE_HIDDEN_NODE_CLASSES", "")
    for cls in ("OpenAIDalle2", "OpenAIDalle3", "ByteDanceTextToVideoNode"):
        assert cls in hidden, f"{cls} should ship hidden"
    # the removed per-class allowlist mechanism must NOT reappear in the template
    assert "BRIDGE_ALLOWED_NODE_CLASSES" not in env
