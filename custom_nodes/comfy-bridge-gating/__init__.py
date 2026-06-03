"""comfy-bridge gating custom node（后端剪枝）。纯逻辑见 _gating_core。
import 期：阻塞拉取 <BRIDGE_GATING_URL>/comfy-bridge/gating，按 segment + 多信号
is_backend_node + per-segment loaded_segments 从 nodes.NODE_CLASS_MAPPINGS 删类，
使 /object_info 干净。超时/不可达 → fail-closed（删全部 comfy_api_nodes）。见 spec v6。"""
import importlib.util
import json
import logging
import os
import pathlib
import time
import urllib.request

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

_log = logging.getLogger("comfy-bridge-gating")

# 目录名带连字符，非合法包名 → 用 importlib 从同目录文件加载纯逻辑模块。
_spec = importlib.util.spec_from_file_location(
    "comfy_bridge_gating_core", str(pathlib.Path(__file__).parent / "_gating_core.py"))
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)

_DEFAULT_URL = "http://127.0.0.1:8190/comfy-bridge/gating"
_GATING_URL = os.environ.get("BRIDGE_GATING_URL", _DEFAULT_URL)
# NOTE: _STARTUP_TIMEOUT is NOT parsed at module top-level to avoid crash→fail-open on malformed env.
# 畸形值在 _run() 内 parse_timeout() 容错处理（Codex 最终审 #2）。


def _http_fetch():
    with urllib.request.urlopen(_GATING_URL, timeout=2) as r:
        return json.loads(r.read())


def _cls_meta(name, cls):
    return _core.node_meta(cls)          # 短路非 comfy_api 节点 + __module__ 兜底（Codex plan #1/#2）


def _cls_meta_min(name, cls):
    return _core.node_meta_min(cls)      # __module__ 兜底，fail-closed 路径不漏（Codex plan #2）


def _run():
    # 容错解析：畸形 env 值回退默认，不让 import 崩→fail-open（Codex 最终审 #2）
    timeout = _core.parse_timeout(os.environ.get("BRIDGE_GATING_STARTUP_TIMEOUT"))
    _log.info("comfy-bridge gating: using gating URL: %s (timeout=%ss)", _GATING_URL, timeout)
    try:
        import nodes
    except Exception as e:
        _log.warning("cannot import nodes module: %s — skip gating", e)
        return
    mappings = nodes.NODE_CLASS_MAPPINGS
    display = nodes.NODE_DISPLAY_NAME_MAPPINGS

    status, gating = _core.fetch_gating_blocking(
        _http_fetch, timeout, clock=time.monotonic, sleep=time.sleep)

    action = _core.classify_payload(status, gating)
    if action == "fail_closed":
        _log.warning("gating 不可用/ payload 畸形（status=%s）→ fail-closed：删除全部 comfy_api_nodes", status)
        removed = _core.fail_closed_prune(mappings, display, cls_meta=_cls_meta_min)
        _log.warning("fail-closed 删除 %d 个 comfy_api_nodes 节点", len(removed))
        return
    if action == "skip":
        _log.info("gating disabled — no pruning")
        return
    try:
        removed = _core.prune(mappings, display, gating, cls_meta=_cls_meta)
    except Exception as e:
        _log.warning("prune 异常 %s → fail-closed", e)
        removed = _core.fail_closed_prune(mappings, display, cls_meta=_cls_meta_min)
        _log.warning("fail-closed 删除 %d 个 comfy_api_nodes 节点", len(removed))
        return
    _log.info("gating 剪枝完成：删除 %d 个节点（allowed=%s, loaded_segments=%s）",
              len(removed), gating.get("allowed_vendors"), gating.get("loaded_segments"))


_run()
