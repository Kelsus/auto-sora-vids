from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
}


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: Dict[str, Any] | None = None

    def to_payload(self) -> Dict[str, Any]:
        return {
            "statusCode": self.status_code,
            "headers": _CORS_HEADERS,
            "body": json.dumps(self.body or {}),
        }


def cors_preflight_response() -> Dict[str, Any]:
    return {
        "statusCode": 204,
        "headers": {
            **_CORS_HEADERS,
            "Access-Control-Allow-Methods": "PATCH,OPTIONS",
        },
        "body": "",
    }


def ok(body: Dict[str, Any]) -> Dict[str, Any]:
    return HttpResponse(status_code=200, body=body).to_payload()


def bad_request(message: str) -> Dict[str, Any]:
    return HttpResponse(status_code=400, body={"message": message}).to_payload()


def not_found(job_id: str) -> Dict[str, Any]:
    return HttpResponse(status_code=404, body={"message": f"Job '{job_id}' not found"}).to_payload()


def server_error(message: str) -> Dict[str, Any]:
    return HttpResponse(status_code=500, body={"message": message}).to_payload()


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    if "body" not in event or event["body"] is None:
        raise ValueError("Missing request body")

    body = event["body"]
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError("Body must be valid JSON") from exc

    if isinstance(body, dict):
        return body

    raise ValueError("Unsupported body type")
