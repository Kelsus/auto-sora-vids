from __future__ import annotations

from datetime import datetime
from typing import Optional

from urllib.parse import urlparse

from pydantic import BaseModel, HttpUrl, ValidationInfo, field_validator


def slugify(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    return "-".join(filter(None, cleaned.split("-")))[:80]


def slug_from_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    domain_slug = slugify(parsed.netloc or raw_url)
    path_segments = [segment for segment in (parsed.path or "").split("/") if segment]
    path_slug = slugify(path_segments[-1]) if path_segments else ""

    parts = []
    if domain_slug:
        parts.append(domain_slug)
    if path_slug and path_slug != domain_slug:
        parts.append(path_slug)

    if not parts:
        return slugify(raw_url)

    combined = "-".join(parts)
    trimmed = combined[:80].strip("-")
    return trimmed or slugify(raw_url)


class ArticleMetadata(BaseModel):
    url: HttpUrl
    title: str
    byline: Optional[str] = None
    published_at: Optional[datetime] = None
    source: Optional[str] = None
    slug: str

    @field_validator("slug", mode="before")
    @classmethod
    def derive_slug(cls, value: Optional[str], info: ValidationInfo) -> str:
        if isinstance(value, str) and value:
            return slugify(value)
        data = info.data or {}
        title = data.get("title")
        if isinstance(title, str) and title:
            return slugify(title)
        url = data.get("url")
        if url:
            return slug_from_url(str(url))
        raise ValueError("Cannot derive slug without title or url")


class ArticleDocument(BaseModel):
    metadata: ArticleMetadata
    raw_html: Optional[str] = None
    text: str
    summary: Optional[str] = None


class ArticleBundle(BaseModel):
    """Container returned by ingestion step."""

    article: ArticleDocument
    cleaned_text: str
    word_count: int

    @classmethod
    def from_document(cls, doc: ArticleDocument) -> "ArticleBundle":
        text = doc.text.strip()
        words = text.split()
        return cls(article=doc, cleaned_text=text, word_count=len(words))

    @property
    def slug(self) -> str:
        return self.article.metadata.slug
