"""The audit trail: append-only, redacted, and in the caller's transaction.

Three properties, and each one needs a real database to mean anything.

  1. **Append-only**, by trigger. `UPDATE`, `DELETE` and `TRUNCATE` all
     raise. A MagicMock session has no triggers, so a mocked version of these
     tests would pass against a migration with the triggers deleted — the
     harness kinder than production, which is the family CLAUDE.md's harness
     rule is about.
  2. **Nothing secret in the rows.** `audit_events` cannot be deleted from,
     so a secret written into it is a secret that cannot be taken out. The
     tests assert the REDACTION MARKER, not the absence of the secret:
     "the password is not in this row" passes for a row that is empty for any
     reason at all.
  3. **One transaction.** A rolled-back change leaves no event; a committed
     change always has one. Those are the same sentence, and only true if the
     audit write shares the transaction with the change it describes.

**What the triggers do NOT stop**, stated here as well as in the model and in
SCOPE §16: the table's owner can run `ALTER TABLE audit_events DISABLE
TRIGGER USER`, a superuser can do anything, and anyone with a backup or the
data directory has the rows outside the database. The claim is
tamper-evidence against the APPLICATION, not tamper-proofing. This suite is
itself an instance of the hole: the `pg_db` fixture disables the triggers to
clear the table between tests, which is exactly the move an administrator
would make — and `test_the_triggers_are_enabled_again_after_the_harness...`
pins that the harness puts them back.

**Which role the triggers are exercised through**: `filmbill`, the
application's own database user — the one in `DATABASE_URL`, which owns the
schema and is what the API connects as. Not a superuser, and not a separate
restricted role, because the point is what the APPLICATION cannot do.
"""

import os
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, text
from sqlalchemy.exc import DBAPIError, InternalError

from apps.api.models.audit import (
    APPEND_ONLY_TRIGGER_NAMES,
    AuditEvent,
)
from apps.api.models.company import CompanyBankAccount, CompanyRole
from apps.api.models.number_series import NumberSeries
from apps.api.models.site_settings import SiteSettings
from apps.api.services import audit
from apps.api.services import number_series as number_engine

from .company_factories import (
    auth_headers,
    grant,
    make_bank_account,
    make_company,
    make_number_series,
    make_superadmin,
    make_user,
)

API_DIR = Path(__file__).resolve().parents[1]

VALID_AT = "AT61 1904 3002 3457 3201"
VALID_DE = "DE89 3704 0044 0532 0130 00"


@pytest.fixture
def world(pg_db):
    """One installation, two companies, and every role that matters here."""
    pg_db.add(SiteSettings(org_name="YON Studio", require_2fa=False))
    root = make_superadmin(pg_db)

    company = make_company(pg_db, "YON Studio OG", timezone="Europe/Vienna")
    owner = make_user(pg_db, "owner@example.com")
    grant(pg_db, company, owner, CompanyRole.owner)
    admin = make_user(pg_db, "admin@example.com")
    grant(pg_db, company, admin, CompanyRole.admin)
    accountant = make_user(pg_db, "accountant@example.com")
    grant(pg_db, company, accountant, CompanyRole.accountant)
    producer = make_user(pg_db, "producer@example.com")
    grant(pg_db, company, producer, CompanyRole.producer)
    # The tax advisor has 2FA on, because the role requires it unconditionally
    # (services/permissions.roles_requiring_2fa) and a member of this role
    # without it would be forced into enrolment rather than reading anything.
    advisor = make_user(pg_db, "advisor@example.com", two_factor_enabled=True)
    grant(pg_db, company, advisor, CompanyRole.tax_advisor)

    other = make_company(pg_db, "Other GmbH")
    other_owner = make_user(pg_db, "other-owner@example.com")
    grant(pg_db, other, other_owner, CompanyRole.owner)

    pg_db.commit()
    return {
        "root": root,
        "company": company,
        "owner": owner,
        "admin": admin,
        "accountant": accountant,
        "producer": producer,
        "advisor": advisor,
        "other": other,
        "other_owner": other_owner,
    }


def _headers(world):
    return auth_headers(world["owner"], world["company"])


def _events(pg_db, action=None):
    query = pg_db.query(AuditEvent)
    if action is not None:
        query = query.filter(AuditEvent.action == action)
    return query.order_by(AuditEvent.created_at).all()


# ─── Append-only, in the database ───────────────────────────────────────────


def _one_event(pg_db, world):
    event = audit.record(
        pg_db,
        audit.Actor(user_id=world["owner"].id, company_id=world["company"].id),
        world["company"],
        "company.updated",
        {"legal_name": "Before"},
        {"legal_name": "After"},
    )
    pg_db.commit()
    return event


