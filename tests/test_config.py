# ABOUTME: Tests for the configuration module that loads settings from
# environment variables with sensible defaults.
import os
from unittest.mock import patch

from durable_wordle.config import load_settings


def test_default_temporal_host() -> None:
    """Default temporal_host should be localhost:7233."""
    settings = load_settings()
    assert settings.temporal_host == "localhost:7233"


def test_default_temporal_namespace() -> None:
    """Default temporal_namespace should be 'default'."""
    settings = load_settings()
    assert settings.temporal_namespace == "default"


def test_default_temporal_task_queue() -> None:
    """Default temporal_task_queue should be 'wordle-tasks'."""
    settings = load_settings()
    assert settings.temporal_task_queue == "wordle-tasks"


def test_reads_temporal_host_from_env() -> None:
    """Settings should read DURABLE_WORDLE_TEMPORAL_HOST from environment."""
    with patch.dict(os.environ, {"DURABLE_WORDLE_TEMPORAL_HOST": "temporal:7233"}):
        settings = load_settings()
    assert settings.temporal_host == "temporal:7233"


def test_reads_temporal_namespace_from_env() -> None:
    """Settings should read DURABLE_WORDLE_TEMPORAL_NAMESPACE from environment."""
    with patch.dict(os.environ, {"DURABLE_WORDLE_TEMPORAL_NAMESPACE": "production"}):
        settings = load_settings()
    assert settings.temporal_namespace == "production"


def test_reads_temporal_task_queue_from_env() -> None:
    """Settings should read DURABLE_WORDLE_TEMPORAL_TASK_QUEUE from environment."""
    with patch.dict(os.environ, {"DURABLE_WORDLE_TEMPORAL_TASK_QUEUE": "custom-queue"}):
        settings = load_settings()
    assert settings.temporal_task_queue == "custom-queue"
