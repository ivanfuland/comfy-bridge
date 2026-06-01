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

    import logging
    import uvicorn
    from app.main import app

    # Friendly console output: uvicorn names its main logger "uvicorn.error" even for
    # normal INFO lines, which reads as a wall of errors to non-technical users. Reformat
    # the root handler (app.main's basicConfig attached one) to drop the scary logger name,
    # and print an ASCII banner so nobody mistakes the running server for a failure.
    for _h in logging.getLogger().handlers:
        _h.setFormatter(logging.Formatter("[bridge] %(message)s"))
    bar = "=" * 64
    print(bar)
    print(f"[bridge] READY on http://{host}:{port}  --  this window IS the running service.")
    print("[bridge] The lines below are NORMAL startup logs, NOT errors.")
    print("[bridge] Keep this window OPEN, then start ComfyUI via run_nvidia_gpu_bridge.bat.")
    print(bar)

    uvicorn.run(app, host=host, port=port, loop="asyncio", http="h11", log_config=None)


if __name__ == "__main__":
    main()
