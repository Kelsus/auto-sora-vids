#!/usr/bin/env python3
"""Ensure every job record has pk2 = 'JOB'."""
from __future__ import annotations

import os
from typing import Any, Dict

import boto3

DEFAULT_TABLE = "VideoAutomationStack-prod-VideoJobsTable9821C425-Y93RXJMCUOMO"

def ensure_pk2(table_name: str) -> int:
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)
    updated = 0
    scanned = 0
    last_key: Dict[str, Any] | None = None
    while True:
        params: Dict[str, Any] = {}
        if last_key:
            params["ExclusiveStartKey"] = last_key
        response = table.scan(**params)
        items = response.get("Items", [])
        scanned += len(items)
        for item in items:
            if "pk2" in item:
                continue
            job_id = item.get("jobId")
            if not job_id:
                continue
            table.update_item(
                Key={"jobId": job_id},
                UpdateExpression="SET pk2 = :pk",
                ExpressionAttributeValues={":pk": "JOB"},
            )
            updated += 1
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    print(f"Scanned {scanned} items; added pk2 to {updated}")
    return updated

if __name__ == "__main__":
    table = os.environ.get("VIDEO_JOBS_TABLE", DEFAULT_TABLE)
    ensure_pk2(table)
