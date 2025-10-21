from __future__ import annotations

import logging
from typing import Optional

import boto3

_logger = logging.getLogger(__name__)
_ssm_client = None
_parameter_cache: dict[str, str] = {}


def _client():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm")
    return _ssm_client


def get_parameter(name: str, *, decrypt: bool = True, cache: bool = True) -> str:
    """Fetch a parameter value from SSM (with optional caching)."""
    if not name:
        raise ValueError("Parameter name cannot be empty")
    if cache and name in _parameter_cache:
        return _parameter_cache[name]
    response = _client().get_parameter(Name=name, WithDecryption=decrypt)
    value: str = response["Parameter"]["Value"]
    if cache:
        _parameter_cache[name] = value
    return value


def hydrate_env(env_name: str, parameter_name: Optional[str]) -> None:
    """Populate an environment variable from SSM if a parameter name is provided."""
    import os

    if not parameter_name:
        return
    if os.getenv(env_name):
        return
    try:
        os.environ[env_name] = get_parameter(parameter_name)
        _logger.info("Loaded %s from SSM parameter %s", env_name, parameter_name)
    except Exception:  # pragma: no cover - defensive logging
        _logger.exception("Failed to hydrate %s from %s", env_name, parameter_name)
        raise
