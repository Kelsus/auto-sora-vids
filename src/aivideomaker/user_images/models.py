from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class UserImageAsset(BaseModel):
    id: str
    source_uri: str = Field(description="Original location for the image (e.g. local path, future s3:// URI).")
    materialized_path: str = Field(description="Path to the image within the run directory (relative or absolute).")
    title: Optional[str] = None
    summary: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)

    def keyword_set(self) -> set[str]:
        tokens = []
        tokens.extend(self.keywords or [])
        if self.title:
            tokens.append(self.title)
        if self.summary:
            tokens.append(self.summary)
        flattened: list[str] = []
        for item in tokens:
            if not item:
                continue
            flattened.extend(str(item).split())
        return {token.strip().lower() for token in flattened if token and token.strip()}


class UserImagePlan(BaseModel):
    images: List[UserImageAsset] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.images


__all__ = ["UserImageAsset", "UserImagePlan"]

