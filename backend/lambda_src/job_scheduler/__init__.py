"""Scheduled dispatcher Lambda package."""

from job_scheduler.handler import handler as lambda_handler

__all__ = ["lambda_handler"]
