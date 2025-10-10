from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl, field_validator


def slugify(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    return "-".join(filter(None, cleaned.split("-")))[:80]


class ArticleMetadata(BaseModel):
    url: HttpUrl
    title: str
    byline: Optional[str] = None
    published_at: Optional[datetime] = None
    source: Optional[str] = None
    slug: str

    @field_validator("slug", mode="before")
    @classmethod
    def derive_slug(cls, value: Optional[str], values: dict[str, object]) -> str:
        if isinstance(value, str) and value:
            return slugify(value)
        title = values.get("title")
        if isinstance(title, str) and title:
            return slugify(title)
        url = values.get("url")
        if url:
            return slugify(str(url))
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
