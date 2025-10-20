from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from .time_utils import utc_now_iso


class RepositoryError(RuntimeError):
    """Raised when a persistence operation cannot be completed."""


@dataclass
class JobsRepository:
    table_name: str

    def __post_init__(self) -> None:
        self._table = boto3.resource("dynamodb").Table(self.table_name)

    def put_job(self, item: Dict[str, Any]) -> None:
        try:
            self._table.put_item(Item=item)
        except ClientError as exc:  # pragma: no cover - boto3 runtime
            raise RepositoryError("Failed to save job") from exc

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        response = self._table.get_item(Key={"jobId": job_id})
        return response.get("Item")

    def query_pending_before(
        self,
        index_name: str,
        scheduled_before_iso: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = dict(
            IndexName=index_name,
            KeyConditionExpression=Key("status").eq("PENDING")
            & Key("scheduled_datetime").lte(scheduled_before_iso),
            Limit=limit,
        )
        items: List[Dict[str, Any]] = []
        while True:
            response = self._table.query(**kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items

    def transition_status(self, job_id: str, expected_status: str, new_status: str) -> bool:
        now_iso = utc_now_iso()
        try:
            self._table.update_item(
                Key={"jobId": job_id},
                UpdateExpression="SET #s = :new_status, updated_at = :now",
                ConditionExpression="#s = :expected",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":new_status": new_status,
                    ":expected": expected_status,
                    ":now": now_iso,
                },
            )
            return True
        except ClientError as exc:  # pragma: no cover
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code == "ConditionalCheckFailedException":
                return False
            raise RepositoryError("Failed to transition job status") from exc

    def update_status(self, job_id: str, status: str, attributes: Dict[str, Any]) -> None:
        now_iso = utc_now_iso()
        expression_parts = ["#s = :status", "updated_at = :now"]
        attribute_names: Dict[str, str] = {"#s": "status"}
        attribute_values: Dict[str, Any] = {":status": status, ":now": now_iso}

        for idx, (key, value) in enumerate(attributes.items()):
            name_placeholder = f"#f{idx}"
            value_placeholder = f":v{idx}"
            expression_parts.append(f"{name_placeholder} = {value_placeholder}")
            attribute_names[name_placeholder] = key
            attribute_values[value_placeholder] = value

        try:
            self._table.update_item(
                Key={"jobId": job_id},
                UpdateExpression="SET " + ", ".join(expression_parts),
                ExpressionAttributeNames=attribute_names,
                ExpressionAttributeValues=attribute_values,
            )
        except ClientError as exc:  # pragma: no cover
            raise RepositoryError("Failed to update job status") from exc