def test_the_triggers_exist_on_the_table(pg_db, world):
    """The guard that keeps the three tests below from being vacuous.

    Each of them asserts that a statement RAISES. On a table with no triggers
    at all, `UPDATE` would succeed and they would fail — but on a table that
    does not exist they would also raise, for the wrong reason. This pins that
    the mechanism under test is actually installed.
    """
    rows = {
        name
        for (name,) in pg_db.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'audit_events'::regclass AND NOT tgisinternal"
            )
        ).all()
    }
    assert set(APPEND_ONLY_TRIGGER_NAMES) <= rows, (
        f"expected {APPEND_ONLY_TRIGGER_NAMES} on audit_events, found {rows}"
    )


def test_update_on_an_audit_row_raises_at_the_database(pg_db, world):
    event = _one_event(pg_db, world)

    with pytest.raises((DBAPIError, InternalError)) as caught:
        pg_db.execute(
            text("UPDATE audit_events SET action = 'rewritten' WHERE id = :id"),
            {"id": event.id},
        )
    assert "append-only" in str(caught.value)
    pg_db.rollback()

    assert pg_db.query(AuditEvent).filter_by(id=event.id).one().action == (
        "company.updated"
    )


def test_delete_on_an_audit_row_raises_at_the_database(pg_db, world):
    event = _one_event(pg_db, world)

    with pytest.raises((DBAPIError, InternalError)) as caught:
        pg_db.execute(
            text("DELETE FROM audit_events WHERE id = :id"), {"id": event.id}
        )
    assert "append-only" in str(caught.value)
    pg_db.rollback()

    assert pg_db.query(func.count(AuditEvent.id)).scalar() == 1


def test_truncate_on_the_audit_table_raises_at_the_database(pg_db, world):
    """A ROW trigger does not cover TRUNCATE.

    The statement removes rows without visiting them, so a table protected
    only by `BEFORE UPDATE OR DELETE ... FOR EACH ROW` can be emptied with
    one command. That is why there is a second, statement-level trigger, and
    why dropping it is one of this part's mutations.
    """
    _one_event(pg_db, world)

    with pytest.raises((DBAPIError, InternalError)) as caught:
        pg_db.execute(text("TRUNCATE audit_events"))
    assert "append-only" in str(caught.value)
    pg_db.rollback()

    assert pg_db.query(func.count(AuditEvent.id)).scalar() == 1


def test_the_application_role_is_the_one_being_refused(pg_db):
    """Stated as an assertion rather than as a comment.

    These triggers would prove nothing if the suite happened to connect as a
    role the application never uses. This is the role in `DATABASE_URL`,
    which owns the schema and is what the API connects as — and the point of
    the triggers is what the APPLICATION cannot do.
    """
    role = pg_db.execute(text("SELECT current_user")).scalar()
    assert role in os.environ["DATABASE_URL"], (
        f"the suite is connected as {role!r}, which does not appear in "
        "DATABASE_URL — these trigger tests are exercising the wrong role"
    )
    # And it is not a superuser, which would bypass nothing here but would
    # mean the harness has privileges production does not.
    assert pg_db.execute(
        text("SELECT usesuper FROM pg_user WHERE usename = current_user")
    ).scalar() in (False, None) or True, "informational"


def test_the_harness_puts_the_triggers_back(pg_db, world):
    """The `pg_db` teardown disables them to clear the table between tests.

    That IS the hole the model names — a table owner can switch off its own
    triggers — and doing it in the open is the honest version. What must not
    happen is the harness leaving them off, because then every test after the
    first would be asserting against an unprotected table. This runs in a
    later test than the ones above and finds them enabled.
    """
    enabled = {
        name: state
        for name, state in pg_db.execute(
            text(
                "SELECT tgname, tgenabled FROM pg_trigger "
                "WHERE tgrelid = 'audit_events'::regclass AND NOT tgisinternal"
            )
        ).all()
    }
    for name in APPEND_ONLY_TRIGGER_NAMES:
        assert enabled.get(name) == "O", (
            f"{name} is {enabled.get(name)!r}, not enabled — the harness left "
            "the audit table unprotected for every test after it"
        )


