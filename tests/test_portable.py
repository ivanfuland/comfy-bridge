from app._portable import resolve_base_dir, missing_bases_for_filled_keys


def test_resolve_finds_env_in_start_dir(tmp_path):
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")
    assert resolve_base_dir(str(tmp_path)) == str(tmp_path)


def test_resolve_walks_up_to_parent(tmp_path):
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")
    deep = tmp_path / "bridge"
    deep.mkdir()
    assert resolve_base_dir(str(deep)) == str(tmp_path)


def test_resolve_stops_after_max_up_and_returns_start(tmp_path):
    # .env 放在 4 层之上，超过 max_up=3 → 找不到 → 退回 start
    (tmp_path / ".env").write_text("X=1", encoding="utf-8")
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    assert resolve_base_dir(str(deep), max_up=3) == str(deep)


def test_resolve_no_env_returns_start(tmp_path):
    deep = tmp_path / "bridge"
    deep.mkdir()
    assert resolve_base_dir(str(deep)) == str(deep)


def test_missing_bases_flags_key_set_base_empty():
    env = {"OPENAI_API_KEY": "sk-x", "OPENAI_BASE_URL": ""}
    assert missing_bases_for_filled_keys(env) == ["OPENAI"]


def test_missing_bases_ok_when_both_set():
    env = {"OPENAI_API_KEY": "sk-x", "OPENAI_BASE_URL": "https://g"}
    assert missing_bases_for_filled_keys(env) == []


def test_missing_bases_ignores_empty_key():
    env = {"GEMINI_API_KEY": "  ", "GEMINI_BASE_URL": ""}
    assert missing_bases_for_filled_keys(env) == []


def test_missing_bases_covers_all_five_providers():
    env = {f"{p}_API_KEY": "k" for p in ["OPENAI", "ANTHROPIC", "GEMINI", "TRIPO", "BYTEPLUS"]}
    assert sorted(missing_bases_for_filled_keys(env)) == sorted(
        ["OPENAI", "ANTHROPIC", "GEMINI", "TRIPO", "BYTEPLUS"]
    )
