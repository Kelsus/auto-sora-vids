from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import error, request

from article_probe.http import bad_request, cors_preflight_response, ok, parse_body, server_error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_USER_AGENT = "videopusher-article-tester/1.0"
_MAX_BYTES = 64 * 1024


@dataclass
class ProbeResult:
    ok: bool
    status_code: Optional[int] = None
    final_url: Optional[str] = None
    content_type: Optional[str] = None
    word_count: Optional[int] = None
    preview: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None


class UrlFetcher:
    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def fetch(self, url: str) -> ProbeResult:
        req = request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with request.urlopen(req, timeout=self._timeout) as response:  # nosec B310 - validated URL
                content_type = response.headers.get("Content-Type")
                raw = response.read(_MAX_BYTES)
                preview_text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
                word_count = len(preview_text.split())
                return ProbeResult(
                    ok=True,
                    status_code=getattr(response, "status", None),
                    final_url=response.geturl(),
                    content_type=content_type,
                    word_count=word_count,
                    preview=preview_text[:1500],
                )
        except error.HTTPError as exc:
            return ProbeResult(
                ok=False,
                status_code=exc.code,
                final_url=getattr(exc, "url", url),
                error_type="HTTP_ERROR",
                error_message=str(exc),
            )
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            return ProbeResult(
                ok=False,
                error_type="NETWORK_ERROR",
                error_message=str(reason),
            )


class ArticleProbeApplication:
    def __init__(self, fetcher: UrlFetcher | None = None) -> None:
        self._fetcher = fetcher or UrlFetcher()

    def handle_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("Article probe event received")
        if event.get("httpMethod") == "OPTIONS":
            return cors_preflight_response()

        try:
            payload = parse_body(event)
        except ValueError as exc:
            return bad_request(str(exc))

        raw_url = payload.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            return bad_request("url is required")
        url = raw_url.strip()

        try:
            probe = self._fetcher.fetch(url)
        except Exception:
            logger.exception("Article probe failed unexpectedly")
            return server_error("Failed to probe URL")

        body: Dict[str, Any] = {"ok": probe.ok}
        if probe.ok:
            body["result"] = {
                "statusCode": probe.status_code,
                "finalUrl": probe.final_url or url,
                "contentType": probe.content_type,
                "wordCount": probe.word_count,
                "preview": probe.preview,
            }
        else:
            body["error"] = {
                "type": probe.error_type or "UNKNOWN",
                "message": probe.error_message or "Unable to fetch article",
                "statusCode": probe.status_code,
                "finalUrl": probe.final_url or url,
            }
        return ok(body)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pragma: no cover
    return ArticleProbeApplication().handle_event(event)
