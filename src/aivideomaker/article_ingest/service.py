from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text as pdf_extract_text
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfparser import PDFParser
from pydantic import HttpUrl
from trafilatura import extract as trafilatura_extract

from .model import ArticleBundle, ArticleDocument, ArticleMetadata

logger = logging.getLogger(__name__)


class ArticleIngestor:
    """Fetches and normalizes article content from a URL."""

    _MIN_WORDS = 200

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def ingest(self, url: str | HttpUrl) -> ArticleBundle:
        response = requests.get(str(url), timeout=self.timeout, headers={"User-Agent": "aivideomaker/0.1"})
        response.raise_for_status()

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "pdf" in content_type or str(response.url).lower().endswith(".pdf"):
            return self._ingest_pdf(response)

        raw_html = response.text
        extracted = trafilatura_extract(raw_html, include_comments=False, include_tables=False, url=str(url))
        if not extracted or self._looks_sparse(extracted):
            ld_text = self._extract_from_ld_json(raw_html)
            if ld_text and not self._looks_sparse(ld_text):
                logger.info("Using structured articleBody payload from JSON-LD")
                extracted = ld_text
            else:
                if extracted:
                    logger.warning(
                        "Trafilatura extraction sparse (%d words); falling back to BeautifulSoup",
                        len(extracted.split()),
                    )
                else:
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

    def _ingest_pdf(self, response: requests.Response) -> ArticleBundle:
        data = response.content
        text = self._extract_pdf_text(data)
        if not text:
            logger.warning("PDF extraction produced no text for %s", response.url)
        metadata = ArticleMetadata(
            url=str(response.url),
            title=self._extract_pdf_title(data) or str(response.url),
            byline=None,
            published_at=None,
            source=response.url.split("/")[2],
            slug=None,  # type: ignore[arg-type]
        )
        document = ArticleDocument(
            metadata=metadata,
            raw_html=None,
            text=text or "",
            summary=None,
        )
        return ArticleBundle.from_document(document)

    def _fallback_extract(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p")]
        return "\n".join(paragraphs)

    def _extract_from_ld_json(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            payload = (script.string or "").strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            text = self._find_article_body(data)
            if text:
                return text.strip()
        return None

    def _find_article_body(self, data: object) -> Optional[str]:
        if isinstance(data, dict):
            body = data.get("articleBody")
            if isinstance(body, str) and body.strip():
                return body
            graph = data.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    found = self._find_article_body(item)
                    if found:
                        return found
        elif isinstance(data, list):
            for item in data:
                found = self._find_article_body(item)
                if found:
                    return found
        return None

    def _looks_sparse(self, text: str) -> bool:
        words = text.split()
        return len(words) < self._MIN_WORDS

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

    def _extract_pdf_text(self, data: bytes) -> str:
        try:
            buffer = io.BytesIO(data)
            text = pdf_extract_text(buffer)
            return text.strip()
        except Exception as exc:  # pragma: no cover - defensive logging for pdf parsing
            logger.error("Failed to extract text from PDF: %s", exc)
            return ""

    def _extract_pdf_title(self, data: bytes) -> Optional[str]:
        try:
            buffer = io.BytesIO(data)
            parser = PDFParser(buffer)
            document = PDFDocument(parser)
            if document.info:
                info = document.info[0]
                title = info.get("Title")
                if isinstance(title, bytes):
                    return title.decode(errors="ignore").strip()
                if isinstance(title, str):
                    return title.strip()
        except Exception as exc:  # pragma: no cover - metadata extraction best-effort
            logger.warning("Failed to read PDF metadata: %s", exc)
        return None
