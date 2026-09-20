"""HTML → PDF through Gotenberg.

The only renderer in FilmBill (SCOPE §7.3): a document's layout JSON becomes
backend Jinja HTML, this turns that HTML into the PDF, and the in-app preview
shows the same HTML in an iframe. There is deliberately no second, in-process
renderer that could drift away from it.

P0a uses exactly one call site — the `/health/pdf` smoke endpoint — so that a
broken or unreachable Gotenberg is found by a deployment check rather than by
a customer's invoice failing to render.
"""

from __future__ import annotations

import logging

import requests

from ..config import settings

logger = logging.getLogger(__name__)


class PdfRenderError(RuntimeError):
    """Gotenberg could not be reached, or refused to render.

    Raised rather than returning None so a caller cannot mistake a failed
    render for an empty document and store or send it.
    """


def render_html_to_pdf(
    html: str,
    *,
    paper_width_inches: float = 8.27,   # A4
    paper_height_inches: float = 11.69,
    margin_inches: float = 0.4,
    print_background: bool = True,
    timeout_seconds: int | None = None,
) -> bytes:
    """Render a standalone HTML page to PDF bytes via Gotenberg's Chromium route.

    The HTML must be self-contained: Gotenberg runs in its own container on
    the internal network with no route back to this API, so a `<link>` or
    `<img src>` pointing at FilmBill resolves to nothing. Inline the CSS and
    embed images as data: URIs.
    """
    url = f"{settings.gotenberg_url.rstrip('/')}/forms/chromium/convert/html"
    timeout = timeout_seconds if timeout_seconds is not None else settings.gotenberg_timeout_seconds

    # Gotenberg identifies the entry point by the FILENAME "index.html", not
    # by the form field name. Sending it as anything else is a 400 whose
    # message does not say so.
    files = {"files": ("index.html", html.encode("utf-8"), "text/html")}
    data = {
        "paperWidth": str(paper_width_inches),
        "paperHeight": str(paper_height_inches),
        "marginTop": str(margin_inches),
        "marginBottom": str(margin_inches),
        "marginLeft": str(margin_inches),
        "marginRight": str(margin_inches),
        "printBackground": "true" if print_background else "false",
    }

    try:
        response = requests.post(url, files=files, data=data, timeout=timeout)
    except requests.RequestException as exc:
        logger.error("Gotenberg request to %s failed: %s", url, exc)
        raise PdfRenderError(f"Could not reach Gotenberg at {url}: {exc}") from exc

    if response.status_code != 200:
        # Gotenberg puts the real reason in the body; the status alone is
        # not enough to act on.
        detail = response.text.strip()[:500]
        logger.error("Gotenberg returned %s: %s", response.status_code, detail)
        raise PdfRenderError(f"Gotenberg returned {response.status_code}: {detail}")

    if not response.content.startswith(b"%PDF-"):
        # A 200 that is not a PDF means something proxied or rewrote the
        # response; storing it would produce a file no reader can open.
        raise PdfRenderError("Gotenberg returned a 200 that is not a PDF")

    return response.content
