from __future__ import annotations

import base64
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

URL_EXPIRY_SECONDS = 900  # 15 minutes
MAX_IMAGES = 10

ALLOWED_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
}

CONTENT_TYPE_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}

CHARACTER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{0,62}[a-zA-Z0-9]$|^[a-zA-Z0-9]{1,2}$")

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,x-api-key",
}


class ValidationError(Exception):
    pass


class NotFoundError(Exception):
    pass


def _cors_preflight() -> Dict[str, Any]:
    return {
        "statusCode": 204,
        "headers": {
            **_CORS_HEADERS,
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": "",
    }


def _ok(data: Any) -> Dict[str, Any]:
    return {
        "statusCode": 200,
        "headers": _CORS_HEADERS,
        "body": json.dumps(data),
    }


def _created(data: Any) -> Dict[str, Any]:
    return {
        "statusCode": 201,
        "headers": _CORS_HEADERS,
        "body": json.dumps(data),
    }


def _no_content() -> Dict[str, Any]:
    return {
        "statusCode": 204,
        "headers": _CORS_HEADERS,
        "body": "",
    }


def _bad_request(message: str) -> Dict[str, Any]:
    return {
        "statusCode": 400,
        "headers": _CORS_HEADERS,
        "body": json.dumps({"message": message}),
    }


def _not_found(message: str) -> Dict[str, Any]:
    return {
        "statusCode": 404,
        "headers": _CORS_HEADERS,
        "body": json.dumps({"message": message}),
    }


def _server_error(message: str) -> Dict[str, Any]:
    return {
        "statusCode": 500,
        "headers": _CORS_HEADERS,
        "body": json.dumps({"message": message}),
    }


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
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


class CharacterManagerApplication:
    """Manages Veo character reference images and metadata in S3."""

    def __init__(
        self,
        s3_client: Any | None = None,
        bucket: str | None = None,
    ) -> None:
        self._s3 = s3_client or boto3.client(
            "s3", config=Config(signature_version="s3v4")
        )
        self._bucket = bucket or os.environ["CHARACTERS_BUCKET"]

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        method = event.get("httpMethod", "")
        if method == "OPTIONS":
            return _cors_preflight()

        resource = event.get("resource", "")
        path_params = event.get("pathParameters") or {}

        try:
            if resource == "/characters" and method == "GET":
                return self._list_characters()
            elif resource == "/characters" and method == "POST":
                return self._create_character(event)
            elif resource == "/characters/{characterId}" and method == "GET":
                return self._get_character(path_params["characterId"])
            elif resource == "/characters/{characterId}" and method == "PUT":
                return self._update_character(path_params["characterId"], event)
            elif resource == "/characters/{characterId}" and method == "DELETE":
                return self._delete_character(path_params["characterId"])
            elif resource == "/characters/{characterId}/upload-urls" and method == "POST":
                return self._get_upload_urls(path_params["characterId"], event)
            else:
                return _bad_request(f"Unsupported route: {method} {resource}")
        except ValidationError as exc:
            return _bad_request(str(exc))
        except NotFoundError as exc:
            return _not_found(str(exc))
        except Exception:
            logger.exception("Unhandled error in character manager")
            return _server_error("Internal server error")

    def _list_characters(self) -> Dict[str, Any]:
        prefixes = self._list_character_prefixes()
        characters: List[Dict[str, Any]] = []

        for prefix in prefixes:
            character_id = prefix.rstrip("/")
            metadata = self._get_metadata(character_id)
            if metadata is None:
                continue

            thumbnail_url = None
            ref_images = metadata.get("referenceImages", [])
            if ref_images:
                thumbnail_url = self._presign_get(f"{character_id}/{ref_images[0]}")

            characters.append({
                "characterId": character_id,
                "imageCount": len(ref_images),
                "thumbnailUrl": thumbnail_url,
                "voiceDescription": metadata.get("voiceDescription", ""),
                "presenterDescription": metadata.get("presenterDescription", ""),
                "promptPrefix": metadata.get("promptPrefix", ""),
                "negativePrompt": metadata.get("negativePrompt", ""),
            })

        return _ok({"characters": characters})

    def _get_character(self, character_id: str) -> Dict[str, Any]:
        metadata = self._get_metadata(character_id)
        if metadata is None:
            raise NotFoundError(f"Character '{character_id}' not found")

        ref_images = metadata.get("referenceImages", [])
        image_urls = [
            {"filename": img, "url": self._presign_get(f"{character_id}/{img}")}
            for img in ref_images
        ]

        return _ok({
            "characterId": character_id,
            "referenceImages": image_urls,
            "voiceDescription": metadata.get("voiceDescription", ""),
            "presenterDescription": metadata.get("presenterDescription", ""),
            "promptPrefix": metadata.get("promptPrefix", ""),
            "negativePrompt": metadata.get("negativePrompt", ""),
        })

    def _create_character(self, event: Dict[str, Any]) -> Dict[str, Any]:
        payload = _parse_body(event)
        character_id = payload.get("characterId") or payload.get("character_id")
        if not character_id or not isinstance(character_id, str):
            raise ValidationError("characterId is required")

        character_id = character_id.strip()
        if not CHARACTER_ID_PATTERN.match(character_id):
            raise ValidationError(
                "characterId must be 2-64 chars, alphanumeric and hyphens, "
                "must start and end with alphanumeric"
            )

        existing = self._get_metadata(character_id)
        if existing is not None:
            raise ValidationError(f"Character '{character_id}' already exists")

        metadata = self._build_metadata(payload)
        self._put_metadata(character_id, metadata)

        return _created({
            "characterId": character_id,
            "message": "Character created",
        })

    def _update_character(self, character_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        existing = self._get_metadata(character_id)
        if existing is None:
            raise NotFoundError(f"Character '{character_id}' not found")

        payload = _parse_body(event)
        metadata = self._merge_metadata(existing, payload)
        self._put_metadata(character_id, metadata)

        return _ok({
            "characterId": character_id,
            "message": "Character updated",
        })

    def _delete_character(self, character_id: str) -> Dict[str, Any]:
        objects = self._list_objects_for_character(character_id)
        if not objects:
            raise NotFoundError(f"Character '{character_id}' not found")

        # Delete in batches of 1000 (S3 limit)
        for i in range(0, len(objects), 1000):
            batch = objects[i : i + 1000]
            self._s3.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": k} for k in batch]},
            )

        return _no_content()

    def _get_upload_urls(self, character_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        existing = self._get_metadata(character_id)
        if existing is None:
            raise NotFoundError(f"Character '{character_id}' not found")

        payload = _parse_body(event)
        images = payload.get("images")
        if not images or not isinstance(images, list):
            raise ValidationError("images is required and must be a list")

        if len(images) > MAX_IMAGES:
            raise ValidationError(f"At most {MAX_IMAGES} images may be uploaded at once")

        results = []
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

            ext = CONTENT_TYPE_TO_EXT.get(content_type, "png")
            unique_id = uuid.uuid4().hex[:12]
            key = f"{character_id}/ref-{unique_id}.{ext}"

            upload_url = self._s3.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                },
                ExpiresIn=URL_EXPIRY_SECONDS,
            )

            results.append({
                "key": key,
                "upload_url": upload_url,
                "filename": filename,
            })

        return _ok({"images": results})

    # ---- helpers ----

    def _list_character_prefixes(self) -> List[str]:
        prefixes: List[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Delimiter="/"):
            for prefix_obj in page.get("CommonPrefixes", []):
                prefixes.append(prefix_obj["Prefix"])
        return prefixes

    def _get_metadata(self, character_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._s3.get_object(
                Bucket=self._bucket,
                Key=f"{character_id}/metadata.json",
            )
            return json.loads(resp["Body"].read().decode("utf-8"))
        except self._s3.exceptions.NoSuchKey:
            return None

    def _put_metadata(self, character_id: str, metadata: Dict[str, Any]) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=f"{character_id}/metadata.json",
            Body=json.dumps(metadata, indent=2),
            ContentType="application/json",
        )

    def _list_objects_for_character(self, character_id: str) -> List[str]:
        keys: List[str] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=f"{character_id}/"):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def _presign_get(self, key: str) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=URL_EXPIRY_SECONDS,
        )

    def _build_metadata(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "referenceImages": payload.get("referenceImages", []),
            "voiceDescription": payload.get("voiceDescription", ""),
            "presenterDescription": payload.get("presenterDescription", ""),
            "promptPrefix": payload.get("promptPrefix", ""),
            "negativePrompt": payload.get("negativePrompt", ""),
        }

    def _merge_metadata(
        self, existing: Dict[str, Any], payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        merged = dict(existing)
        for key in ("referenceImages", "voiceDescription", "presenterDescription", "promptPrefix", "negativePrompt"):
            if key in payload:
                merged[key] = payload[key]
        return merged


def lambda_handler(
    event: Dict[str, Any], context: Any
) -> Dict[str, Any]:  # pragma: no cover - AWS entry
    return CharacterManagerApplication().handle_event(event)
