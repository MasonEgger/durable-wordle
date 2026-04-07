# ABOUTME: Temporal worker entry point for Durable Wordle. Connects to Temporal,
# registers the workflow and activity, and runs the worker polling loop.
import asyncio
import concurrent.futures
import logging
import os
from pathlib import Path

from temporalio.client import Client
from temporalio.envconfig import ClientConfigProfile
from temporalio.worker import Worker

from durable_wordle.activities import (
    calculate_feedback,
    select_word,
    validate_guess,
)
from durable_wordle.workflow import UserSessionWorkflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "wordle-tasks")
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = PROJECT_ROOT / "temporal.toml"


async def run_worker() -> None:
    """Connect to Temporal and run the worker until interrupted.

    Uses Temporal's ``envconfig`` to load connection settings from
    ``TEMPORAL_ADDRESS``, ``TEMPORAL_NAMESPACE``, etc. or a TOML
    config file. The task queue is read from ``TEMPORAL_TASK_QUEUE``.
    """
    profile = ClientConfigProfile.load(config_source=CONFIG_FILE)
    connect_config = profile.to_client_connect_config()
    client = await Client.connect(**connect_config)

    with concurrent.futures.ThreadPoolExecutor() as activity_executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[UserSessionWorkflow],
            activities=[calculate_feedback, select_word, validate_guess],
            activity_executor=activity_executor,
        )
        logging.info(
            "Worker started on task queue '%s' (host=%s)",
            TASK_QUEUE,
            connect_config.get("target_host", "default"),
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
