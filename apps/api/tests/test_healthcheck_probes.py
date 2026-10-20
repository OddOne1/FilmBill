"""Every healthcheck probe addresses 127.0.0.1, and the web probe asks `/login`.

`filmbill_web` reported `(unhealthy)` for 213 consecutive probes while serving
every request correctly. The probe was
`wget -q --spider http://localhost:3000`: inside that image `/etc/hosts` maps
`localhost` to both `127.0.0.1` and `::1`, BusyBox `wget` tries the IPv6
address first and gives up there, and Next listens on IPv4 only. So the probe
had never once succeeded — and because nothing depended on web being healthy,
nothing said so.

**What these tests are and are not.** They read the compose files and the
Dockerfiles as *data* — a healthcheck is configuration, and a probe URL is a
value in it, not prose about one (CLAUDE.md rule 11). What they cannot prove is
that the probe succeeds against the running server; only bringing the stack up
and reading `docker inspect` does that, which is why this prompt's acceptance
does exactly that and this file carries the regression guard.

Plain YAML rather than `docker compose config`, so this runs in CI's api-tests
job, which has no Docker and no `.env`. Nothing asserted below is produced by
variable substitution, so the raw file and the resolved one agree here.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]

COMPOSE_FILES = (
    "docker-compose.dev.yml",
    "docker-compose.prod.yml",
    "docker-compose.server-test.yml",
)

DOCKERFILES = (
    "apps/api/Dockerfile",
    "apps/api/Dockerfile.prod",
    "apps/web/Dockerfile.prod",
)

#: Every way a probe can name the loopback interface by a name that may
#: resolve to `::1`. `0.0.0.0` is in here too: it is a bind address, never a
#: destination, and a probe that uses it works only by accident.
FORBIDDEN_HOSTS = ("localhost", "ip6-localhost", "0.0.0.0", "[::1]")

#: The services whose probe must ask Next for `/login`. `/` serves a redirect,
#: so a probe on `/` passes only if the client follows it — a dependency on
#: client behaviour that buys nothing when a 200 is one path away.
WEB_SERVICES = ("web",)


def _probe_commands(compose: dict) -> list[tuple[str, str]]:
    """(service, probe as one string) for every service that has a healthcheck."""
    found = []
    for name, service in (compose.get("services") or {}).items():
        health = (service or {}).get("healthcheck")
        if not health:
            continue
        test = health.get("test")
        if test is None:
            continue
        found.append((name, test if isinstance(test, str) else " ".join(test)))
    return found


@pytest.fixture(scope="module", params=COMPOSE_FILES)
def compose(request) -> tuple[str, dict]:
    path = REPO_ROOT / request.param
    return request.param, yaml.safe_load(path.read_text())


def test_the_file_has_healthchecks_at_all(compose):
    """Guards the two tests below from passing on an empty list.

    A selector that silently matched nothing would report "every probe is
    correct" for ever, which is the same false green one level up.
    """
    name, data = compose
    probes = _probe_commands(data)
    assert len(probes) >= 4, (
        f"{name} resolved to only {len(probes)} healthcheck(s) — the parser "
        f"below is reading the wrong shape: {probes}"
    )


def test_no_probe_addresses_localhost(compose):
    name, data = compose
    offenders = {
        service: probe
        for service, probe in _probe_commands(data)
        if any(host in probe for host in FORBIDDEN_HOSTS)
    }
    assert not offenders, (
        f"{name}: a healthcheck must address 127.0.0.1, because `localhost` "
        f"can resolve to ::1 while the server binds IPv4 only; these do not: "
        f"{offenders}"
    )


def test_the_web_probe_asks_for_login(compose):
    name, data = compose
    probes = dict(_probe_commands(data))
    for service in WEB_SERVICES:
        if service not in probes:
            continue
        assert "/login" in probes[service], (
            f"{name}: the {service} probe must ask for a path that answers "
            f"200 directly; `/` is a redirect and only passes if the client "
            f"follows it. Got: {probes[service]!r}"
        )


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_no_dockerfile_healthcheck_addresses_localhost(dockerfile):
    """An image whose own HEALTHCHECK is wrong is a trap for the next service.

    `apps/api/Dockerfile.prod` carried `http://localhost:8000/health` and
    nothing was broken by it — every compose file overrides the instruction.
    That is the whole problem: the next service to run this image without an
    override inherits a probe that cannot succeed.
    """
    text = (REPO_ROOT / dockerfile).read_text()
    # Line continuations joined, so a HEALTHCHECK split over two lines is read
    # as the one instruction it is.
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    instructions = [
        line.strip()
        for line in joined.splitlines()
        if line.strip().upper().startswith("HEALTHCHECK")
    ]
    offenders = [
        line
        for line in instructions
        if any(host in line for host in FORBIDDEN_HOSTS)
    ]
    assert not offenders, (
        f"{dockerfile}: HEALTHCHECK must address 127.0.0.1, not a name that "
        f"can resolve to ::1: {offenders}"
    )


def test_at_least_one_dockerfile_declares_a_healthcheck():
    """The guard above is vacuous for a file with no HEALTHCHECK at all, so
    this pins that the set as a whole still contains one to check."""
    found = [
        name
        for name in DOCKERFILES
        if re.search(r"^\s*HEALTHCHECK", (REPO_ROOT / name).read_text(), re.M | re.I)
    ]
    assert found, (
        "no Dockerfile in this repository declares a HEALTHCHECK any more — "
        "test_no_dockerfile_healthcheck_addresses_localhost now proves nothing"
    )
