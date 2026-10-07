"""The LAN test instance's compose file, as Docker actually resolves it.

Why the real loader and not `yaml.safe_load` (CLAUDE.md rule 17b)
-----------------------------------------------------------------
Every property worth asserting here is a property of the EFFECTIVE
configuration, and most of them do not exist in the YAML text:

  * `"${LAN_IP}:${WEB_PORT}:3000"` is one string in the file. Whether it binds
    to a specific address or to every interface is decided by compose's own
    port parser, after interpolation. A test that split that string itself
    would be asserting against its own parser — and would keep passing if the
    real one disagreed.
  * `*labels` is a YAML anchor. Reading the file as YAML does expand it, but
    `environment:` on a service can *override* the anchor it merged, and only
    the resolved config shows which won.
  * an undefined variable interpolates to an EMPTY STRING rather than failing,
    which turns `${LAN_IP}:3100:3000` into `:3100:3000` — published on all
    interfaces. That is invisible in the text and obvious in the resolution.

So these tests shell out to `docker compose config --format json`, and SKIP
loudly with a named reason where Docker is absent. A YAML-only version would
run in more places and prove less, which is the trade rule 17b exists to
refuse: FilmBill's own dev-mail bug survived a phase because its acceptance
never resolved the compose file it shipped.

What is being defended
----------------------
Rule 18: a FilmBill v1 may exist and may be auto-updated from a registry.
Nothing in this stack may collide with it, and nothing may be reachable from
the internet. Those are one-line mistakes — a forgotten label, a port bound to
`0.0.0.0`, an `external:` network that joins someone else's — so each gets its
own assertion with its own failure message.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.server-test.yml"
EXAMPLE_ENV = REPO_ROOT / ".env.server-test.example"

PROJECT_NAME = "filmbill-v2"
IMAGE_PREFIX = "filmbill-v2-"
WATCHTOWER_LABEL = "com.centurylinklabs.watchtower.enable"

#: Services that must NOT be destroyable by `docker compose down -v`. Each
#: keeps data the test instance is expected to survive a redeploy with.
STATEFUL = ("postgres", "redis", "minio", "mailpit")

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="needs the docker CLI to resolve the compose file through the real loader",
)


def _example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in EXAMPLE_ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


@pytest.fixture(scope="module")
def resolved() -> dict:
    """The compose file as Docker resolves it, using the EXAMPLE env.

    The example is the right input: it is the file under test's companion, and
    resolving with it proves the two agree. A hand-written dict here would let
    the example drift from the compose file undetected, which is the specific
    thing test `every_variable_is_in_the_example` exists to stop.
    """
    env = os.environ.copy()
    env.update(_example_values())
    proc = subprocess.run(
        [
            "docker", "compose",
            "-f", str(COMPOSE_FILE),
            "--env-file", str(EXAMPLE_ENV),
            "config", "--format", "json",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    if proc.returncode != 0:
        pytest.fail(
            "`docker compose config` could not resolve "
            f"{COMPOSE_FILE.name}:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


# ── isolation from a possible v1 (rule 18) ─────────────────────────────────


def test_the_project_name_is_fixed(resolved):
    """Not left to default to the directory name — which on the server is
    whatever the clone was called, and could be v1's."""
    assert resolved["name"] == PROJECT_NAME


def test_every_built_image_is_tagged_for_v2(resolved):
    """A tag no registry serves, so there is nothing for an auto-updater to
    pull over it, and it can never be v1's name."""
    built = {
        name: svc["image"]
        for name, svc in resolved["services"].items()
        if "build" in svc
    }
    assert built, "no service builds from source — did the build: blocks go?"
    wrong = {n: i for n, i in built.items() if not i.startswith(IMAGE_PREFIX)}
    assert not wrong, (
        f"built images must be tagged {IMAGE_PREFIX}<service>:test so no "
        f"registry can serve them (rule 18); these are not: {wrong}"
    )


def test_no_built_image_looks_like_a_registry_reference(resolved):
    """`org/filmbill:latest` would be pullable — and v1 is auto-updated from
    Docker Hub, so an image with that shape could replace production."""
    for name, svc in resolved["services"].items():
        if "build" not in svc:
            continue
        image = svc["image"]
        assert "/" not in image, (
            f"{name} builds to {image!r}, which has a registry/namespace "
            "shape; a locally built test image must not (rule 18)"
        )


def test_every_service_disables_watchtower(resolved):
    missing = [
        name
        for name, svc in resolved["services"].items()
        if str(svc.get("labels", {}).get(WATCHTOWER_LABEL, "")).lower() != "false"
    ]
    assert not missing, (
        f"these services are missing {WATCHTOWER_LABEL}=false: {missing}. "
        "It is a courtesy rather than a guarantee (DockMon ignores it), but a "
        "missing one is a service an updater is invited to touch."
    )


