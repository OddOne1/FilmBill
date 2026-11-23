# Golden calculation fixtures

One JSON file per scenario. Each has three keys:

* `why` — what the scenario is evidence about, in a sentence.
* `document` — the engine's input, in exactly the shape
  `billing.calc.document_from_data` parses and `POST /calc/preview` accepts.
* `expect` — the numbers, written out by hand and verified independently
  (SCOPE §12 for S1 and S6; the rest are computed in the prompt or stated in
  the file's own `why`).

**They are data files on purpose.** A rounding rule written as a Python
expression is reviewable by a programmer; the same rule as a table of inputs
and expected cents is reviewable by Mathias and by a Steuerberater, which is
the only review that settles whether S1 is right. `test_calc_fixtures.py`
walks this directory, so adding a scenario is adding a file — and
`test_every_fixture_file_is_loaded` fails if the walk ever finds nothing,
because a fixture suite that silently matched no files is the cheapest
possible green.

Money is written as a **string** everywhere, in both the input and the
expectations. A JSON number is a binary float and has already lost the exact
value; `core/rounding.to_decimal` refuses one, and these files must not be
the exception that makes the refusal untestable.
