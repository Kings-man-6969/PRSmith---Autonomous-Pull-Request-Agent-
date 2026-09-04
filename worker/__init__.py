"""Celery asynchronous worker package for PRSmith v2."""

from worker.celery import celery_app

__all__ = ["celery_app"]
