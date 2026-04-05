# ABOUTME: Configuration module that loads Temporal connection settings from
# environment variables with sensible defaults for local development.
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Application settings for connecting to Temporal.

    :param temporal_host: Address of the Temporal server.
    :param temporal_namespace: Temporal namespace to use.
    :param temporal_task_queue: Task queue name for the worker.
    """

    temporal_host: str
    temporal_namespace: str
    temporal_task_queue: str


def load_settings() -> Settings:
    """Load settings from environment variables with defaults.

    Reads ``DURABLE_WORDLE_TEMPORAL_HOST``, ``DURABLE_WORDLE_TEMPORAL_NAMESPACE``,
    and ``DURABLE_WORDLE_TEMPORAL_TASK_QUEUE`` from the environment. Falls back
    to sensible defaults for local development.

    :returns: A populated :class:`Settings` instance.
    """
    return Settings(
        temporal_host=os.environ.get("DURABLE_WORDLE_TEMPORAL_HOST", "localhost:7233"),
        temporal_namespace=os.environ.get(
            "DURABLE_WORDLE_TEMPORAL_NAMESPACE", "default"
        ),
        temporal_task_queue=os.environ.get(
            "DURABLE_WORDLE_TEMPORAL_TASK_QUEUE", "wordle-tasks"
        ),
    )
