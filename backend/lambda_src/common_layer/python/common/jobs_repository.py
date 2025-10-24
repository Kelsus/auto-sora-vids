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

    def query_pending_immediate(
        self,
        index_name: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = dict(
            IndexName=index_name,
            KeyConditionExpression=Key("status").eq("PENDING"),
            Limit=limit,
        )
        items: List[Dict[str, Any]] = []
        while len(items) < limit:
            response = self._table.query(**kwargs)
            for item in response.get("Items", []):
                if item.get("job_type") == "IMMEDIATE":
                    items.append(item)
                if len(items) >= limit:
                    break
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
        set_parts = ["#s = :status", "updated_at = :now"]
        remove_names: List[str] = []
        attribute_names: Dict[str, str] = {"#s": "status"}
        attribute_values: Dict[str, Any] = {":status": status, ":now": now_iso}

        value_index = 0
        name_index = 0
        for key, value in attributes.items():
            name_placeholder = f"#f{name_index}"
            attribute_names[name_placeholder] = key
            name_index += 1
            if value is None:
                remove_names.append(name_placeholder)
                continue
            value_placeholder = f":v{value_index}"
            value_index += 1
            set_parts.append(f"{name_placeholder} = {value_placeholder}")
            attribute_values[value_placeholder] = value

        try:
            expression = "SET " + ", ".join(set_parts)
            if remove_names:
                expression += " REMOVE " + ", ".join(remove_names)
            self._table.update_item(
                Key={"jobId": job_id},
                UpdateExpression=expression,
                ExpressionAttributeNames=attribute_names,
                ExpressionAttributeValues=attribute_values,
            )
        except ClientError as exc:  # pragma: no cover
            raise RepositoryError("Failed to update job status") from exc
