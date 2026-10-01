"""Dev mail actually reaches Mailpit.

For three phases it did not, and nothing said so. `smtp_security_from` maps a
false `SMTP_USE_TLS` to **implicit_tls** — deliberately, because that is what
the boolean has always DONE and P0b-0 would not silently switch off anybody's
production encryption — so the dev stack opened an SMTP_SSL socket to Mailpit's
plaintext port 1025 and every send died with `[SSL: WRONG_VERSION_NUMBER]`. The
invite links, 2FA codes and backup-address codes that three acceptance
walkthroughs depend on were never delivered.

P0b-0 fixed the MECHANISM and its acceptance never exercised the dev compose
against Mailpit, which is why the gap survived it. These tests close that: they
read the compose file the stack actually runs from and push its values through
the real config path, rather than asserting anything about source text
(CLAUDE.md rule 11).

Three layers, in order of how much they need:

  1. the compose file resolves to mode `none` — pure YAML plus the real
     settings loader, runs anywhere, including CI with no Docker;
  2. every mail-sending service resolves to the SAME config as `api` — the
     anchor is not enough on its own, because a future service can be given
     its own `environment:` block;
  3. a message really arrives in Mailpit, read back out of Mailpit's own API —
     needs the dev stack, and SKIPS loudly with a named reason when it is not
     there rather than passing for the wrong reason.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from apps.api.services.email_config import (
    SMTP_SECURITY_NONE,
    MailConfig,
    resolve_mail_config,
)

COMPOSE_FILE = Path(__file__).resolve().parents[3] / "docker-compose.dev.yml"

#: Every service that sends mail, or could. `api` sends synchronously on some
#: paths; `email_worker` consumes the mail queues; `worker` and `beat` are here
#: because a scheduled job or a document task that notifies somebody would use
#: the same config, and a service that silently had a different one is exactly
#: the bug this file exists for.
MAIL_SERVICES = ("api", "worker", "email_worker", "beat")

#: Not a mail setting, and `Settings` refuses to construct without it. The
#: compose file leaves it to `env_file`, so the raw YAML has no value for it —
#: supplied here as a placeholder so the loader can run at all. Nothing below
#: asserts anything about it.
_REQUIRED_NON_MAIL = {"JWT_SECRET": "not-used-by-anything-in-this-file"}


def _compose() -> dict:
    """The dev compose file as data.

    `yaml.safe_load` resolves `&api_env` / `*api_env` itself, so a service that
    inherits the anchor comes back with the SAME dict — which is the point of
    the anchor and the reason test 2 has to compare resolved configs rather
    than trust it.
    """
    assert COMPOSE_FILE.exists(), f"missing {COMPOSE_FILE}"
    return yaml.safe_load(COMPOSE_FILE.read_text())


def _service_env(name: str) -> dict:
    env = _compose()["services"][name].get("environment")
    assert env, f"service {name!r} declares no environment block"
    # A compose `environment:` can be a mapping or a list of "K=V" strings.
    # Both are legal and the list form would otherwise silently read as empty.
    if isinstance(env, list):
        pairs = [item.split("=", 1) for item in env]
        env = {k: v for k, v in pairs if len((k, v)) == 2}
    return env


def _settings_from(env: dict):
    """A real `Settings` built from ONLY these variables.

    `_env_file=None` is load-bearing: without it `Settings` reads the
    developer's own `.env`, which on this machine sets `SMTP_HOST`,
    `SMTP_PORT=587` and `SMTP_USE_TLS=true` — so every test here would be
    asserting against a blend of the compose file and whatever the operator
    happens to have configured, and would pass or fail for reasons that have
    nothing to do with the compose file.

    `os.environ` is replaced wholesale for the same reason, and everything is
    stringified because YAML gives `SMTP_PORT: 1025` as an int while the real
    path receives strings from the process environment — so the `"false"` →
    `False` coercion is exercised rather than assumed.
    """
    from apps.api.config import Settings

    environ = {str(k): str(v) for k, v in env.items()}
    environ.update(_REQUIRED_NON_MAIL)

    with patch.dict(os.environ, environ, clear=True):
        return Settings(_env_file=None)


def _mail_config_from(env: dict) -> MailConfig:
    """Push those variables through the real settings → MailConfig path.

    The module reads its settings as a global, so that is where they have to be
    swapped in. `resolve_mail_config(None)` is the no-database-row branch,
    which is the state a fresh dev stack is in.
    """
    with patch("apps.api.services.email_config.settings", _settings_from(env)):
        return resolve_mail_config(None)


# ─── 1. the compose file resolves to a plaintext connection ──────────────────


def test_the_dev_stack_talks_plaintext_to_mailpit():
    """The regression this file exists for.

    Mode, host and port together: a mode of `none` pointed at a relay on 587
    would be a different and worse bug, so the assertion is about the whole
    connection rather than the one field that changed.
    """
    config = _mail_config_from(_service_env("api"))

    assert config.smtp_security == SMTP_SECURITY_NONE, (
        f"dev mail resolves to {config.smtp_security!r}, not 'none'. Mailpit's "
        f"port 1025 is plaintext; anything else fails with "
        f"[SSL: WRONG_VERSION_NUMBER] and no mail is delivered at all. "
        f"docker-compose.dev.yml must set SMTP_SECURITY: \"none\" — "
        f"SMTP_USE_TLS: \"false\" does NOT mean that, it means implicit TLS."
    )
    assert config.smtp_host == "mailpit"
    assert config.smtp_port == 1025
    assert config.provider == "smtp"


def test_the_false_boolean_alone_would_not_have_been_enough():
    """The control, and the reason the explicit mode is not redundant.

    Without this, the test above would read as "the compose file happens to be
    fine" rather than "this one line is what makes it fine". Drop
    SMTP_SECURITY and the same compose values resolve to implicit_tls — the
    broken state, reached from the file's own `SMTP_USE_TLS: "false"`.
    """
    env = dict(_service_env("api"))
    assert env.pop("SMTP_SECURITY", None) == "none", (
        "SMTP_SECURITY is not in the api environment block at all — see the "
        "test above for what that costs."
    )

    assert _mail_config_from(env).smtp_security == "implicit_tls"


def test_the_developers_own_env_file_cannot_change_the_answer():
    """`.env` sets SMTP_USE_TLS=true, 587 and a real host on this machine, and
    compose's `environment:` block overrides `env_file:`. Asserted because the
    helper's `_env_file=None` is the thing that makes every test here mean the
    compose file rather than the operator's laptop."""
    with patch.dict(os.environ, {"SMTP_SECURITY": "starttls", "SMTP_PORT": "587"}):
        config = _mail_config_from(_service_env("api"))

    assert config.smtp_security == SMTP_SECURITY_NONE
    assert config.smtp_port == 1025


