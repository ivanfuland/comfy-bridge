"""Portable-mode helpers for the frozen bridge.exe. Pure functions, no I/O side
effects beyond filesystem existence checks — kept out of run.py so they're unit-
testable and importable WITHOUT triggering app.config's top-level load_dotenv()."""
import os

# Providers whose {P}_API_KEY / {P}_BASE_URL pair the bridge proxies (spec §7.1).
_PROVIDERS = ["OPENAI", "ANTHROPIC", "GEMINI", "TRIPO", "BYTEPLUS"]


def resolve_base_dir(start_dir: str, marker: str = ".env", max_up: int = 3) -> str:
    """Walk up from start_dir (inclusive) at most max_up parents looking for a dir
    containing `marker`. Return the first match, else start_dir. Used to locate the
    kit root (which holds .env / asset-cache / logs) from the exe's own location."""
    d = start_dir
    for _ in range(max_up + 1):
        if os.path.exists(os.path.join(d, marker)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return start_dir


def missing_bases_for_filled_keys(env) -> list:
    """Return providers whose API key is set (non-blank) but BASE_URL is empty/unset.
    Portable fail-fast guard (Codex #6): a filled key with no base would silently fall
    back to the official upstream in config.py — leaking the gateway key to the wrong
    host. Order follows _PROVIDERS for deterministic messaging."""
    missing = []
    for p in _PROVIDERS:
        key = (env.get(f"{p}_API_KEY") or "").strip()
        base = (env.get(f"{p}_BASE_URL") or "").strip()
        if key and not base:
            missing.append(p)
    return missing
