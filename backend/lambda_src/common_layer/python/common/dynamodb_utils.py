from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping


def normalize_dynamodb_value(value: Any) -> Any:
    """Convert DynamoDB-decoded values (e.g. Decimal) into JSON-safe types."""
    if isinstance(value, Decimal):
        if value == value.to_integral():
            return int(value)
        return float(value)
    if isinstance(value, Mapping):
        return {key: normalize_dynamodb_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_dynamodb_value(v) for v in value]
    if isinstance(value, set):
        return [normalize_dynamodb_value(v) for v in value]
    return value


def serialize_dynamodb_value(value: Any) -> Any:
    """Convert Python values into DynamoDB-safe types (e.g. Decimal for floats)."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {key: serialize_dynamodb_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_dynamodb_value(v) for v in value]
    if isinstance(value, set):
        return [serialize_dynamodb_value(v) for v in value]
    return value
