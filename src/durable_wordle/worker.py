# ABOUTME: Temporal worker entry point for Durable Wordle. Connects to Temporal,
# registers the workflow and activity, and runs the worker polling loop.
import asyncio
import concurrent.futures
import os

from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker

from durable_wordle.activities import (
    calculate_feedback,
    select_daily_word,
    validate_guess,
)
from durable_wordle.workflow import UserSessionWorkflow

TASK_QUEUE = os.environ.get("TEMPORAL_TASK_QUEUE", "wordle-tasks")


async def run_worker() -> None:
    """Connect to Temporal and run the worker until interrupted.

    Uses Temporal's ``envconfig`` to load connection settings from
    ``TEMPORAL_ADDRESS``, ``TEMPORAL_NAMESPACE``, etc. or a TOML
    config file. The task queue is read from ``TEMPORAL_TASK_QUEUE``.
    """
    connect_config = ClientConfig.load_client_connect_config()
    client = await Client.connect(**connect_config)

    with concurrent.futures.ThreadPoolExecutor() as activity_executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[UserSessionWorkflow],
            activities=[calculate_feedback, select_daily_word, validate_guess],
            activity_executor=activity_executor,
        )
        print(
            f"Worker started on task queue '{TASK_QUEUE}' "
            f"(host={connect_config.get('target_host', 'default')})"
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