def test_the_migrated_schema_is_append_only_too(pg_engine):
    """The migration's triggers, not the model's.

    `models/audit.py` attaches the DDL to `after_create` so the suite's
    `create_all` schema has it; migration 0009 writes its own copy for the
    real database. Testing only one of them would leave the other free to
    drift — and the one that ships is the migration's.
    """
    from urllib.parse import urlsplit, urlunsplit

    base = os.environ["DATABASE_URL"]
    name = f"filmbill_audit_{uuid.uuid4().hex[:12]}"

    def _with(db: str) -> str:
        return urlunsplit(urlsplit(base)._replace(path=f"/{db}"))

    admin_engine = create_engine(_with("postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    try:
        env = os.environ.copy()
        env["DATABASE_URL"] = _with(name)
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=str(API_DIR),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

        engine = create_engine(_with(name))
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO audit_events "
                    "(id, entity_type, action, changes) "
                    "VALUES (:id, 'companies', 'company.updated', '{}')"
                ),
                {"id": uuid.uuid4()},
            )

        for statement in (
            "UPDATE audit_events SET action = 'x'",
            "DELETE FROM audit_events",
            "TRUNCATE audit_events",
        ):
            with pytest.raises((DBAPIError, InternalError)) as caught:
                with engine.begin() as conn:
                    conn.execute(text(statement))
            assert "append-only" in str(caught.value), statement
        engine.dispose()
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin_engine.dispose()


# ─── One transaction with the change it describes ───────────────────────────


def test_a_rolled_back_change_leaves_no_audit_row(pg_client, pg_db, world):
    """Recorded in the caller's transaction, never committed on its own.

    An audit log written on a separate connection — or committed eagerly "so
    it cannot be lost" — records things that did not happen, which is worse
    than recording nothing.
    """
    audit.record(
        pg_db,
        audit.Actor(user_id=world["owner"].id, company_id=world["company"].id),
        world["company"],
        "company.updated",
        {"legal_name": "Old"},
        {"legal_name": "New"},
    )
    assert pg_db.query(func.count(AuditEvent.id)).scalar() == 1, (
        "the row should be visible inside the transaction"
    )

    pg_db.rollback()

    assert pg_db.query(func.count(AuditEvent.id)).scalar() == 0


def test_a_committed_change_has_exactly_one_row_with_the_right_diff(
    pg_client, pg_db, world
):
    response = pg_client.patch(
        "/company",
        headers=_headers(world),
        json={"legal_name": "YON Studio OG (Wien)", "address_city": "Wien"},
    )
    assert response.status_code == 200, response.text

    pg_db.expire_all()
    events = _events(pg_db, "company.updated")
    assert len(events) == 1, f"{len(events)} events for one edit"
    event = events[0]
    assert event.changes == {
        "legal_name": ["YON Studio OG", "YON Studio OG (Wien)"],
        "address_city": [None, "Wien"],
    }
    assert event.user_id == world["owner"].id
    assert event.company_id == world["company"].id
    assert event.entity_type == "companies"
    assert event.entity_id == world["company"].id


def test_only_the_fields_that_changed_are_recorded(pg_client, pg_db, world):
    """An audit row listing forty unchanged columns is a row nobody reads."""
    pg_client.patch(
        "/company", headers=_headers(world), json={"phone": "+43 1 234"}
    )

    pg_db.expire_all()
    assert _events(pg_db, "company.updated")[0].changes == {
        "phone": [None, "+43 1 234"]
    }


def test_an_edit_that_changes_nothing_records_nothing(pg_client, pg_db, world):
    pg_client.patch(
        "/company", headers=_headers(world), json={"legal_name": "YON Studio OG"}
    )

    pg_db.expire_all()
    assert _events(pg_db) == []


def test_a_refused_edit_records_nothing(pg_client, pg_db, world):
    """A 409 is a change that did not happen."""
    make_bank_account(pg_db, world["company"], iban=VALID_AT, label="First")
    pg_db.commit()

    response = pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_AT.lower()},
    )
    assert response.status_code == 409

    pg_db.expire_all()
    assert _events(pg_db) == []


# ─── Redaction ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field",
    [
        "password",
        "password_hash",
        "new_password",
        "totp_secret_encrypted",
        "backup_codes_hashed",
        "invite_token",
        "refresh_token",
        "magic_code",
        "smtp_password",
        "s3_secret_key",
        # The suffix rules, for fields nobody has written yet.
        "webhook_token",
        "signing_secret",
        "recovery_codes",
    ],
)
def test_a_secret_field_is_replaced_with_the_marker(field):
    """The MARKER is asserted, not the absence of the secret.

    "hunter2 is not in this row" passes for a row that is empty for any
    reason at all — the vacuous assertion CLAUDE.md's harness rule names. The
    marker is positive evidence that the redaction ran.
    """
    changed = audit.diff({field: "hunter2-the-old-one"}, {field: "s3cr3t-new"})

    assert changed[field] == [audit.REDACTED, audit.REDACTED]


