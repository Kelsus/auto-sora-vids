#!/usr/bin/env python3
"""Delete DynamoDB job records that lack the camelCase createdAt field."""
from __future__ import annotations

import os
from typing import Any, Dict

import boto3

DEFAULT_TABLE = "VideoAutomationStack-prod-VideoJobsTable9821C425-Y93RXJMCUOMO"

def delete_bad_records(table_name: str) -> int:
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)
    deleted = 0
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
            if "createdAt" in item:
                continue
            job_id = item.get("jobId")
            if not job_id:
                continue
            table.delete_item(Key={"jobId": job_id})
            deleted += 1
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    print(f"Scanned {scanned} items; deleted {deleted} without createdAt")
    return deleted

if __name__ == "__main__":
    table_name = os.environ.get("VIDEO_JOBS_TABLE", DEFAULT_TABLE)
    delete_bad_records(table_name)
