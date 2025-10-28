#!/usr/bin/env python3
"""
Push key/value pairs from a .env file into AWS Systems Manager Parameter Store.

Usage:
    python scripts/sync_env_to_ssm.py --stage nahuel --prefix /auto-sora/env

Each variable in the .env file is written to:
    <prefix>/<STAGE>/<KEY>  (upper-case key)

Values are stored as SecureString parameters with overwrite enabled by default.
"""

from __future__ import annotations

import argparse
import pathlib
from typing import Iterable

import boto3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync .env secrets to AWS SSM Parameter Store.")
    parser.add_argument(
        "--env-file",
        type=pathlib.Path,
        default=pathlib.Path(".env"),
        help="Path to the .env file (default: %(default)s)",
    )
    parser.add_argument(
        "--prefix",
        default="/auto-sora/env",
        help="Base prefix for parameters (default: %(default)s)",
    )
    parser.add_argument(
        "--stage",
        default=None,
        help="Stage suffix to append to the prefix (optional)",
    )
    parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Do not overwrite existing parameters (default: overwrite).",
    )
    return parser.parse_args()


def load_env(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f".env file not found at {path}")
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip().upper()] = value.strip()
    return result


def ensure_trailing_slash(prefix: str) -> str:
    return prefix if prefix.endswith("/") else prefix + "/"


def put_parameters(
    client,
    items: Iterable[tuple[str, str]],
    prefix: str,
    overwrite: bool,
) -> None:
    for key, value in items:
        name = f"{prefix}{key}"
        client.put_parameter(
            Name=name,
            Value=value,
            Type="SecureString",
            Overwrite=overwrite,
        )
        print(f"✅  {name}")


def main() -> None:
    args = parse_args()
    env_values = load_env(args.env_file)
    if not env_values:
        print("No environment variables found; nothing to upload.")
        return

    base_prefix = ensure_trailing_slash(args.prefix)
    if args.stage:
        stage_prefix = ensure_trailing_slash(args.stage.upper())
        full_prefix = f"{base_prefix}{stage_prefix}"
    else:
        full_prefix = base_prefix

    client = boto3.client("ssm")
    put_parameters(client, env_values.items(), full_prefix, overwrite=not args.no_overwrite)
    print(f"\nUploaded {len(env_values)} parameters under {full_prefix}")


if __name__ == "__main__":
    main()
