# ABOUTME: Temporal worker entry point for Durable Wordle. Connects to Temporal,
# registers the workflow and activity, and runs the worker polling loop.
import asyncio
import concurrent.futures

from temporalio.client import Client
from temporalio.worker import Worker

from durable_wordle.activities import validate_guess
from durable_wordle.config import load_settings
from durable_wordle.workflows import UserSessionWorkflow


async def run_worker() -> None:
    """Connect to Temporal and run the worker until interrupted.

    Reads connection settings from environment variables via
    :func:`~durable_wordle.config.load_settings`, then creates a
    :class:`~temporalio.worker.Worker` registered with the game
    workflow and activity.
    """
    settings = load_settings()
    client = await Client.connect(
        settings.temporal_host, namespace=settings.temporal_namespace
    )

    with concurrent.futures.ThreadPoolExecutor() as activity_executor:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[UserSessionWorkflow],
            activities=[validate_guess],
            activity_executor=activity_executor,
        )
        print(
            f"Worker started on task queue '{settings.temporal_task_queue}' "
            f"(host={settings.temporal_host}, namespace={settings.temporal_namespace})"
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(run_worker())