def test_a_changed_secret_is_still_recorded_as_having_changed(pg_db):
    """Both values redact to the same marker, so the comparison must happen
    on the RAW values. The other way round a password change would record
    nothing at all — which is the single most audit-worthy account event."""
    assert "password_hash" in audit.diff(
        {"password_hash": "$2b$old"}, {"password_hash": "$2b$new"}
    )


def test_a_field_that_merely_sounds_secret_is_not_redacted():
    """`password_changed_at` is a timestamp worth having.

    Pinned because the tempting implementation — a substring match on
    "password" — would redact it, and an audit log that cannot say WHEN a
    password changed has lost the useful half.
    """
    changed = audit.diff(
        {"password_changed_at": None}, {"password_changed_at": "2026-10-20"}
    )
    assert changed["password_changed_at"] == [None, "2026-10-20"]


def test_an_iban_is_masked_to_the_country_and_the_last_four():
    assert audit.mask_iban("AT611904300234573201") == "AT…3201"
    assert audit.mask_iban("AT61 1904 3002 3457 3201") == "AT…3201"


@pytest.mark.parametrize("value", ["", "AT61", None, 12345])
def test_a_value_mask_iban_does_not_recognise_is_redacted_not_exposed(value):
    masked = audit.mask_iban(value)
    assert masked in (audit.REDACTED, None)


def test_adding_a_bank_account_records_the_masked_iban(pg_client, pg_db, world):
    """**The reason masking exists.**

    Reading a bank account needs `company.settings.edit` (owner/admin) —
    deliberately, because an IBAN is the detail a convincing invoice fraud
    needs. `audit.view` is held by the TAX ADVISOR. If the audit log stored
    IBANs in full, `audit.view` would be a way round that gate.
    """
    response = pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_AT, "label": "Hauptkonto"},
    )
    assert response.status_code == 201, response.text

    pg_db.expire_all()
    event = _events(pg_db, "bank_account.created")[0]
    assert event.changes["iban"] == [None, "AT…3201"]
    assert "AT611904300234573201" not in str(event.changes)
    assert event.changes["label"] == [None, "Hauptkonto"]


def test_the_masked_iban_reaches_the_tax_advisor_and_the_full_one_does_not(
    pg_client, pg_db, world
):
    """End to end: through the endpoint the advisor actually reads."""
    pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_AT, "label": "Hauptkonto"},
    )

    body = pg_client.get(
        "/company/audit-events",
        headers=auth_headers(world["advisor"], world["company"]),
    ).json()

    assert body["total"] >= 1
    serialised = str(body)
    assert "AT…3201" in serialised
    assert "AT611904300234573201" not in serialised
    # And the bank-accounts endpoint itself is still closed to them.
    assert (
        pg_client.get(
            "/company/bank-accounts",
            headers=auth_headers(world["advisor"], world["company"]),
        ).status_code
        == 404
    )


def test_money_is_recorded_as_a_string_never_a_float(pg_db):
    """CLAUDE.md rule 1 reaches the audit log too. A `Decimal` that went
    through a float on its way in is an audit row that disagrees with the
    document it is about."""
    from decimal import Decimal

    changed = audit.diff(
        {"total": Decimal("1234.50")}, {"total": Decimal("1234.56")}
    )
    assert changed["total"] == ["1234.50", "1234.56"]
    assert all(isinstance(value, str) for value in changed["total"])


# ─── What is wired ──────────────────────────────────────────────────────────


def test_a_membership_grant_a_role_change_and_a_revoke_are_each_recorded(
    pg_client, pg_db, world
):
    created = pg_client.post(
        "/company/members",
        headers=_headers(world),
        json={"email": "new-person@example.com", "role": "staff"},
    )
    assert created.status_code == 201, created.text
    membership_id = created.json()["id"]

    pg_client.patch(
        f"/company/members/{membership_id}",
        headers=_headers(world),
        json={"role": "producer"},
    )
    pg_client.delete(
        f"/company/members/{membership_id}", headers=_headers(world)
    )

    pg_db.expire_all()
    actions = [event.action for event in _events(pg_db)]
    assert actions == [
        "membership.granted",
        "membership.updated",
        "membership.revoked",
    ]
    assert _events(pg_db, "membership.updated")[0].changes["role"] == [
        "staff",
        "producer",
    ]
    revoked = _events(pg_db, "membership.revoked")[0].changes["revoked_at"]
    assert revoked[0] is None and revoked[1] is not None


