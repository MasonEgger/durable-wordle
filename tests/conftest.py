# ABOUTME: Shared pytest fixtures for Temporal test environments used
# across workflow and API tests.
import uuid
from collections.abc import AsyncGenerator, Generator
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment

from durable_wordle.word_lists import is_valid_guess


@pytest_asyncio.fixture(scope="session")
async def workflow_environment() -> AsyncGenerator[WorkflowEnvironment, None]:
    """Start a single local Temporal test environment shared across all tests."""
    env = await WorkflowEnvironment.start_local()
    yield env
    await env.shutdown()


@pytest.fixture(autouse=True)
def mock_dictionary_api() -> Generator[MagicMock, None, None]:
    """Mock the dictionary API to avoid external HTTP calls in tests.

    Returns 200 for words in the bundled word list, 404 otherwise.
    """
    with patch("durable_wordle.activities.requests.get") as mock_get:

        def fake_get(url: str, **kwargs: object) -> MagicMock:
            word = url.rsplit("/", 1)[-1].upper()
            response = MagicMock()
            response.status_code = 200 if is_valid_guess(word) else 404
            return response

        mock_get.side_effect = fake_get
        yield mock_get


@pytest.fixture()
def task_queue() -> str:
    """Generate a unique task queue name per test."""
    return str(uuid.uuid4())


@pytest.fixture()
def activity_environment() -> ActivityEnvironment:
    """Create a Temporal ActivityEnvironment for isolated activity testing."""
    return ActivityEnvironment()
