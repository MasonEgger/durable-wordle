# ABOUTME: Shared pytest fixtures for Temporal test environments used
# across workflow and API tests.
from collections.abc import AsyncGenerator

import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture(scope="session")
async def workflow_environment() -> AsyncGenerator[WorkflowEnvironment, None]:
    """Start a single local Temporal test environment shared across all tests."""
    env = await WorkflowEnvironment.start_local()
    yield env
    await env.shutdown()
