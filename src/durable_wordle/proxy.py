# ABOUTME: Reverse-proxy for the Temporal Web UI so it can be embedded in the
# ABOUTME: display's iframe (strips frame-busting headers, rewrites asset URLs).
import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

TEMPORAL_UI_BASE = "http://localhost:8233"
_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_CSP_META_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']content-security-policy["\'][^>]*>',
    flags=re.IGNORECASE,
)


async def proxy_to_temporal_ui(upstream_path: str, request: Request) -> Response:
    """Forward a request to the Temporal dev server at ``TEMPORAL_UI_BASE``.

    Strips X-Frame-Options and CSP headers so responses can be embedded in an
    iframe, and rewrites root-relative asset URLs in HTML responses so they
    continue to route through the ``/temporal-ui/`` proxy prefix.

    :param upstream_path: Path on the Temporal server (no leading slash).
    :param request: The incoming HTTP request.
    :returns: Proxied response with frame-busting headers removed.
    """
    query = request.url.query
    target_url = f"{TEMPORAL_UI_BASE}/{upstream_path}"
    if query:
        target_url = f"{target_url}?{query}"

    # Generous timeout: the UI long-polls history with waitNewEvent=true, which
    # the Temporal server holds open until an event or its own timeout.
    timeout = httpx.Timeout(70.0, connect=5.0)
    body = await request.body()
    forward_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "origin", "referer", "content-length")
    }
    forward_headers["accept-encoding"] = "identity"

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as http_client:
        try:
            upstream = await http_client.request(
                request.method,
                target_url,
                headers=forward_headers,
                content=body or None,
            )
        except httpx.TimeoutException:
            # Long-poll exceeded our window — return empty so the UI retries.
            return Response(status_code=204)
        except httpx.HTTPError:
            # Upstream unreachable or dropped the connection mid-response
            # (ConnectError, RemoteProtocolError, ...). Fail soft with 502 so one
            # flaky request doesn't 500 and break the embedded UI.
            return Response(
                content="Temporal UI not available",
                status_code=502,
                media_type="text/plain",
            )

    skip_headers = {"x-frame-options", "content-security-policy", "transfer-encoding"}
    headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in skip_headers
    }

    content = upstream.content
    if "text/html" in upstream.headers.get("content-type", ""):
        # NOTE: these string rewrites are pinned to the current Temporal Web UI
        # build (a SvelteKit app, UI ~2.49). A Temporal upgrade can change the
        # markup (asset paths, the `base: ""` hydration token, inline CSP) and
        # silently break iframe embedding / timeline extraction. The e2e test
        # `test_proxy_rewrites_temporal_ui` is the guard: it fails loudly if
        # these rewrites stop matching.
        text = content.decode("utf-8", errors="replace")
        # Rewrite root-relative asset/link URLs to route through the proxy.
        text = text.replace('src="/', 'src="/temporal-ui/')
        text = text.replace("src='/", "src='/temporal-ui/")
        text = text.replace('href="/', 'href="/temporal-ui/')
        text = text.replace("href='/", "href='/temporal-ui/")
        # Rewrite dynamic ES-module imports in inline scripts (e.g.
        # import("/_app/...")) which the above does not catch.
        text = text.replace('import("/', 'import("/temporal-ui/')
        text = text.replace("import('/", "import('/temporal-ui/")
        # Tell SvelteKit its base path so the client router strips the
        # /temporal-ui prefix and matches its routes correctly.
        text = text.replace('base: ""', 'base: "/temporal-ui"')
        text = text.replace("base: ''", 'base: "/temporal-ui"')
        # Strip the inline CSP meta tag (blocks script loading in the iframe).
        text = _CSP_META_RE.sub("", text)
        content = text.encode("utf-8")
        headers["content-length"] = str(len(content))

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=upstream.headers.get("content-type"),
    )


proxy_router = APIRouter()


@proxy_router.api_route(
    "/temporal-ui/{path:path}", methods=_PROXY_METHODS, include_in_schema=False
)
async def temporal_ui_proxy(path: str, request: Request) -> Response:
    """Reverse-proxy the Temporal UI assets, pages, and base-prefixed API.

    :param path: URL path under the Temporal UI prefix.
    :param request: The incoming HTTP request.
    :returns: Proxied response.
    """
    return await proxy_to_temporal_ui(path, request)


@proxy_router.api_route(
    "/api/v1/{path:path}", methods=_PROXY_METHODS, include_in_schema=False
)
async def temporal_api_proxy(path: str, request: Request) -> Response:
    """Reverse-proxy the Temporal server API so the UI's fetch calls work.

    The Temporal UI (SvelteKit) calls ``/api/v1/...`` using absolute paths. When
    the UI is embedded via the ``/temporal-ui/`` proxy those calls hit this app
    instead of the Temporal server; this route forwards them.

    :param path: URL path under ``/api/v1/``.
    :param request: The incoming HTTP request.
    :returns: Proxied response.
    """
    return await proxy_to_temporal_ui(f"api/v1/{path}", request)
