"""The one mapping from untyped document data to engine result to wire shape.

`POST /calc/preview` had these three lines inside its handler. A2's budget
needs the same three — the budget's calculated view is "turn the stored rows
into the engine's input shape and ask the engine" — and a second copy would
be a second answer to "what does the API send back for a line": the preview
and the budget sheet would drift by one field and the web app would need two
renderers for one set of numbers.

So they live here, once, and both callers use them:

  * `routers/calc.py` — the stateless preview, straight from the request body;
  * `services/budget_service.py` — the same shape, assembled from
    `budget_sections`, `budget_lines`, `budget_globals` and
    `budget_adjustments`.

**Nothing in this module reads a database**, and that is deliberate even
though it lives under `services/`: its input is the untyped `{children,
adjustments}` shape that `billing/calc/model.document_from_data` parses, and
its output is the response model. Whoever builds that shape — a request body
or a query — is somebody else's problem. That keeps `billing/calc`'s
import boundary intact (D70, `tests/test_billing_import_boundary.py`).
"""

from typing import Any, Mapping

from ..billing.calc import CalcError, CalcResult, calculate, document_from_data
from ..schemas.calc import CalcPreviewResponse

__all__ = ["CalcError", "calculate_document", "response_from_result"]


def calculate_document(data: Mapping[str, Any]) -> CalcResult:
    """Parse `data` and price it. Raises `CalcError` with the engine's sentence.

    The parse is the engine's own (`document_from_data`), so a third nesting
    level, a duplicate id, an unknown period unit and a value that names
    something instead of being a number are refused with the sentences the
    engine wrote — which are the sentences the client shows, because they
    name the thing somebody has to fix.
    """
    return calculate(document_from_data(data))


def response_from_result(result: CalcResult) -> CalcPreviewResponse:
    """The result as the API sends it: money as strings, tree intact."""
    return CalcPreviewResponse.model_validate(result)