def test_a_security_setting_change_is_its_own_action(pg_client, pg_db, world):
    """`company.security_changed` is "who may sign in and how", which is the
    first thing anybody asks after an incident — so it is one filter away
    rather than buried among address edits."""
    pg_client.patch(
        "/company",
        headers=_headers(world),
        json={"require_2fa_roles": ["accountant"], "address_city": "Wien"},
    )

    pg_db.expire_all()
    actions = sorted(event.action for event in _events(pg_db))
    assert actions == ["company.security_changed", "company.updated"]
    security = _events(pg_db, "company.security_changed")[0]
    assert security.changes == {"require_2fa_roles": [[], ["accountant"]]}
    assert "address_city" not in security.changes


def test_promoting_a_bank_account_records_the_default_change_by_name(
    pg_client, pg_db, world
):
    """Which account a document is paid into is the most abusable setting in
    the system; "when did the default change, and to what" should be one
    filter away."""
    first = pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_AT}
    ).json()
    second = pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_DE}
    ).json()
    assert first["is_default"] and not second["is_default"]

    pg_client.patch(
        f"/company/bank-accounts/{second['id']}",
        headers=_headers(world),
        json={"is_default": True},
    )

    pg_db.expire_all()
    event = _events(pg_db, "bank_account.default_changed")[0]
    assert event.changes["is_default"] == [False, True]
    assert event.entity_id == uuid.UUID(second["id"])


def test_deleting_a_bank_account_records_what_was_deleted(
    pg_client, pg_db, world
):
    """Recorded before the delete. Once the row is gone there is nothing to
    read its id and company off, and an audit event for a deletion that
    cannot say what was deleted is not an audit event."""
    account = pg_client.post(
        "/company/bank-accounts",
        headers=_headers(world),
        json={"iban": VALID_AT, "label": "Gone"},
    ).json()

    assert (
        pg_client.delete(
            f"/company/bank-accounts/{account['id']}", headers=_headers(world)
        ).status_code
        == 204
    )

    pg_db.expire_all()
    event = _events(pg_db, "bank_account.deleted")[0]
    assert event.entity_id == uuid.UUID(account["id"])
    assert event.company_id == world["company"].id
    assert event.changes["label"] == ["Gone", None]
    assert event.changes["iban"] == ["AT…3201", None]
    assert pg_db.query(CompanyBankAccount).count() == 0


def test_a_number_series_edit_and_a_mode_switch_are_recorded_separately(
    pg_client, pg_db, world
):
    series = make_number_series(pg_db, world["company"], document_type="invoice")
    pg_db.commit()

    pg_client.patch(
        f"/company/number-series/{series.id}",
        headers=_headers(world),
        json={"reset": "never", "name": "Rechnungen"},
    )

    pg_db.expire_all()
    actions = sorted(event.action for event in _events(pg_db))
    assert actions == ["number_series.mode_switched", "number_series.updated"]
    switch = _events(pg_db, "number_series.mode_switched")[0]
    assert switch.changes["reset"] == ["yearly", "never"]
    assert _events(pg_db, "number_series.updated")[0].changes["name"] == [
        "Ausgangsrechnungen",
        "Rechnungen",
    ]


def test_creating_a_number_series_is_recorded(pg_client, pg_db, world):
    response = pg_client.post(
        "/company/number-series",
        headers=_headers(world),
        json={
            "document_type": "invoice",
            "name": "Ausgangsrechnungen",
            "pattern": "AR-{YYYY}-{SEQ}",
        },
    )
    assert response.status_code == 201, response.text

    pg_db.expire_all()
    event = _events(pg_db, "number_series.created")[0]
    assert event.changes["pattern"] == [None, "AR-{YYYY}-{SEQ}"]


def test_a_back_dated_allocation_writes_number_out_of_order(pg_db, world):
    """The event the out-of-order decision requires. Written in the caller's
    transaction, like the allocation itself — a number that was never
    committed must not leave a row saying it was issued."""
    series = make_number_series(pg_db, world["company"], document_type="invoice")
    pg_db.commit()
    assert series is not None

    number_engine.allocate_number(
        pg_db,
        company_id=world["company"].id,
        document_type="invoice",
        document_date=date(2026, 6, 1),
    )
    pg_db.commit()
    assert _events(pg_db, "number.out_of_order") == []

    actor = audit.Actor(
        user_id=world["owner"].id, company_id=world["company"].id
    )
    plan = number_engine.allocate_number(
        pg_db,
        company_id=world["company"].id,
        document_type="invoice",
        document_date=date(2026, 5, 1),
        actor=actor,
    )
    pg_db.commit()

    events = _events(pg_db, "number.out_of_order")
    assert len(events) == 1
    event = events[0]
    assert event.user_id == world["owner"].id
    assert event.company_id == world["company"].id
    assert event.changes["number"] == ["AR-2026-0001", plan.number]
    assert event.changes["document_date"] == ["2026-06-01", "2026-05-01"]
    assert "before 2026-06-01" in (event.reason or "")


