"""Liveness and dependency smoke checks."""

from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from ..middleware.auth import get_current_user
from ..models.user import User, UserGlobalRole
from ..services.pdf_service import PdfRenderError, render_html_to_pdf

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}


def _smoke_page(now: str) -> str:
    """A self-contained A4 page. No external CSS, no images, by design —
    Gotenberg has no route back to this API (see services/pdf_service.py).
    """
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>FilmBill PDF OK</title>
    <style>
      @page {{ size: A4; }}
      body {{ font-family: Helvetica, Arial, sans-serif; margin: 0; }}
      h1 {{ font-size: 28pt; margin: 0 0 12pt; }}
      p {{ font-size: 12pt; color: #333; margin: 0 0 6pt; }}
    </style>
  </head>
  <body>
    <h1>FilmBill PDF OK</h1>
    <p>Rendered at {escape(now)}</p>
    <p>Gotenberg Chromium HTML route, A4.</p>
  </body>
</html>"""


@router.get("/health/pdf")
def health_pdf(current_user: User = Depends(get_current_user)):
    """Render a fixed page through Gotenberg and return the PDF.

    Superadmin-only: it is a deployment check, and an unauthenticated one
    would be a free way to make this instance do CPU work on demand.

    This is the only PDF code in P0a. Real document rendering arrives in P2
    and goes through the same services/pdf_service.py.
    """
    if current_user.role != UserGlobalRole.superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can run the PDF smoke check",
        )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        pdf = render_html_to_pdf(_smoke_page(now))
    except PdfRenderError as exc:
        # 502: this endpoint is fine, its dependency is not. A 500 would
        # point the reader at the wrong container.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="filmbill-pdf-smoke.pdf"',
            "Cache-Control": "no-store",
        },
    )
