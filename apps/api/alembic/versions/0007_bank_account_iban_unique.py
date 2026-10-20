"""One IBAN once per company, and the stored form normalised.

Two statements, in this order, and the order is the whole migration:

  1. **Normalise what is there.** Whitespace out, upper case. Every write
     path already stores the normalised form — `core/iban.validate_iban`
     returns it rather than a bool exactly so a caller cannot validate one
     string and store another — but the column has existed since 0005 with
     a docstring that said "stored exactly as entered, spaces and all", and
     a constraint added over a column that might hold `at61 1904 …` next to
     `AT611904…` is a constraint that silently does not hold.
  2. **The unique constraint** on `(company_id, iban)`.

**Duplicates fail the migration; they are not repaired here.** Two rows for
one account in one company is a question about which of them a person meant
to keep, and the answer may be neither — they can differ in label, BIC, bank
name and in which is the default. Merging them would throw that away and
deleting one would throw away whichever was wrong to delete. So this raises
with the companies and IBANs listed, and whoever runs it decides. A migration
that silently loses a row is worse than a migration that stops.

Checked against the dev database (2026-10-20): two accounts in one company,
both already compact and upper case, no duplicates. Checked against the
server-test instance's reported state: one account, label "Test", IBAN
`DE70100110012526517299` — already normalised, so step 1 is a no-op there and
step 2 adds the constraint over a single row.

The same IBAN in a DIFFERENT company stays allowed, which is why the
constraint is on the pair and not on `iban` alone.

Revision ID: 0007_bank_account_iban_unique
Revises: 0006_company_accounting_settings
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_bank_account_iban_unique"
down_revision: Union[str, Sequence[str], None] = "0006_company_accounting_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "uq_company_bank_accounts_company_iban"
TABLE = "company_bank_accounts"


def upgrade() -> None:
    connection = op.get_bind()

    # 1. Normalise in place. `regexp_replace(..., 's'` would stop at the
    #    first match; the 'g' flag is what makes it every run of whitespace.
    connection.execute(
        sa.text(
            f"UPDATE {TABLE} "
            "SET iban = upper(regexp_replace(iban, '\\s', '', 'g')) "
            "WHERE iban <> upper(regexp_replace(iban, '\\s', '', 'g'))"
        )
    )

    # 2. Refuse rather than repair. Reported before the constraint is added,
    #    so the error names the rows instead of being Postgres's "could not
    #    create unique index … Key (company_id, iban)=(…) is duplicated",
    #    which names one pair and stops.
    duplicates = connection.execute(
        sa.text(
            f"SELECT company_id, iban, count(*) AS n FROM {TABLE} "
            "GROUP BY company_id, iban HAVING count(*) > 1 "
            "ORDER BY company_id, iban"
        )
    ).fetchall()
    if duplicates:
        listing = "\n".join(
            f"  company {row.company_id}: {row.iban} × {row.n}"
            for row in duplicates
        )
        raise RuntimeError(
            "This migration adds a unique constraint on "
            f"{TABLE}(company_id, iban), and the table already holds "
            "duplicates:\n"
            f"{listing}\n"
            "Nothing has been deleted or merged — the rows can differ in "
            "label, BIC, bank name and in which one is the default, so which "
            "to keep is a decision, not a repair. Remove the ones you do not "
            "want (or move them to another company) and run the migration "
            "again."
        )

    op.create_unique_constraint(CONSTRAINT, TABLE, ["company_id", "iban"])


def downgrade() -> None:
    # Only the constraint comes off. The normalisation in step 1 is not
    # undone, and must not be: the un-normalised form is not recoverable
    # (where the spaces were is gone), and a downgrade that re-spaced every
    # IBAN at a guess would corrupt data to restore a shape nothing needs.
    op.drop_constraint(CONSTRAINT, TABLE, type_="unique")
