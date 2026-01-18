from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_IMAGES = 3
URL_EXPIRY_SECONDS = 900  # 15 minutes

ALLOWED_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/avif",
    "image/heic",
    "image/heif",
}

CONTENT_TYPE_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/avif": "avif",
    "image/heic": "heic",
    "image/heif": "heif",
}

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,x-api-key",
}


class ValidationError(Exception):
    pass


@dataclass(frozen=True)
class ImageUploadRequest:
    job_id: str
    images: List[Dict[str, str]]

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ImageUploadRequest":
        job_id = payload.get("job_id") or payload.get("jobId")
        if not job_id or not isinstance(job_id, str):
            raise ValidationError("job_id is required and must be a string")

        images = payload.get("images")
        if not images or not isinstance(images, list):
            raise ValidationError("images is required and must be a list")

        if len(images) > MAX_IMAGES:
            raise ValidationError(f"At most {MAX_IMAGES} images may be uploaded")

        if len(images) == 0:
            raise ValidationError("At least one image must be provided")

        for i, img in enumerate(images):
            if not isinstance(img, dict):
                raise ValidationError(f"images[{i}] must be an object")

            filename = img.get("filename")
            content_type = img.get("content_type") or img.get("contentType")

            if not filename or not isinstance(filename, str):
                raise ValidationError(f"images[{i}].filename is required")

            if not content_type or not isinstance(content_type, str):
                raise ValidationError(f"images[{i}].content_type is required")

            if content_type not in ALLOWED_CONTENT_TYPES:
                raise ValidationError(
                    f"images[{i}].content_type '{content_type}' is not supported. "
                    f"Allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
                )

        return cls(job_id=job_id, images=images)


class ImageUploadUrlsApplication:
    """Generates presigned S3 URLs for uploading user images."""

    def __init__(self, s3_client: Any | None = None, bucket: str | None = None) -> None:
        self._s3 = s3_client or boto3.client(
            "s3", config=Config(signature_version="s3v4")
        )
        self._bucket = bucket or os.environ["OUTPUT_BUCKET"]

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Image upload URL request received")

        if event.get("httpMethod") == "OPTIONS":
            return self._cors_preflight_response()

        try:
            payload = self._parse_request(event)
            request = ImageUploadRequest.from_payload(payload)
        except ValidationError as exc:
            return self._bad_request(str(exc))

        try:
            result = self._generate_upload_urls(request)
            return self._success(result)
        except Exception:
            logger.exception("Failed to generate upload URLs")
            return self._server_error("Failed to generate upload URLs")

    def _parse_request(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if "body" not in event or event["body"] is None:
            raise ValidationError("Missing request body")

        body = event["body"]
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")

        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise ValidationError("Body must be valid JSON") from exc

        if isinstance(body, dict):
            return body

        raise ValidationError("Unsupported body type")

    def _generate_upload_urls(
        self, request: ImageUploadRequest
    ) -> Dict[str, List[Dict[str, str]]]:
        results = []

        for img in request.images:
            filename = img["filename"]
            content_type = img.get("content_type") or img.get("contentType")
            ext = CONTENT_TYPE_TO_EXT.get(content_type, "jpg")

            unique_id = uuid.uuid4().hex[:12]
            key = f"jobs/{request.job_id}/uploads/user-image-{unique_id}.{ext}"

            upload_url = self._s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=URL_EXPIRY_SECONDS,
            )

            results.append(
                {
                    "key": key,
                    "upload_url": upload_url,
                    "filename": filename,
                }
            )

        return {"images": results}

    def _cors_preflight_response(self) -> Dict[str, Any]:
        return {
            "statusCode": 204,
            "headers": {
                **_CORS_HEADERS,
                "Access-Control-Allow-Methods": "POST,OPTIONS",
            },
            "body": "",
        }

    def _bad_request(self, message: str) -> Dict[str, Any]:
        return {
            "statusCode": 400,
            "headers": _CORS_HEADERS,
            "body": json.dumps({"message": message}),
        }

    def _success(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "statusCode": 200,
            "headers": _CORS_HEADERS,
            "body": json.dumps(data),
        }

    def _server_error(self, message: str) -> Dict[str, Any]:
        return {
            "statusCode": 500,
            "headers": _CORS_HEADERS,
            "body": json.dumps({"message": message}),
        }


def lambda_handler(
    event: Dict[str, Any], context: Any
) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return ImageUploadUrlsApplication().handle_event(event)
