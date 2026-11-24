"""`POST /calc/preview` — the engine, over HTTP, over nothing stored.

One endpoint, and it is the whole of CLAUDE.md rule 2 in practice: the web
app sends a document and renders the numbers that come back. It does not add
a column, re-round a subtotal, or derive a VAT figure from a net. Every
screen that shows money — the A1 calculator, A2's budget sheet, and every
quote and invoice after that — asks this.

**Stateless, and deliberately so.** Nothing is read from the database and
nothing is written: no draft, no autosave, no audit row. A preview is
arithmetic, and arithmetic that left a trail would make the audit log
unreadable by the time somebody had dragged a slider twenty times.

**`catalog.view`, not an edit permission.** Somebody planning a shoot needs
these numbers and must not need the right to change the price list to get
them. The company context still comes from `require()`, so the endpoint is
members-only and another company's session gets its own 404 from the
dependency — even though this handler never touches a company-scoped row.

**Validation is the engine's.** `services/calc_mapping` refuses a third nesting
level, a duplicate id, an unknown period unit and a quantity that names
something, with its own sentences, and those sentences are what the client
shows. Pydantic's job here stops at "this is well-formed JSON of the right
shape" — see `schemas/calc.py`.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status

from ..billing.calc import apply_vat
from ..middleware.company import Membership, require
from ..schemas.calc import (
    CalcPreviewRequest,
    CalcPreviewResponse,
    CalcVatResponse,
)
from ..services.calc_mapping import (
    CalcError,
    calculate_document,
    response_from_result,
)

router = APIRouter(tags=["calc"])


@router.post("/calc/preview", response_model=CalcPreviewResponse)
def preview_calculation(
    body: CalcPreviewRequest,
    membership: Membership = Depends(require("catalog.view")),
):
    """Calculate a document. Writes nothing, reads nothing, stores nothing.

    A refused document is a 422 carrying the engine's own sentence — "group
    'week-2' is at nesting level 3; groups may be two deep" — because that
    sentence names the thing the person has to fix and a generic
    "unprocessable entity" does not.
    """
    try:
        # `services/calc_mapping` and not `document_from_data` directly: A2's
        # budget sheet asks the engine for the same numbers from stored rows,
        # and one mapping means one answer for what a line's result looks
        # like. See that module's docstring.
        result = calculate_document(body.document.model_dump(mode="json"))
    except CalcError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from None

    response = response_from_result(result)

    if body.vat_rates is not None:
        rates: dict[str, Decimal] = dict(body.vat_rates)
        try:
            response.vat = CalcVatResponse.model_validate(apply_vat(result, rates))
        except CalcError as error:
            # A missing rate is the common case and the message names the
            # bucket: "no VAT rate was given for tax category 'AT-13'". The
            # client cannot fix that by retrying, so it is a 422 and not a
            # 500 — the request asked for VAT without saying what VAT.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
            ) from None

    return response
