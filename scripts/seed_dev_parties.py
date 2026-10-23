#!/usr/bin/env python3
"""Put a handful of recognisable parties in the DEV database. Dev only.

For the P1b acceptance walkthrough: an Austrian organisation with a
department and a person, a German organisation, a Swiss one, an American one,
and a private person. Enough to see inheritance, each buyer type and each
VIES outcome without typing six forms.

**It refuses to run anywhere but the dev stack**, and the refusal is the
important part. A seed script that could reach the LAN server-test instance
is a script that will eventually put "ORF (test)" in front of somebody real,
and one that could reach production is worse. Three independent gates, and
any one of them stops it:

  1. `FILMBILL_ENV` must be `dev`, or absent — the dev compose sets nothing,
     while `docker-compose.server-test.yml` sets `server-test`;
  2. the database name must look like a development one (`filmbill`,
     `filmbill_dev`, or a pytest scratch database) — the server-test instance
     uses `filmbill_v2`;
  3. the host must be local (`postgres`, `localhost`, `127.0.0.1`) — the
     server-test instance is reached by its LAN address.

Belt, braces and a third thing, because "which database am I pointed at" is
the question people get wrong at 23:00. `--i-know-what-i-am-doing` overrides
nothing: there is deliberately no override flag.

    docker compose -f docker-compose.dev.yml exec api \\
        python scripts/seed_dev_parties.py

It is idempotent on name: running it twice adds nothing.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

#: Database names this script will write to. `filmbill` is the dev stack's.
DEV_DATABASE_NAMES = ("filmbill", "filmbill_dev", "postgres")
#: Hosts that cannot be the LAN instance or production.
DEV_HOSTS = ("postgres", "localhost", "127.0.0.1", "::1", "")
#: What `docker-compose.server-test.yml` sets, and what this refuses.
FORBIDDEN_ENVS = ("server-test", "production", "prod", "staging")


class NotTheDevStack(RuntimeError):
    """The environment is not the dev stack, so nothing was written."""


def check_environment(
    *, database_url: str, filmbill_env: str | None
) -> None:
    """Raise unless this is unmistakably the dev stack.

    Separated from `main` so a test can exercise every refusal without
    needing a database — the refusal is the behaviour under test, and a test
    that could only check it by pointing at production would be worse than
    none.
    """
    env = (filmbill_env or "").strip().lower()
    if env in FORBIDDEN_ENVS:
        raise NotTheDevStack(
            f"FILMBILL_ENV is {env!r}, so this is not the dev stack. This "
            "script only ever writes to a development database — putting "
            "sample customers in front of somebody real is not a mistake "
            "that can be undone by deleting them."
        )
    if env and env != "dev":
        raise NotTheDevStack(
            f"FILMBILL_ENV is {env!r}, which this script does not recognise "
            "as development. Set it to `dev` only if that is what it is."
        )

    if not database_url:
        raise NotTheDevStack(
            "DATABASE_URL is not set, so there is no way to tell which "
            "database this would write to."
        )

    parsed = urlsplit(database_url)
    name = (parsed.path or "").lstrip("/")
    host = (parsed.hostname or "").lower()

    if not (name in DEV_DATABASE_NAMES or name.startswith("filmbill_pytest")):
        raise NotTheDevStack(
            f"DATABASE_URL points at the database {name!r}, which is not a "
            f"development one. Expected one of {', '.join(DEV_DATABASE_NAMES)}. "
            "The LAN server-test instance uses `filmbill_v2`, and this "
            "script will not write to it."
        )
    if host not in DEV_HOSTS:
        raise NotTheDevStack(
            f"DATABASE_URL points at the host {host!r}, which is not local. "
            "The dev stack reaches Postgres as `postgres` or `localhost`; "
            "anything else could be the LAN instance or production."
        )


#: `(type, name, parent name or None, fields)`. Recognisable on purpose — a
#: real Austrian broadcaster, a German one, a Swiss and an American company —
#: so the acceptance walkthrough's "which one am I looking at" is obvious.
PARTIES = (
    (
        "organisation",
        "ORF (seed)",
        None,
        {
            "is_customer": True,
            "vat_id": "ATU16360407",
            "tax_country": "AT",
            "language": "de",
            "payment_terms_days": 30,
        },
        {"street": "Würzburggasse 30", "zip": "1136", "city": "Wien", "country": "AT"},
    ),
    (
        "department",
        "Redaktion Kultur (seed)",
        "ORF (seed)",
        {},
        None,
    ),
    (
        "person",
        "Maria Huber (seed)",
        "Redaktion Kultur (seed)",
        {"position": "Redakteurin"},
        None,
    ),
    (
        "organisation",
        "Berlin Produktion GmbH (seed)",
        None,
        {
            "is_customer": True,
            "vat_id": "DE123456789",
            "tax_country": "DE",
            "language": "de",
        },
        {"street": "Potsdamer Platz 1", "zip": "10785", "city": "Berlin", "country": "DE"},
    ),
    (
        "organisation",
        "Zürich Film AG (seed)",
        None,
        {
            "is_customer": True,
            "vat_id": "CHE123456789",
            "tax_country": "CH",
            "language": "de",
        },
        {"street": "Bahnhofstrasse 1", "zip": "8001", "city": "Zürich", "country": "CH"},
    ),
    (
        "organisation",
        "Pacific Pictures Inc (seed)",
        None,
        {"is_customer": True, "tax_country": "US", "language": "en"},
        {"street": "1 Sunset Blvd", "zip": "90028", "city": "Los Angeles", "country": "US"},
    ),
    (
        "person",
        "Hans Muster (seed)",
        None,
        {"is_customer": True, "tax_country": "AT"},
        {"street": "Privatgasse 1", "zip": "1010", "city": "Wien", "country": "AT"},
    ),
)


def main() -> int:
    try:
        check_environment(
            database_url=os.environ.get("DATABASE_URL", ""),
            filmbill_env=os.environ.get("FILMBILL_ENV"),
        )
    except NotTheDevStack as error:
        print(f"refusing to run: {error}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from apps.api.database import SessionLocal
    from apps.api.models.company import Company
    from apps.api.models.party import Party, PartyAddress
    from apps.api.services import party_service

    db = SessionLocal()
    try:
        company = db.query(Company).order_by(Company.created_at).first()
        if company is None:
            print(
                "no company in this database — log in once to create one first",
                file=sys.stderr,
            )
            return 1

        existing = {
            row.name
            for row in db.query(Party).filter(Party.company_id == company.id)
        }
        by_name: dict = {}
        added = 0

        for party_type, name, parent_name, fields, address in PARTIES:
            if name in existing:
                by_name[name] = (
                    db.query(Party)
                    .filter(Party.company_id == company.id, Party.name == name)
                    .first()
                )
                continue
            parent = by_name.get(parent_name) if parent_name else None
            row = Party(
                company_id=company.id,
                type=party_type,
                name=name,
                parent_id=parent.id if parent is not None else None,
                tags=["seed"],
                einvoice_routing={},
                **fields,
            )
            db.add(row)
            db.flush()
            party_service.allocate_party_number(db, party=row)
            if address:
                db.add(
                    PartyAddress(
                        company_id=company.id,
                        party_id=row.id,
                        kind="billing",
                        is_default=True,
                        **address,
                    )
                )
            by_name[name] = row
            added += 1

        db.commit()
        print(
            f"company {company.legal_name!r}: added {added} party/parties, "
            f"{len(PARTIES) - added} already there"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
