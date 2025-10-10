from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup
from pydantic import HttpUrl
from trafilatura import extract

from .model import ArticleBundle, ArticleDocument, ArticleMetadata

logger = logging.getLogger(__name__)


class ArticleIngestor:
    """Fetches and normalizes article content from a URL."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def ingest(self, url: str | HttpUrl) -> ArticleBundle:
        response = requests.get(str(url), timeout=self.timeout, headers={"User-Agent": "aivideomaker/0.1"})
        response.raise_for_status()
        raw_html = response.text

        extracted = extract(raw_html, include_comments=False, include_tables=False, url=str(url))
        if not extracted:
            logger.warning("Trafilatura extraction failed; falling back to BeautifulSoup")
            extracted = self._fallback_extract(raw_html)

        metadata = ArticleMetadata(
            url=str(response.url),
            title=self._extract_title(raw_html) or response.url,
            byline=self._extract_author(raw_html),
            published_at=self._extract_published_at(raw_html),
            source=response.url.split("/")[2],
            slug=None,  # type: ignore[arg-type]
        )
        document = ArticleDocument(
            metadata=metadata,
            raw_html=raw_html,
            text=extracted or "",
            summary=None,
        )
        return ArticleBundle.from_document(document)

    def _fallback_extract(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
        return "\n".join(paragraphs)

    def _extract_title(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()
        return None

    def _extract_author(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        meta_author = soup.find("meta", attrs={"name": "author"})
        if meta_author and meta_author.get("content"):
            return meta_author["content"].strip()
        return None

    def _extract_published_at(self, html: str) -> Optional[datetime]:
        soup = BeautifulSoup(html, "html.parser")
        time_tags = soup.find_all("time")
        for tag in time_tags:
            datetime_str = tag.get("datetime") or tag.get_text(strip=True)
            parsed = self._parse_datetime(datetime_str)
            if parsed:
                return parsed
        return None

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None
