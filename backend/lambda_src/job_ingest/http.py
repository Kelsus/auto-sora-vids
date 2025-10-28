from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict

from job_ingest.models import JobRequest, ValidationError


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


class HttpRequestParser:
    """Extracts the payload from API Gateway proxy events."""

    def parse(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if "body" not in event or event["body"] is None:
            raise ValidationError("Missing request body")

        body = event["body"]
        if event.get("isBase64Encoded"):  # pragma: no cover - gateway config
            body = base64.b64decode(body).decode("utf-8")

        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise ValidationError("Body must be valid JSON") from exc

        if isinstance(body, dict):
            return body

        raise ValidationError("Unsupported body type")


def cors_preflight_response() -> Dict[str, Any]:
    return {
        "statusCode": 204,
        "headers": {
            **_CORS_HEADERS,
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": "",
    }


def bad_request(message: str) -> Dict[str, Any]:
    return HttpResponse(status_code=400, body={"message": message}).to_payload()


def created(job_id: str) -> Dict[str, Any]:
    return HttpResponse(status_code=201, body={"jobId": job_id}).to_payload()


def server_error(message: str) -> Dict[str, Any]:
    return HttpResponse(status_code=500, body={"message": message}).to_payload()
