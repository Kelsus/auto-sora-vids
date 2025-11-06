from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


class ArtifactCleanupError(RuntimeError):
    """Raised when job artifact cleanup fails."""


def delete_job_artifacts(
    job_item: Mapping[str, Any],
    s3_client: Any,
    *,
    logger: Any | None = None,
    extra_keys: Sequence[str] | None = None,
) -> None:
    """Delete S3 artifacts associated with a job.

    Expects the job item to include `output_bucket`, optional `output_prefix`,
    and optional keys such as `final_video_key`, `captions_ass_key`, or `bundle_key`.
    Additional object keys can be supplied via `extra_keys`.
    """

    bucket = job_item.get("output_bucket")
    if not bucket:
        if logger:
            logger.info("No output bucket recorded for job; skipping artifact cleanup")
        return
    bucket = str(bucket)

    try:
        _delete_prefix(s3_client, bucket, job_item.get("output_prefix"), logger=logger)
        keys: list[str] = []
        for field in ("final_video_key", "captions_ass_key", "bundle_key"):
            value = job_item.get(field)
            if isinstance(value, str) and value.strip():
                keys.append(value.strip())
        if extra_keys:
            for value in extra_keys:
                if isinstance(value, str) and value.strip():
                    keys.append(value.strip())
        if keys:
            s3_client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in keys]},
            )
    except Exception as exc:  # pragma: no cover - defensive
        raise ArtifactCleanupError(str(exc)) from exc


def _delete_prefix(s3_client: Any, bucket: str, prefix_value: Any, *, logger: Any | None = None) -> None:
    if not isinstance(prefix_value, str) or not prefix_value.strip():
        return
    prefix = prefix_value.strip()
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents: Iterable[Mapping[str, Any]] = page.get("Contents", []) or []
        objects = [
            {"Key": item["Key"]}
            for item in contents
            if isinstance(item, Mapping) and item.get("Key")
        ]
        if objects:
            s3_client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
    if logger:
        logger.info("Deleted artifacts under prefix %s/%s", bucket, prefix)
