from __future__ import annotations

from typing import Any, Dict

from videopusher_forwarder.app import VideoPusherForwarder


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    app = VideoPusherForwarder()
    return app.handle(event.get("Records", []))