# ─── 2. every mail-sending service agrees with api ───────────────────────────


@pytest.mark.parametrize("service", [s for s in MAIL_SERVICES if s != "api"])
def test_every_mail_sending_service_resolves_like_api(service):
    """The anchor is a promise, not a guarantee.

    `*api_env` is one line and a future service can be given its own
    `environment:` block — for one extra variable, say — at which point it
    quietly stops inheriting the mail settings and its sends fail while the
    API's succeed. Comparing the RESOLVED config catches that; comparing the
    YAML to itself would not.
    """
    mine = _mail_config_from(_service_env(service))
    api = _mail_config_from(_service_env("api"))

    for field in ("provider", "smtp_host", "smtp_port", "smtp_security", "smtp_use_tls"):
        assert getattr(mine, field) == getattr(api, field), (
            f"{service} resolves {field}={getattr(mine, field)!r} while api "
            f"resolves {getattr(api, field)!r}. If {service} was given its own "
            f"environment block, it needs the mail settings too — or it should "
            f"go back to inheriting *api_env."
        )


def test_the_service_list_still_matches_the_compose_file():
    """A new service that consumes a mail queue and is not in MAIL_SERVICES is
    invisible to the test above. This is the cheapest signal for that: any
    service whose command mentions an email queue has to be listed."""
    services = _compose()["services"]
    consumers = {
        name
        for name, spec in services.items()
        if "email" in str(spec.get("command", "")).lower()
    }

    missing = sorted(consumers - set(MAIL_SERVICES))
    assert not missing, (
        f"services consuming an email queue but not covered here: {missing}. "
        f"Add them to MAIL_SERVICES."
    )