# ── nothing reachable from outside the LAN ─────────────────────────────────


def test_no_service_joins_an_external_network_or_volume(resolved):
    for name, net in (resolved.get("networks") or {}).items():
        assert not net.get("external"), (
            f"network {name!r} is external — this stack must not join "
            "FreeFrame's or anyone else's network"
        )
    for name, vol in (resolved.get("volumes") or {}).items():
        assert not vol.get("external"), (
            f"volume {name!r} is external — this stack must not mount a "
            "volume it does not own"
        )


def test_no_service_is_attached_to_any_network_but_this_project_s_own(resolved):
    """The service-level half of the check above, and the one that matters.

    Worth both: `docker compose config` PRUNES a declared network that no
    service joins, so a stray `external: true` block on its own disappears
    from the resolved output and the networks-level assertion never sees it.
    Only a network a service actually attaches to survives — which is also
    the only version that could do any harm.
    """
    for name, svc in resolved["services"].items():
        joined = sorted((svc.get("networks") or {"default": None}).keys())
        assert joined == ["default"], (
            f"{name} is attached to {joined}; every service belongs on this "
            "project's own network and nothing else (rule 18)"
        )


def test_there_are_no_traefik_or_cloudflare_labels(resolved):
    """A Traefik label is all it takes for the host's existing Traefik to
    start routing this instance, and Cloudflare Tunnel sits in front of that.
    The test instance must stay LAN-only."""
    for name, svc in resolved["services"].items():
        for key, value in (svc.get("labels") or {}).items():
            low = f"{key}={value}".lower()
            assert "traefik" not in low, f"{name} has a Traefik label: {key}"
            assert "cloudflare" not in low, f"{name} has a Cloudflare label: {key}"


def test_every_published_port_binds_to_the_lan_address(resolved):
    """The assertion that needs the real loader most.

    An empty `host_ip` means every interface — including whatever the host is
    exposed on — and that is what an unset `LAN_IP` silently produces.
    """
    expected = _example_values()["LAN_IP"]
    assert expected and expected != "0.0.0.0"

    published = {
        name: svc["ports"]
        for name, svc in resolved["services"].items()
        if svc.get("ports")
    }
    assert published, "no ports published at all — the web UI would be unreachable"

    for name, ports in published.items():
        for port in ports:
            host_ip = port.get("host_ip", "")
            assert host_ip == expected, (
                f"{name} publishes {port.get('published')} on "
                f"{host_ip or '0.0.0.0 (every interface)'}; it must bind to "
                f"${{LAN_IP}} ({expected}) only"
            )


def test_only_the_browser_facing_services_publish_a_port(resolved):
    """Postgres, Redis, Gotenberg and the workers have no business on the LAN.
    Postgres especially: it is the one service here worth attacking."""
    publishing = {
        name for name, svc in resolved["services"].items() if svc.get("ports")
    }
    assert publishing == {"web", "api", "minio", "mailpit"}, (
        f"unexpected set of services publishing ports: {sorted(publishing)}"
    )


# ── data survives a redeploy ───────────────────────────────────────────────


def test_stateful_services_use_bind_mounts_not_named_volumes(resolved):
    """`down -v` removes named volumes. Nothing in the docs or scripts uses
    `-v`, but the file should not be one typo away from a wiped instance."""
    data_dir = _example_values()["DATA_DIR"]
    for name in STATEFUL:
        mounts = resolved["services"][name].get("volumes") or []
        assert mounts, f"{name} persists nothing — its data dies with the container"
        for mount in mounts:
            assert mount["type"] == "bind", (
                f"{name} has a {mount['type']} mount at "
                f"{mount.get('target')}; stateful paths must be bind mounts "
                "under DATA_DIR so `down -v` cannot remove them"
            )
            assert mount["source"].startswith(data_dir), (
                f"{name} binds {mount['source']}, which is outside "
                f"DATA_DIR ({data_dir}) — a redeploy or re-clone would not "
                "find the data where the backup script looks for it"
            )


def test_no_top_level_named_volumes_exist(resolved):
    assert not (resolved.get("volumes") or {}), (
        "this stack declares named volumes; everything durable belongs under "
        "DATA_DIR as a bind mount"
    )


# ── mail actually works (the dev-mail lesson) ──────────────────────────────


def test_every_mail_sending_service_resolves_smtp_security_none(resolved):
    """Mailpit's 1025 is plaintext. `SMTP_USE_TLS=false` resolves to
    implicit_tls in `smtp_security_from`, so without the explicit mode every
    send dies with `[SSL: WRONG_VERSION_NUMBER]` and no mail arrives — which
    is exactly how FilmBill's dev stack delivered nothing for three phases.
    """
    for name in ("api", "worker", "email_worker", "beat"):
        env = resolved["services"][name]["environment"]
        assert env.get("SMTP_SECURITY") == "none", (
            f"{name} resolves SMTP_SECURITY={env.get('SMTP_SECURITY')!r}; "
            "Mailpit needs 'none' or nothing is delivered"
        )
        assert env.get("SMTP_HOST") == "mailpit"
        assert str(env.get("SMTP_PORT")) == "1025"


