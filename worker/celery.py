"""Celery application configuration with dedicated queues."""

from celery import Celery
from kombu import Queue
from backend.config import settings

celery_app = Celery(
    "prsmith_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "worker.tasks.graph",
        "worker.tasks.review",
        "worker.tasks.repair",
        "worker.tasks.validation",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_default_queue="celery",
    task_queues=[
        Queue("celery", routing_key="celery"),
        Queue("graph_build", routing_key="graph_build"),
        Queue("review", routing_key="review"),
        Queue("repair", routing_key="repair"),
        Queue("validation", routing_key="validation"),
    ],
    task_routes={
        "worker.tasks.graph.*": {"queue": "graph_build"},
        "worker.tasks.review.*": {"queue": "review"},
        "worker.tasks.repair.*": {"queue": "repair"},
        "worker.tasks.validation.*": {"queue": "validation"},
    },
)
