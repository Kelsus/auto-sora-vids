#!/usr/bin/env python3
"""Submit and monitor remote video jobs via the serverless pipeline.

This helper posts article URLs to the API Gateway ingest endpoint,
optionally starts the Step Functions state machine immediately, and
polls for completion so you can retrieve the generated S3 artifacts
without exercising spreadsheet ingestion or Google Drive forwarding.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
import requests


DEFAULT_STACK_NAME = "VideoAutomationStack-kelsus-dev"
SUCCESS_STATUSES = {"SUCCEEDED", "COMPLETED"}
TERMINAL_STATUSES = SUCCESS_STATUSES | {"FAILED", "TIMED_OUT", "ABORTED"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trigger remote video generation for a single URL")
    parser.add_argument("url", help="News article URL to process")
    parser.add_argument(
        "--stack-name",
        default=None,
        help="CloudFormation stack name; used to resolve resources when explicit options are omitted",
    )
    parser.add_argument("--profile", default=None, help="AWS profile for boto3 session")
    parser.add_argument("--region", default=None, help="AWS region override")
    parser.add_argument("--api-endpoint", help="API endpoint base (e.g. https://abc123.execute-api.../prod)")
    parser.add_argument("--api-key", help="API key for the ingest endpoint")
    parser.add_argument("--jobs-table", help="DynamoDB table name for job metadata")
    parser.add_argument("--state-machine-arn", help="Step Functions state machine ARN")
    parser.add_argument(
        "--enqueue-only",
        action="store_true",
        help="Create the job record but do not start the Step Functions execution",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll the job until it completes (requires Step Functions or Dynamo access)",
    )
    parser.add_argument("--wait-timeout", type=int, default=900, help="Max seconds to wait when --wait is set")
    parser.add_argument("--poll-interval", type=int, default=30, help="Seconds between polling attempts")
    parser.add_argument(
        "--skip-drive",
        action="store_true",
        help="Set deliver_final_exports=false so the Google Drive forwarder is bypassed.",
    )
    parser.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional metadata fields to persist on the job",
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        help="JSON file containing extra metadata to merge into the request",
    )
    parser.add_argument(
        "--pipeline-config",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Pipeline config overrides included with the job",
    )
    parser.add_argument(
        "--pipeline-config-file",
        type=Path,
        help="JSON file with pipeline_config overrides",
    )
    parser.add_argument(
        "--label",
        help="Convenience metadata label (stored as metadata['label'])",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    session = boto3.session.Session(profile_name=args.profile, region_name=args.region)

    stack_outputs: Dict[str, str] = {}
    if args.stack_name or DEFAULT_STACK_NAME:
        stack_name = args.stack_name or DEFAULT_STACK_NAME
        try:
            stack_outputs = _describe_stack_outputs(session, stack_name)
        except Exception as exc:  # pragma: no cover - diagnostic only
            print(f"⚠️  Unable to load stack outputs for {stack_name}: {exc}", file=sys.stderr)

    api_endpoint = _resolve_api_endpoint(args, stack_outputs)
    api_key = _resolve_api_key(args, stack_outputs)
    jobs_table = _resolve_jobs_table(session, args, stack_outputs)
    state_machine_arn = _resolve_state_machine_arn(session, args, stack_outputs)

    if not api_endpoint or not api_key:
        print("API endpoint and key are required; pass --api-endpoint/--api-key or provide a stack name.", file=sys.stderr)
        sys.exit(1)

    jobs_url = api_endpoint.rstrip("/") + "/jobs"

    try:
        metadata = _build_metadata(args)
    except ValueError as exc:
        print(f"❌  {exc}", file=sys.stderr)
        sys.exit(1)
    pipeline_config = metadata.get("pipeline_config") or {}
    if args.skip_drive:
        pipeline_config["deliver_final_exports"] = False
    if pipeline_config:
        metadata["pipeline_config"] = pipeline_config

    payload: Dict[str, Any] = {"url": args.url, "job_type": "IMMEDIATE"}
    if metadata:
        payload["metadata"] = metadata

    response = requests.post(
        jobs_url,
        json=payload,
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        timeout=30,
    )
    if response.status_code != 201:
        print(f"❌  Job creation failed ({response.status_code}): {response.text}", file=sys.stderr)
        sys.exit(1)

    job_info = response.json()
    job_id = job_info.get("jobId")
    if not job_id:
        print("❌  API response did not contain a jobId", file=sys.stderr)
        sys.exit(1)

    print(f"✅  Created job {job_id}")

    table = None
    if jobs_table:
        table = session.resource("dynamodb").Table(jobs_table)

    record = None
    if table is not None:
        record = _fetch_job_record(table, job_id)

    if args.enqueue_only:
        print("ℹ️  Enqueue-only mode; scheduler or cron will start the execution later.")
    else:
        if not state_machine_arn:
            print(
                "⚠️  State machine ARN not available; skipping execution start. "
                "Use --state-machine-arn or --enqueue-only.",
                file=sys.stderr,
            )
        else:
            execution_input = _build_execution_input(record, args.url, metadata)
            execution_arn = _start_execution(session, state_machine_arn, job_id, execution_input)
            print(f"🚀  Started execution: {execution_arn}")

            if args.wait:
                _wait_for_execution(session, execution_arn, args.wait_timeout, args.poll_interval)

    final_record = record
    if table is not None:
        final_record = _wait_for_job_completion(
            table,
            job_id,
            args.wait_timeout if args.wait else 0,
            args.poll_interval,
        )

    if final_record:
        _print_job_summary(final_record)
    else:
        print("ℹ️  Job record not available yet; check AWS console for progress.")


def _describe_stack_outputs(session: boto3.session.Session, stack_name: str) -> Dict[str, str]:
    cf = session.client("cloudformation")
    stack = cf.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = stack.get("Outputs", [])
    return {entry["OutputKey"]: entry["OutputValue"] for entry in outputs}


def _resolve_api_endpoint(args: argparse.Namespace, outputs: Dict[str, str]) -> Optional[str]:
    if args.api_endpoint:
        return args.api_endpoint
    for key, value in outputs.items():
        if key.startswith("VideoJobsApiEndpoint"):
            return value
    return os.getenv("AUTO_SORA_API_ENDPOINT")


def _resolve_api_key(args: argparse.Namespace, outputs: Dict[str, str]) -> Optional[str]:
    if args.api_key:
        return args.api_key
    for key, value in outputs.items():
        if key.startswith("VideoJobsApiKey"):
            return value
    return os.getenv("AUTO_SORA_API_KEY")


def _resolve_jobs_table(session: boto3.session.Session, args: argparse.Namespace, outputs: Dict[str, str]) -> Optional[str]:
    if args.jobs_table:
        return args.jobs_table
    table = os.getenv("AUTO_SORA_JOBS_TABLE")
    if table:
        return table
    stack_name = args.stack_name or DEFAULT_STACK_NAME
    if not stack_name:
        return None
    return _resolve_physical_resource_id(session, stack_name, "VideoJobsTable")


def _resolve_state_machine_arn(session: boto3.session.Session, args: argparse.Namespace, outputs: Dict[str, str]) -> Optional[str]:
    if args.enqueue_only:
        return None
    if args.state_machine_arn:
        return args.state_machine_arn
    arn = os.getenv("AUTO_SORA_STATE_MACHINE_ARN")
    if arn:
        return arn
    stack_name = args.stack_name or DEFAULT_STACK_NAME
    if not stack_name:
        return None
    return _resolve_physical_resource_id(session, stack_name, "VideoJobStateMachine")


def _resolve_physical_resource_id(session: boto3.session.Session, stack_name: str, logical_id: str) -> Optional[str]:
    cf = session.client("cloudformation")
    try:
        resp = cf.describe_stack_resources(StackName=stack_name, LogicalResourceId=logical_id)
    except cf.exceptions.ClientError:
        return None
    resources = resp.get("StackResources", [])
    if not resources:
        return None
    return resources[0].get("PhysicalResourceId")


def _build_metadata(args: argparse.Namespace) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    if args.label:
        metadata["label"] = args.label

    if args.metadata_file:
        metadata.update(json.loads(args.metadata_file.read_text(encoding="utf-8")))

    for item in args.metadata:
        key, value = _parse_key_value(item)
        metadata[key] = value

    pipeline_config: Dict[str, Any] = {}
    if args.pipeline_config_file:
        pipeline_config.update(json.loads(args.pipeline_config_file.read_text(encoding="utf-8")))

    for item in args.pipeline_config:
        key, value = _parse_key_value(item)
        pipeline_config[key] = value

    if pipeline_config:
        metadata["pipeline_config"] = pipeline_config

    return metadata


def _parse_key_value(entry: str) -> tuple[str, Any]:
    if "=" not in entry:
        raise ValueError(f"Expected KEY=VALUE pair, got '{entry}'")
    key, raw_value = entry.split("=", 1)
    return key.strip(), _coerce_json_value(raw_value.strip())


def _coerce_json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _fetch_job_record(table, job_id: str, attempts: int = 5, delay: float = 1.5):
    for attempt in range(attempts):
        resp = table.get_item(Key={"jobId": job_id}, ConsistentRead=True)
        item = resp.get("Item")
        if item:
            return item
        time.sleep(delay)
    return None


def _build_execution_input(record: Optional[Dict[str, Any]], url: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    if record:
        metadata = record.get("metadata", metadata)
        scheduled = record.get("scheduled_datetime", "")
        job_type = record.get("job_type", "IMMEDIATE")
        return {
            "jobId": record["jobId"],
            "articleUrl": record.get("url", url),
            "scheduledDatetime": scheduled,
            "metadata": metadata,
            "jobType": job_type,
        }

    return {
        "jobId": _slugify(url),
        "articleUrl": url,
        "scheduledDatetime": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata,
        "jobType": "IMMEDIATE",
    }


def _start_execution(session: boto3.session.Session, state_machine_arn: str, job_id: str, payload: Dict[str, Any]) -> str:
    client = session.client("stepfunctions")
    execution_name = _execution_name(job_id)
    response = client.start_execution(
        stateMachineArn=state_machine_arn,
        name=execution_name,
        input=json.dumps(payload),
    )
    return response["executionArn"]


def _execution_name(job_id: str) -> str:
    import uuid

    timestamp = int(time.time())
    suffix = uuid.uuid4().hex[:8]
    base = f"{suffix}-{timestamp}-{job_id}"
    return base[:80]


def _wait_for_execution(session: boto3.session.Session, execution_arn: str, timeout: int, interval: int) -> None:
    client = session.client("stepfunctions")
    deadline = time.time() + timeout
    while True:
        resp = client.describe_execution(executionArn=execution_arn)
        status = resp.get("status")
        if status in TERMINAL_STATUSES:
            if status not in SUCCESS_STATUSES:
                cause = resp.get("cause") or resp.get("error") or "unknown"
                print(f"❌  Execution ended with status {status}: {cause}", file=sys.stderr)
            else:
                output = resp.get("output")
                if output:
                    try:
                        details = json.loads(output)
                        final_key = details.get("finalVideoKey")
                        if final_key:
                            print(f"🎬  Final video key: {final_key}")
                    except json.JSONDecodeError:
                        pass
            return

        if time.time() > deadline:
            print("⚠️  Timed out waiting for Step Functions execution to finish", file=sys.stderr)
            return

        time.sleep(interval)


def _wait_for_job_completion(table, job_id: str, timeout: int, interval: int) -> Optional[Dict[str, Any]]:
    if timeout <= 0:
        return _fetch_job_record(table, job_id)

    deadline = time.time() + timeout
    while True:
        record = _fetch_job_record(table, job_id)
        if record:
            status = record.get("status")
            if status in TERMINAL_STATUSES:
                return record
        if time.time() > deadline:
            return record
        time.sleep(interval)


def _print_job_summary(record: Dict[str, Any]) -> None:
    status = record.get("status", "UNKNOWN")
    print(f"📄  Job status: {status}")
    attributes = record.get("attributes", {})
    bucket = attributes.get("output_bucket")
    prefix = attributes.get("output_prefix")
    if bucket and prefix:
        print(f"📁  Run artifacts: s3://{bucket}/{prefix}/")
        print(f"📝  Prompt bundle: s3://{bucket}/{prefix}/bundle.json")
    final_key = attributes.get("final_video_key")
    if final_key:
        if isinstance(final_key, str) and final_key.startswith("s3://"):
            print(f"🎬  Final video: {final_key}")
        elif bucket:
            print(f"🎬  Final video: s3://{bucket}/{final_key}")
        else:
            print(f"🎬  Final video key: {final_key}")
    elif attributes.get("final_exports_skipped"):
        print("⭕  Final exports skipped; use the run artifacts above to review outputs.")
    if status not in SUCCESS_STATUSES:
        error_message = attributes.get("error_message") or record.get("error")
        if error_message:
            print(f"⚠️  Error: {error_message}", file=sys.stderr)


def _slugify(url: str) -> str:
    safe = [c.lower() if c.isalnum() else "-" for c in url]
    condensed = "".join(safe).strip("-")
    while "--" in condensed:
        condensed = condensed.replace("--", "-")
    return condensed[:64] or "manual-job"


if __name__ == "__main__":
    main()
