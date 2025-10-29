from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field


class ChartSpec(BaseModel):
    library: str
    width: int
    height: int
    data: Dict[str, Any]
    mark: str
    encoding: Dict[str, Any]
    style: str | None = None
