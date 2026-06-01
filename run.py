"""Frozen entrypoint for the portable bridge.exe (PyInstaller onedir, spec §6.2).

Order is load-bearing (Codex #5): resolve kit root -> chdir -> load_dotenv(override)
-> preflight -> ONLY THEN import app.main (which imports app.config). Importing config
before the .env load would let stale process env vars win / miss the kit .env entirely."""
import os
import sys

from app._portable import resolve_base_dir, missing_bases_for_filled_keys


def _start_dir() -> str:
    # Frozen: dir holding bridge.exe. Source: this file's dir.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    base = resolve_base_dir(_start_dir())
    os.chdir(base)  # so app.config load_dotenv() + default asset-cache/logs resolve to kit root

    from dotenv import load_dotenv
    load_dotenv(os.path.join(base, ".env"), override=True)

    missing = missing_bases_for_filled_keys(os.environ)
    if missing:
        # explicit f-string (no implicit literal concatenation — avoids silently
        # dropping a line if someone edits between literals later)
        providers = ", ".join(missing)
        sys.stderr.write(
            f"[bridge] 配置错误：以下 provider 填了 API key 但 *_BASE_URL 为空：{providers}\n"
            f"[bridge] 便携套件应使用预填雷火网关地址的 .env（见 .env.example）；"
            f"补全对应 *_BASE_URL（如 https://ai.leihuo.netease.com）后重试。\n"
        )
        raise SystemExit(2)

    host = os.getenv("BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("BRIDGE_PORT", "8190"))
    log_io = os.getenv("BRIDGE_LOG_IO", "on")
    print(f"[bridge] config from {os.path.join(base, '.env')} | host={host} port={port} log_io={log_io}")

    import uvicorn
    from app.main import app
    uvicorn.run(app, host=host, port=port, loop="asyncio", http="h11", log_config=None)


if __name__ == "__main__":
    main()
