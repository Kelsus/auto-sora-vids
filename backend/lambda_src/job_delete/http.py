from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,x-api-key",
    "Access-Control-Allow-Methods": "DELETE,OPTIONS",
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
        },
        "body": "",
    }


def no_content() -> Dict[str, Any]:
    return {
        "statusCode": 204,
        "headers": _CORS_HEADERS,
        "body": "",
    }


def bad_request(message: str) -> Dict[str, Any]:
    return HttpResponse(status_code=400, body={"message": message}).to_payload()


def not_found(job_id: str) -> Dict[str, Any]:
    return HttpResponse(status_code=404, body={"message": f"Job '{job_id}' not found"}).to_payload()


def server_error(message: str) -> Dict[str, Any]:
    return HttpResponse(status_code=500, body={"message": message}).to_payload()
