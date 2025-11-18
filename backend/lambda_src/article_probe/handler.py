from __future__ import annotations

from article_probe.app import ArticleProbeApplication


def handler(event, context):  # pragma: no cover - AWS entry
    return ArticleProbeApplication().handle_event(event)