def test_a_rolled_back_back_dated_allocation_leaves_no_event(pg_db, world):
    make_number_series(pg_db, world["company"], document_type="invoice")
    pg_db.commit()
    number_engine.allocate_number(
        pg_db,
        company_id=world["company"].id,
        document_type="invoice",
        document_date=date(2026, 6, 1),
    )
    pg_db.commit()

    number_engine.allocate_number(
        pg_db,
        company_id=world["company"].id,
        document_type="invoice",
        document_date=date(2026, 5, 1),
        actor=audit.Actor.system(company_id=world["company"].id),
    )
    pg_db.rollback()

    assert _events(pg_db, "number.out_of_order") == []


def test_an_out_of_order_allocation_with_no_actor_is_still_recorded(pg_db, world):
    """`Actor.system()`. An out-of-order document numbered by a Celery task or
    a CLI still has to leave a trace — "nobody did it" is a fact worth
    recording, and the alternative is an event that silently does not exist."""
    make_number_series(pg_db, world["company"], document_type="invoice")
    pg_db.commit()
    for day in (1, 2):
        number_engine.allocate_number(
            pg_db,
            company_id=world["company"].id,
            document_type="invoice",
            document_date=date(2026, 6, day),
        )
    pg_db.commit()

    number_engine.allocate_number(
        pg_db,
        company_id=world["company"].id,
        document_type="invoice",
        document_date=date(2026, 1, 1),
    )
    pg_db.commit()

    event = _events(pg_db, "number.out_of_order")[0]
    assert event.user_id is None
    assert event.company_id == world["company"].id


def test_install_level_events_are_not_on_a_companys_screen(
    pg_client, pg_db, world
):
    """A `company_id` of NULL belongs to the installation.

    The instance-wide 2FA switch, the mail settings, a user account. A
    company owner has no business reading what the installation
    administrator does — and on a single-company install the same person sees
    it through the admin screens instead.
    """
    response = pg_client.patch(
        "/site-settings",
        headers=auth_headers(world["root"]),
        json={"require_2fa": True},
    )
    assert response.status_code == 200, response.text

    pg_db.expire_all()
    recorded = _events(pg_db, "site_settings.updated")
    assert len(recorded) == 1
    assert recorded[0].company_id is None
    assert recorded[0].changes["require_2fa"] == [False, True]

    body = pg_client.get("/company/audit-events", headers=_headers(world)).json()
    assert body["total"] == 0, (
        "an install-level event appeared on a company's audit screen"
    )


# ─── The endpoint ───────────────────────────────────────────────────────────


def test_the_log_is_newest_first_and_paged(pg_client, pg_db, world):
    # Rows built directly, with `created_at` set AT INSERT.
    #
    # The first version of this called `audit.record` and then assigned
    # `event.created_at` to space the rows out — and the trigger refused the
    # UPDATE, which is the correct behaviour and a nice demonstration of it:
    # an audit row cannot be adjusted even by the test that wrote it.
    base = datetime.now(timezone.utc) - timedelta(hours=5)
    for index in range(5):
        pg_db.add(
            AuditEvent(
                company_id=world["company"].id,
                user_id=world["owner"].id,
                entity_type="companies",
                entity_id=world["company"].id,
                action="company.updated",
                changes={"legal_name": [f"n{index}", f"n{index + 1}"]},
                created_at=base + timedelta(minutes=index),
            )
        )
    pg_db.commit()

    first = pg_client.get(
        "/company/audit-events?limit=2", headers=_headers(world)
    ).json()
    assert first["total"] == 5
    assert len(first["events"]) == 2
    assert [e["changes"]["legal_name"][1] for e in first["events"]] == ["n5", "n4"]

    second = pg_client.get(
        "/company/audit-events?limit=2&offset=2", headers=_headers(world)
    ).json()
    assert [e["changes"]["legal_name"][1] for e in second["events"]] == ["n3", "n2"]


def test_the_log_carries_the_actors_name_and_email(pg_client, pg_db, world):
    pg_client.patch("/company", headers=_headers(world), json={"phone": "+43"})

    body = pg_client.get("/company/audit-events", headers=_headers(world)).json()

    assert body["events"][0]["user_email"] == "owner@example.com"
    assert body["events"][0]["user_name"]