def test_the_api_family_agrees_about_everything_that_matters(resolved):
    """The anchor is not proof: a service can override what it merged, and
    only the resolved config shows it."""
    keys = (
        "DATABASE_URL", "REDIS_URL", "FRONTEND_URL",
        "S3_ENDPOINT", "S3_PUBLIC_ENDPOINT", "SMTP_SECURITY",
    )
    api = resolved["services"]["api"]["environment"]
    for name in ("worker", "email_worker", "beat"):
        env = resolved["services"][name]["environment"]
        for key in keys:
            assert env.get(key) == api.get(key), (
                f"{name}.{key} ({env.get(key)!r}) differs from "
                f"api.{key} ({api.get(key)!r})"
            )


# ── the browser-facing origins are the LAN, never localhost ───────────────


def test_links_and_presigned_urls_use_the_lan_address(resolved):
    """`localhost` in either of these is a link that works only on the server
    itself: an emailed magic code would point at the recipient's own machine,
    and a presigned URL at a MinIO they do not run (rule 16)."""
    vals = _example_values()
    env = resolved["services"]["api"]["environment"]

    assert env["FRONTEND_URL"] == f"http://{vals['LAN_IP']}:{vals['WEB_PORT']}"
    assert env["S3_PUBLIC_ENDPOINT"] == f"http://{vals['LAN_IP']}:{vals['MINIO_PORT']}"
    for key in ("FRONTEND_URL", "S3_PUBLIC_ENDPOINT"):
        assert "localhost" not in env[key]
        assert "127.0.0.1" not in env[key]


def test_the_web_bundle_is_built_against_the_lan_api_url(resolved):
    """`NEXT_PUBLIC_API_URL` is baked in at build time, so it is a build arg
    and not an environment variable — setting it at runtime would do nothing
    and would look like it had worked (rule 15)."""
    vals = _example_values()
    web = resolved["services"]["web"]
    args = web["build"]["args"]
    assert args["NEXT_PUBLIC_API_URL"] == f"http://{vals['LAN_IP']}:{vals['API_PORT']}"


def test_next_server_side_code_gets_an_internal_api_url(resolved):
    """middleware.ts and site-settings-server run INSIDE the web container,
    where the LAN address is a round trip out and back, and `localhost` is
    nothing. Without this the setup redirect silently never fires (rule 15)."""
    env = resolved["services"]["web"]["environment"]
    assert env.get("API_INTERNAL_URL") == "http://api:8000"


# ── healthchecks address 127.0.0.1, never localhost ───────────────────────


def test_no_healthcheck_probes_localhost(resolved):
    """Inside these images `localhost` resolves to ::1 as well as 127.0.0.1,
    BusyBox wget tries IPv6 first, and the servers bind IPv4 only — so the
    probe never succeeds and the container reads `(unhealthy)` while working.
    FilmBill's dev web service reached FailingStreak 213 that way."""
    for name, svc in resolved["services"].items():
        test = (svc.get("healthcheck") or {}).get("test")
        if not test:
            continue
        joined = " ".join(test) if isinstance(test, list) else str(test)
        assert "localhost" not in joined, (
            f"{name}'s healthcheck probes localhost: {joined!r}"
        )


def test_the_web_probe_asks_for_login(resolved):
    """`/login` answers 200 directly; `/` is a 307 and depends on the probe
    following redirects."""
    test = resolved["services"]["web"]["healthcheck"]["test"]
    joined = " ".join(test) if isinstance(test, list) else str(test)
    assert "http://127.0.0.1:3000/login" in joined


# ── the example env and the compose file agree ─────────────────────────────


def test_every_variable_the_compose_file_uses_is_in_the_example():
    """An undefined variable interpolates to an empty string instead of
    failing, so a missing one is a silent misconfiguration — `${LAN_IP}` gone
    publishes on every interface."""
    text = COMPOSE_FILE.read_text()
    used = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", text))
    documented = set(_example_values())
    missing = sorted(used - documented)
    assert not missing, (
        f"used by {COMPOSE_FILE.name} but absent from {EXAMPLE_ENV.name}: "
        f"{missing}"
    )


def test_the_example_holds_no_real_secret():
    """It is committed. Every secret in it must be an obvious placeholder."""
    vals = _example_values()
    for key in (
        "POSTGRES_PASSWORD", "SECRET_KEY", "JWT_SECRET",
        "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD",
    ):
        assert vals[key] == "replace-me-generated", (
            f"{key} in {EXAMPLE_ENV.name} is {vals[key]!r} — the committed "
            "template must never carry a usable value"
        )
