"""Job worker Lambda package."""

from job_worker.handler import handler as lambda_handler

__all__ = ["lambda_handler"]