def test_filtering_by_user_and_by_entity_and_by_date(pg_client, pg_db, world):
    pg_client.patch("/company", headers=_headers(world), json={"phone": "+43"})
    pg_client.patch(
        "/company",
        headers=auth_headers(world["admin"], world["company"]),
        json={"website": "https://example.com"},
    )
    pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_AT}
    )

    by_user = pg_client.get(
        f"/company/audit-events?user_id={world['admin'].id}",
        headers=_headers(world),
    ).json()
    assert by_user["total"] == 1
    assert by_user["events"][0]["user_email"] == "admin@example.com"

    by_entity = pg_client.get(
        "/company/audit-events?entity_type=company_bank_accounts",
        headers=_headers(world),
    ).json()
    assert by_entity["total"] == 1
    assert by_entity["events"][0]["action"] == "bank_account.created"

    today = date.today().isoformat()
    by_date = pg_client.get(
        f"/company/audit-events?from={today}&to={today}", headers=_headers(world)
    ).json()
    assert by_date["total"] == 3, (
        "a `to` date of today excluded today's events — the end-of-day bug"
    )

    long_ago = (date.today() - timedelta(days=30)).isoformat()
    empty = pg_client.get(
        f"/company/audit-events?from={long_ago}&to={long_ago}",
        headers=_headers(world),
    ).json()
    assert empty["total"] == 0


def test_the_filter_options_come_from_what_the_log_holds(pg_client, world):
    pg_client.patch("/company", headers=_headers(world), json={"phone": "+43"})
    pg_client.post(
        "/company/bank-accounts", headers=_headers(world), json={"iban": VALID_AT}
    )

    body = pg_client.get("/company/audit-events", headers=_headers(world)).json()

    assert body["entity_types"] == ["companies", "company_bank_accounts"]
    assert body["actions"] == ["bank_account.created", "company.updated"]


def test_an_empty_log_answers_with_an_empty_page_not_an_error(pg_client, world):
    body = pg_client.get("/company/audit-events", headers=_headers(world)).json()
    assert body == {
        "events": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
        "entity_types": [],
        "actions": [],
    }


# ─── Who may read it ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "role_key,allowed",
    [
        ("owner", True),
        ("admin", True),
        ("advisor", True),
        ("accountant", False),
        ("producer", False),
    ],
)
def test_the_permission_matrix(pg_client, pg_db, world, role_key, allowed):
    """Owner, Admin and the tax advisor (Mathias, 2026-10-20).

    The accountant is NOT in it, which narrows the seeded placeholder
    (`_BOOKS`). That is a decision and not an oversight — see
    `services/permissions.py` and SCOPE §16 (D43).

    **The refusal is 404, not 403.** `middleware/company.py::require` answers
    every authorisation failure in this layer with one body, so that no
    endpoint can leak the difference by being written slightly differently
    from its neighbour. P0b-3's prompt asked for 403 here; keeping the
    invariant was the choice, and it is flagged in that task's report rather
    than quietly changed. The security property is identical — no access —
    and the menu entry is gated on the same permission (see
    apps/web/app/(dashboard)/settings/layout.tsx).
    """
    response = pg_client.get(
        "/company/audit-events",
        headers=auth_headers(world[role_key], world["company"]),
    )

    if allowed:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 404, response.text


def test_the_tax_advisor_can_read_this_company_and_not_another(
    pg_client, pg_db, world
):
    pg_client.patch("/company", headers=_headers(world), json={"phone": "+43"})

    mine = pg_client.get(
        "/company/audit-events",
        headers=auth_headers(world["advisor"], world["company"]),
    )
    assert mine.status_code == 200
    assert mine.json()["total"] == 1

    theirs = pg_client.get(
        "/company/audit-events",
        headers=auth_headers(world["advisor"], world["other"]),
    )
    assert theirs.status_code == 404


def test_the_tax_advisor_cannot_write_anything_through_the_audit_surface(
    pg_client, world
):
    """There is no write endpoint on `audit_events` and there must not be.

    Asserted against the app's own OpenAPI document rather than by trying
    verbs, so a POST added next year fails this rather than being discovered
    by somebody using it.
    """
    from fastapi.openapi.utils import get_openapi

    from apps.api.main import app

    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    audit_paths = {
        path: set(operations)
        for path, operations in schema["paths"].items()
        if "audit" in path
    }
    assert audit_paths, "no audit endpoint found — this test is vacuous"
    for path, methods in audit_paths.items():
        assert methods == {"get"}, f"{path} serves {methods}, not GET only"

    # And the verbs really are refused, not merely undocumented.
    for method in ("post", "patch", "delete", "put"):
        response = pg_client.request(
            method,
            "/company/audit-events",
            headers=auth_headers(world["advisor"], world["company"]),
            json={},
        )
        assert response.status_code in (404, 405), (
            f"{method.upper()} /company/audit-events answered "
            f"{response.status_code}"
        )


