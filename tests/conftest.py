# ABOUTME: Shared pytest fixtures for Temporal test environments used
# across workflow and API tests.
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment


@pytest_asyncio.fixture(scope="session")
async def workflow_environment() -> WorkflowEnvironment:
    """Start a single local Temporal test environment shared across all tests."""
    env = await WorkflowEnvironment.start_local()
    yield env
    await env.shutdown()
