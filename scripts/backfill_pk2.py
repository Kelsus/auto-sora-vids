#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill DynamoDB jobs with pk2 partition key set to 'JOB'.",
    )
    parser.add_argument(
        "--table",
        required=True,
        help="Name of the DynamoDB table containing jobs.",
    )
    parser.add_argument(
        "--region",
        default=None,
        help="AWS region for the table (defaults to boto3 configuration).",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS profile to use for boto3 session (optional).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=100,
        help="Number of items fetched per scan request (default: 100).",
    )
    return parser.parse_args()


def build_session(args: argparse.Namespace) -> boto3.Session:
    if args.profile or args.region:
        return boto3.Session(profile_name=args.profile, region_name=args.region)
    return boto3.Session()


def needs_backfill(item: Dict[str, Any]) -> bool:
    return "pk2" not in item or not item["pk2"]


def main() -> None:
    args = parse_args()
    session = build_session(args)
    table = session.resource("dynamodb").Table(args.table)

    scanned = 0
    updated = 0
    last_key: Optional[Dict[str, Any]] = None

    while True:
        scan_kwargs: Dict[str, Any] = {
            "ProjectionExpression": "jobId, pk2",
            "Limit": args.chunk_size,
        }
        if last_key:
            scan_kwargs["ExclusiveStartKey"] = last_key

        response = table.scan(**scan_kwargs)
        items = response.get("Items", [])
        scanned += len(items)

        for item in items:
            job_id = item.get("jobId")
            if not isinstance(job_id, str) or not job_id:
                continue
            if not needs_backfill(item):
                continue
            try:
                table.update_item(
                    Key={"jobId": job_id},
                    UpdateExpression="SET pk2 = :pk",
                    ExpressionAttributeValues={":pk": "JOB"},
                    ConditionExpression="attribute_not_exists(pk2)",
                )
                updated += 1
                print(f"Updated pk2 for jobId={job_id}")
            except ClientError as exc:
                error = exc.response.get("Error", {}).get("Code")
                if error == "ConditionalCheckFailedException":
                    continue
                raise

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    print(f"Scan complete. Examined {scanned} items; updated {updated} records.")


if __name__ == "__main__":
    main()
