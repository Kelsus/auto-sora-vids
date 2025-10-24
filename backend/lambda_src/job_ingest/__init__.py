"""Job ingest Lambda package."""

from job_ingest.handler import handler as lambda_handler

__all__ = ["lambda_handler"]
