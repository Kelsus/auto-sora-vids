from __future__ import annotations

import json
from typing import Any

from article_probe.app import ArticleProbeApplication, ProbeResult, UrlFetcher


class StubFetcher(UrlFetcher):
    def __init__(self, result: ProbeResult) -> None:
        super().__init__(timeout=0.1)
        self.result = result
        self.calls: list[str] = []

    def fetch(self, url: str) -> ProbeResult:  # type: ignore[override]
        self.calls.append(url)
        return self.result


def make_event(url: Any) -> dict[str, Any]:
    return {
        "httpMethod": "POST",
        "body": json.dumps({"url": url}),
    }


def test_article_probe_success():
    fetcher = StubFetcher(
        ProbeResult(
            ok=True,
            status_code=200,
            final_url="https://example.com/final",
            content_type="text/html",
            word_count=120,
            preview="Example text",
        )
    )
    app = ArticleProbeApplication(fetcher=fetcher)

    response = app.handle_event(make_event("https://example.com"))
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["ok"] is True
    assert body["result"]["statusCode"] == 200
    assert body["result"]["finalUrl"] == "https://example.com/final"
    assert fetcher.calls == ["https://example.com"]


def test_article_probe_failure():
    fetcher = StubFetcher(
        ProbeResult(
            ok=False,
            status_code=403,
            final_url="https://blocked.example.com",
            error_type="HTTP_ERROR",
            error_message="403 Forbidden",
        )
    )
    app = ArticleProbeApplication(fetcher=fetcher)

    response = app.handle_event(make_event("https://blocked.example.com"))
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["ok"] is False
    assert body["error"]["type"] == "HTTP_ERROR"
    assert body["error"]["statusCode"] == 403


def test_article_probe_requires_url():
    app = ArticleProbeApplication(fetcher=StubFetcher(ProbeResult(ok=True)))
    response = app.handle_event({"httpMethod": "POST", "body": json.dumps({})})
    assert response["statusCode"] == 400