# ─── 3. a message really arrives ─────────────────────────────────────────────
#
# Needs the dev stack. Skips loudly rather than passing when it is absent,
# because a green line for a delivery that never happened is exactly the shape
# this whole file is about.

#: Mailpit's HTTP API. `mailpit:8025` from inside the compose network,
#: `localhost:8125` from the host — both are tried so the same test runs in
#: either place.
MAILPIT_BASES = (
    os.getenv("MAILPIT_API_URL", "").rstrip("/"),
    "http://mailpit:8025",
    "http://localhost:8125",
)


def _mailpit_base() -> str:
    import urllib.error
    import urllib.request

    for base in MAILPIT_BASES:
        if not base:
            continue
        try:
            with urllib.request.urlopen(f"{base}/api/v1/info", timeout=2):
                return base
        except (urllib.error.URLError, OSError):
            continue
    pytest.skip(
        "Mailpit is not reachable at any of "
        f"{[b for b in MAILPIT_BASES if b]} — this test needs the dev stack "
        "(`docker compose -f docker-compose.dev.yml up -d mailpit`). SKIPPED, "
        "not passed: the point of it is that a message really arrives."
    )


def _mailpit_messages(base: str) -> list:
    import json
    import urllib.request

    with urllib.request.urlopen(f"{base}/api/v1/messages?limit=200", timeout=5) as r:
        return json.load(r).get("messages", [])


def _recipients(message: dict) -> list:
    return [entry.get("Address", "") for entry in (message.get("To") or [])]


@pytest.fixture
def mailpit():
    return _mailpit_base()


@pytest.mark.parametrize(
    "path",
    ["direct", "celery"],
    ids=["through EmailService", "through the Celery task"],
)
def test_a_message_really_arrives_in_mailpit(mailpit, path):
    """End to end, with the real transport and no mocked SMTP.

    Both paths, because they are different processes in production: `api` sends
    some mail inline and `email_worker` consumes the queues, and the whole
    reason test 2 exists is that the two can be configured differently. Running
    only one of them would leave the other unproven.

    The Celery variant runs the task EAGERLY (`.apply()`) rather than queueing
    it. Queueing would need the worker container up and would turn this into a
    test of Redis round-trip latency; running it in-process still exercises the
    task's own config resolution, template rendering and transport, which is
    the part that was broken. It uses `send_invite_email` specifically because
    that is the mail three acceptance walkthroughs wait for.

    **The send is configured from the compose file, not from this process.**
    Both paths construct their own `EmailService`, which reads
    `email_config.settings` — so without the patch below the test would send
    using whatever mail settings the test runner happens to have, and would
    fail with "Name or service not known" in any harness that does not define
    `SMTP_HOST`. That failure says nothing about the bug. Patching the compose
    values in is what makes this assert the thing the compose file promises:
    these settings, this transport, a message that really arrives.
    """
    import uuid

    marker = uuid.uuid4().hex[:12]
    recipient = f"arrival-{marker}@example.test"
    compose_settings = _settings_from(_service_env("api"))

    if path == "direct":
        from apps.api.services.email_service import EmailService

        subject = f"Dev mail probe {marker}"
        with patch("apps.api.services.email_config.settings", compose_settings):
            sent = EmailService().send_email(
                recipient, subject, f"<p>{marker}</p>", marker
            )
        assert sent, (
            "send_email returned False with the compose file's own settings. "
            "If the log says [SSL: WRONG_VERSION_NUMBER], SMTP_SECURITY is not "
            "resolving to 'none' — which is the exact bug this file exists for."
        )
    else:
        from apps.api.tasks.email_tasks import send_invite_email

        # The subject is the task's own, not ours — which is the point: this
        # asserts the real invite mail, built by the real template.
        subject = "You've been invited to join FilmBill"
        with patch("apps.api.services.email_config.settings", compose_settings):
            result = send_invite_email.apply(
                args=[
                    recipient,
                    "Probe Admin",
                    "FilmBill",
                    f"https://example.test/{marker}",
                ]
            )
        assert result.successful(), f"the invite task failed: {result.traceback}"

    delivered = [
        m for m in _mailpit_messages(mailpit) if recipient in _recipients(m)
    ]
    assert len(delivered) == 1, (
        f"expected exactly one message for {recipient}, found {len(delivered)}"
    )
    assert delivered[0]["Subject"] == subject
