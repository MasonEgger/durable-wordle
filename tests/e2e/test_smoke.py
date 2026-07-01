# ABOUTME: End-to-end browser smoke tests for the booth-critical flows —
# ABOUTME: keyboard-driven start, guess submission, and the display page.
import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def _fill_start_form(page: Page) -> None:
    """Fill the required start-screen fields (first name + email)."""
    page.fill("input[name='first_name']", "Ada")
    page.fill("input[name='email']", "ada@example.com")


def _expect_game_board(page: Page) -> None:
    """Wait until the workflow-backed board is visible."""
    expect(page.locator("#game-board")).to_be_visible(timeout=15000)


def _return_to_start_screen(page: Page) -> None:
    """Swap back to the start screen without a browser refresh."""
    page.get_by_role("link", name=re.compile("START OVER")).click()
    expect(page.locator("#start-screen")).to_be_visible(timeout=10000)


def test_start_screen_renders(live_server: str, page: Page) -> None:
    """The start screen shows the PLAY button and madlib form."""
    page.goto(f"{live_server}/")
    expect(page.get_by_role("button", name="PLAY")).to_be_visible()
    expect(page.locator("input[name='first_name']")).to_be_visible()


def test_start_screen_idle_demo_returns_to_form(live_server: str, page: Page) -> None:
    """The start-screen attract animation exits on the next physical key."""
    page.goto(f"{live_server}/")
    page.evaluate("stopStartIdleDemo(); START_IDLE_DELAY_MS = 50; initStartIdleDemo();")

    expect(page.locator("#start-idle-demo")).to_be_visible(timeout=2000)
    expect(page.locator("#start-idle-demo .wordle-demo__title")).to_have_text(
        "How to play"
    )
    expect(page.locator("#start-idle-demo #game-board .wordle-tile")).to_have_count(30)
    expect(page.locator("#start-idle-demo #game-board .guess-row")).to_have_count(6)
    expect(page.locator("#start-idle-demo .tile-reveal").first).to_be_visible(
        timeout=2500
    )
    expect(page.locator("#start-idle-demo .bg-wordle-absent").first).to_be_visible(
        timeout=2500
    )
    page.keyboard.press("A")

    expect(page.locator("#start-idle-demo")).to_be_hidden()
    expect(page.locator("#start-form")).to_be_visible()
    expect(page.locator("input[name='first_name']")).to_be_focused()
    assert page.locator("input[name='first_name']").input_value() == ""


def test_play_via_click_starts_game(live_server: str, page: Page) -> None:
    """Clicking PLAY starts the game from the initial page load."""
    page.goto(f"{live_server}/")
    _fill_start_form(page)
    page.get_by_role("button", name="PLAY").click()
    _expect_game_board(page)


def test_play_via_enter_starts_game(live_server: str, page: Page) -> None:
    """Pressing Enter from the form starts the game from the initial page load."""
    page.goto(f"{live_server}/")
    _fill_start_form(page)
    page.locator("input[name='email']").focus()
    page.keyboard.press("Enter")
    _expect_game_board(page)


def test_start_over_then_play_via_click_without_refresh(
    live_server: str, page: Page
) -> None:
    """A swapped-in start screen can start the next game by clicking PLAY."""
    page.goto(f"{live_server}/")
    _fill_start_form(page)
    page.get_by_role("button", name="PLAY").click()
    _expect_game_board(page)

    _return_to_start_screen(page)
    _fill_start_form(page)
    page.get_by_role("button", name="PLAY").click()
    _expect_game_board(page)


def test_start_over_then_play_via_enter_without_refresh(
    live_server: str, page: Page
) -> None:
    """A swapped-in start screen can start the next game by pressing Enter."""
    page.goto(f"{live_server}/")
    _fill_start_form(page)
    page.get_by_role("button", name="PLAY").click()
    _expect_game_board(page)

    _return_to_start_screen(page)
    _fill_start_form(page)
    page.locator("input[name='email']").focus()
    page.keyboard.press("Enter")
    _expect_game_board(page)


def test_guess_submission_succeeds(live_server: str, page: Page) -> None:
    """Typing a valid word and pressing Enter posts the guess (regression: 422)."""
    page.goto(f"{live_server}/")
    _fill_start_form(page)
    page.get_by_role("button", name="PLAY").click()
    _expect_game_board(page)

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
    """The /display second screen loads and its external JS runs.

    The particle field is built by static/display.js at load, so a populated
    #particles proves the extracted module executed (not just the HTML shell).
    """
    page.goto(f"{live_server}/display")
    expect(page.locator("#attract")).to_be_attached()
    expect(page.locator("#particles .particle").first).to_be_attached(timeout=5000)


def test_proxy_rewrites_temporal_ui(live_server: str, page: Page) -> None:
    """The /temporal-ui reverse-proxy applies the rewrites that let the Temporal
    UI render inside the display's iframe.

    This is the guard for the proxy's fragile HTML rewriting: a Temporal UI
    upgrade that changes the markup would break these and this fails loudly.
    """
    response = page.request.get(f"{live_server}/temporal-ui/")
    assert response.status == 200, f"proxy returned {response.status}"
    body = response.text()
    # Asset paths and the SvelteKit base must be rewritten under the prefix,
    # and the inline CSP (which blocks iframe scripts) must be stripped.
    assert "/temporal-ui/_app" in body, "asset paths were not rewritten"
    assert 'base: "/temporal-ui"' in body, "SvelteKit base was not rewritten"
    assert "content-security-policy" not in body.lower(), "inline CSP not stripped"
