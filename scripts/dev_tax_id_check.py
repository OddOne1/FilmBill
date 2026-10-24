#!/usr/bin/env python3
"""Run the tax-identifier checks NOW, in the dev stack. Dev only.

The scheduler runs hourly and the first re-check is 90 days out, so watching
it work would otherwise mean waiting. This brings identifiers forward and
runs the task inline, in one command.

**It refuses to run anywhere but the dev stack**, using the same three gates
as `seed_dev_parties.py` — `FILMBILL_ENV`, the database name, the host —
because a script that moves check schedules forward and can invent registry
answers has no business near the LAN instance or production. The gates are
imported from that script rather than copied, so there is one implementation
of "is this the dev stack" and it cannot drift.

    # bring everything due now and check it for real
    docker compose -f docker-compose.dev.yml exec api \
        python scripts/dev_tax_id_check.py --due-now

    # the same, but with a fake registry that answers however you say,
    # so the Needs-attention flow can be seen without a real company
    # changing its name
    docker compose -f docker-compose.dev.yml exec api \
        python scripts/dev_tax_id_check.py --due-now \
        --fake valid --fake-name "Something Else GmbH"

    # age the newest check, to watch max_check_age_days bite
    docker compose -f docker-compose.dev.yml exec api \
        python scripts/dev_tax_id_check.py --age-checks 400

**`--fake` never reaches a registry.** It is the only way to make a
registry's answer change on demand, which acceptance step 7 needs: nobody
can make the Austrian register rename a company to order.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed_dev_parties import NotTheDevStack, check_environment  # noqa: E402

#: The statuses `--fake` accepts. The same five the providers use, so a
#: walkthrough can see every chip without a registry's cooperation.
FAKE_STATUSES = (
    "valid",
    "invalid",
    "unavailable",
    "not_configured",
    "not_checkable",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--due-now",
        action="store_true",
        help="bring every active, checkable identifier forward to now",
    )
    parser.add_argument(
        "--age-checks",
        type=int,
        default=0,
        metavar="DAYS",
        help="backdate the newest check of each identifier by DAYS",
    )
    parser.add_argument(
        "--fake",
        choices=FAKE_STATUSES,
        help="answer from a fake registry instead of a real one",
    )
    parser.add_argument(
        "--fake-name",
        help="the name the fake registry reports (use it to force a change)",
    )
    parser.add_argument(
        "--limit", type=int, default=100, help="how many to check in one go"
    )
    args = parser.parse_args()

    try:
        check_environment(
            database_url=os.environ.get("DATABASE_URL", ""),
            filmbill_env=os.environ.get("FILMBILL_ENV"),
        )
    except NotTheDevStack as error:
        print(f"refusing to run: {error}", file=sys.stderr)
        return 2

    from apps.api.billing.parties.verification import VerificationResult
    from apps.api.database import SessionLocal
    from apps.api.models.party import Party, PartyTaxId, PartyVatCheck
    from apps.api.services import tax_id_checks

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        if args.age_checks:
            from sqlalchemy import text

            # Around the append-only trigger, for one statement, and put
            # straight back. The trigger is the reason a check is evidence;
            # this is a dev tool and says so.
            when = now - timedelta(days=args.age_checks)
            db.execute(
                text(
                    "ALTER TABLE party_vat_checks "
                    "DISABLE TRIGGER party_vat_checks_no_update_delete"
                )
            )
            aged = db.execute(
                text(
                    "UPDATE party_vat_checks SET checked_at = :when "
                    "WHERE id IN (SELECT DISTINCT ON (tax_id_id) id "
                    "FROM party_vat_checks WHERE tax_id_id IS NOT NULL "
                    "ORDER BY tax_id_id, checked_at DESC)"
                ),
                {"when": when},
            ).rowcount
            db.execute(
                text(
                    "ALTER TABLE party_vat_checks "
                    "ENABLE TRIGGER party_vat_checks_no_update_delete"
                )
            )
            db.commit()
            print(f"backdated {aged} check(s) by {args.age_checks} days")

        if args.due_now:
            brought = 0
            for row in (
                db.query(PartyTaxId).filter(PartyTaxId.is_active.is_(True)).all()
            ):
                from apps.api.billing.parties.tax_ids import is_verifiable

                if not is_verifiable(row.kind):
                    continue
                row.next_check_at = now - timedelta(minutes=1)
                brought += 1
            db.commit()
            print(f"brought {brought} identifier(s) forward to now")

        due = tax_id_checks.due_identifiers(db, limit=args.limit, now=now)
        if not due:
            print("nothing is due. Try --due-now.")
            return 0

        for tax_id in due:
            party = db.get(Party, tax_id.party_id)
            if args.fake:
                result = VerificationResult(
                    status=args.fake,
                    provider="vies" if tax_id.kind == "vat" else tax_id.kind,
                    value=tax_id.value,
                    kind=tax_id.kind,
                    country=tax_id.country,
                    detail="A fake answer from scripts/dev_tax_id_check.py.",
                    registered_name=args.fake_name,
                    name_match=(
                        None
                        if not args.fake_name
                        else _match(party.name, args.fake_name)
                    ),
                    raw={"_fake": True},
                )
            else:
                result = tax_id_checks.run_check(db, tax_id=tax_id, party=party)

            tax_id_checks.record_check(
                db,
                tax_id=tax_id,
                party=party,
                result=result,
                trigger_kind="scheduled",
                actor=None,
            )
            flag = " NEEDS ATTENTION" if tax_id.needs_attention else ""
            print(
                f"  {party.name}: {tax_id.kind} {tax_id.value} -> "
                f"{result.status}{flag}"
            )
        db.commit()
        print(f"checked {len(due)} identifier(s)")
        return 0
    finally:
        db.close()


def _match(party_name: str, registry_name: str) -> str:
    from apps.api.billing.parties.name_match import compare_names

    return compare_names(party_name or "", registry_name).result


if __name__ == "__main__":
    raise SystemExit(main())
