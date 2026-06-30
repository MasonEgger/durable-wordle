# ABOUTME: End-to-end browser smoke tests for the booth-critical flows —
# ABOUTME: keyboard-driven start, guess submission, and the display page.
import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _fill_start_form(page: Page) -> None:
    """Fill the required start-screen fields (first name + email)."""
    page.fill("input[name='first_name']", "Ada")
    page.fill("input[name='email']", "ada@example.com")


def test_start_screen_renders(live_server: str, page: Page) -> None:
    """The start screen shows the PLAY button and madlib form."""
    page.goto(f"{live_server}/")
    expect(page.get_by_role("button", name="PLAY")).to_be_visible()
    expect(page.locator("input[name='first_name']")).to_be_visible()


def test_play_via_keyboard_starts_game(live_server: str, page: Page) -> None:
    """Focusing PLAY and pressing Enter starts the game (the Tab+Enter bug)."""
    page.goto(f"{live_server}/")
    _fill_start_form(page)
    page.get_by_role("button", name="PLAY").focus()
    page.keyboard.press("Enter")
    # The game board only renders once the workflow is running.
    expect(page.locator("#game-board")).to_be_visible(timeout=15000)


def test_guess_submission_succeeds(live_server: str, page: Page) -> None:
    """Typing a valid word and pressing Enter posts the guess (regression: 422)."""
    page.goto(f"{live_server}/")
    _fill_start_form(page)
    page.get_by_role("button", name="PLAY").click()
    expect(page.locator("#game-board")).to_be_visible(timeout=15000)

    for letter in "CRANE":
        page.keyboard.press(letter)
    # Wait for the actual /guess response rather than racing the async post.
    with page.expect_response("**/guess") as response_info:
        page.keyboard.press("Enter")
    status = response_info.value.status
    assert status == 200, f"guess submission returned {status} (422 = the old bug)"

    # The submitted word should now appear as a rendered tile on the board.
    expect(page.locator("#game-board").get_by_text("C").first).to_be_visible(
        timeout=10000
    )


def test_display_page_loads(live_server: str, page: Page) -> None:
    """The /display second screen loads its calibration/attract scaffolding."""
    page.goto(f"{live_server}/display")
    expect(page.locator("#particles")).to_be_attached()
    expect(page.locator("#attract")).to_be_attached()