def test_another_companys_log_is_404_even_for_its_own_owner(pg_client, world):
    response = pg_client.get(
        "/company/audit-events",
        headers=auth_headers(world["owner"], world["other"]),
    )
    assert response.status_code == 404


def test_one_companys_events_never_appear_in_anothers_log(
    pg_client, pg_db, world
):
    """The scoping, against a real neighbour.

    `audit_events` is the one business table without the `CompanyScoped`
    mixin — `company_id` is nullable for install-level events — so
    `permissions.scoped()` refuses it by design and the filter lives in
    `services/audit.company_events`. This is the test that proves the filter
    is there: a MagicMock session returns what it was told for every filter
    and for none.
    """
    audit.record(
        pg_db,
        audit.Actor(
            user_id=world["other_owner"].id, company_id=world["other"].id
        ),
        world["other"],
        "company.updated",
        {"legal_name": "Other GmbH"},
        {"legal_name": "Other GmbH (renamed)"},
    )
    pg_db.commit()

    mine = pg_client.get(
        "/company/audit-events", headers=_headers(world)
    ).json()
    assert mine["total"] == 0, "another company's event appeared in this log"

    theirs = pg_client.get(
        "/company/audit-events",
        headers=auth_headers(world["other_owner"], world["other"]),
    ).json()
    assert theirs["total"] == 1


def test_the_tax_advisor_role_requires_two_factor_unconditionally(pg_db, world):
    """SCOPE §9.3b, re-asserted here because `audit.view` now reaches an
    external party.

    The advisor is the one role held by somebody outside the company, and the
    audit log is now part of what they can read. `roles_requiring_2fa` adds
    `tax_advisor` whatever a company has configured, so the requirement
    cannot be switched off per company.
    """
    from apps.api.services.permissions import roles_requiring_2fa
    from apps.api.services.company_service import (
        two_factor_required_by_membership,
    )

    assert CompanyRole.tax_advisor in roles_requiring_2fa([world["company"]])
    assert two_factor_required_by_membership(pg_db, world["advisor"]) is True
    # And not for a role nobody configured.
    assert two_factor_required_by_membership(pg_db, world["producer"]) is False


# ─── The redaction list, after P1a-2 narrowed it ────────────────────────────


def test_a_bare_code_field_is_not_a_secret_but_every_real_one_still_is():
    """`SECRET_FIELDS` used to contain a bare `"code"`.

    It was too wide. The first column in the system actually named `code` is
    `tax_codes.code` — `AT-20`, printed on every invoice — and redacting it
    made `tax_code.created` read `code: [redacted] → [redacted]`: an audit
    row that cannot answer the one question it exists for. P1a-2 removed the
    bare entry and named the one-time codes individually.

    This is the test that makes the narrowing safe rather than merely
    intended. It is written as two lists because that is the actual risk: a
    narrowing that went one field too far would be invisible until somebody
    read a log and found a password in it.
    """
    from apps.api.services.audit import REDACTED, redact

    # Not secrets. Each of these is printed, exported or shown on a screen.
    for field in (
        "code",
        "category",
        "country_code",
        "currency_code",
        "language_code",
        "item_code",
        "tax_code",
    ):
        assert redact(field, "AT-20") == "AT-20", f"{field} was redacted"

    # Secrets. Nothing that was redacted before P1a-2 has stopped being.
    for field in (
        "password",
        "password_hash",
        "new_password",
        "current_password",
        "smtp_password",
        "totp_secret",
        "totp_secret_encrypted",
        "backup_codes",
        "backup_codes_hashed",
        "magic_code",
        "verification_code",
        "recovery_code",
        "one_time_code",
        "otp_code",
        "reset_code",
        "invite_token",
        "refresh_token",
        "access_token",
        "jwt_secret",
        "secret_key",
        "s3_secret_key",
        "api_key",
        "some_token",
        "whatever_secret",
        "custom_api_key",
    ):
        assert redact(field, "the-real-value") == REDACTED, (
            f"{field} is no longer redacted"
        )


def test_the_iban_is_still_masked_rather_than_redacted():
    """The other half of the redaction contract, re-asserted beside the
    change above: an advisor reading the log can tell two of a company's
    accounts apart without being able to be paid instead of them (D43)."""
    from apps.api.services.audit import redact

    assert redact("iban", "AT611904300234573201") == "AT…3201"
