"""fal-ai backend for the ByteDance/Seedance vendor (spec M2).

Speaks the inbound byteplus protocol (same paths/bodies the ComfyUI bytedance
nodes POST); proxies outbound to fal.ai queue/storage. Registered under the
same 3 route segments as the native byteplus adapter:
  byteplus            : video create + seedream image
  byteplus-seedance2  : video poll
  seedance            : asset shim
Native app/adapters/byteplus.py is NOT modified (spec §2)."""
import json
import logging

from fastapi import Request, Response

from app.adapters import register
from app.adapters.base import BaseAdapter

_log = logging.getLogger("comfy-bridge.adapters.fal_ai.bytedance")


def _json_response(obj, status_code: int = 200) -> Response:
    return Response(content=json.dumps(obj), media_type="application/json",
                    status_code=status_code)


class FalBytedanceAdapter(BaseAdapter):
    # Do NOT use provider="byteplus" — BaseAdapter.base()/key() would look up
    # BYTEPLUS_BASE_URL/KEY via config; fal uses FAL_KEY (read directly later).
    # fal adapter never calls self.base()/self.key().
    provider = "fal"  # not in config map; identifier only, never used for credential lookup

    async def handle(self, path: str, request: Request, raw: bytes) -> Response:
        # Skeleton: real dispatch in later tasks. 424 stub for everything now.
        return _json_response(
            {"error": {"code": "not_implemented",
                       "message": f"fal-ai byteplus: no handler yet for {request.method} {path}"}},
            status_code=424,
        )


_adapter = FalBytedanceAdapter()
register("byteplus", _adapter)
register("byteplus-seedance2", _adapter)
register("seedance", _adapter)
