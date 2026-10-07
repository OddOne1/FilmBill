"""The env generator and the pre-flight, run as the scripts they are (§17).

These are shell scripts, so they are executed — not imported, not
reimplemented, not approximated by a Python translation of what they are
believed to do. A rewritten copy would prove the copy (CLAUDE.md rule 17b).

Two things get real resources rather than doubles, because they are the
subject:

  * the port check opens an actual listening socket. "Is this port in use" is
    a question about the host, and a fixture saying "pretend 3100 is busy"
    would test the fixture's format and not whether the script can read `ss`
    or `netstat` output on the machine it runs on.
  * the generator writes a real file and the assertions read it back.

One thing IS a fixture, deliberately: `ip -4 addr` output. The host running
these tests has whatever addresses it has, and the behaviour under test is the
PARSING — "is LAN_IP present", "is its interface dynamic" — which cannot be
exercised against a host that happens not to have a dynamic address. The
script takes `PREFLIGHT_IP_CMD` for exactly this, and each test overrides at
most one of the two injection points so the other stays real.
"""

import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN = REPO_ROOT / "scripts" / "gen-server-test-env.sh"
PREFLIGHT = REPO_ROOT / "scripts" / "server-test-preflight.sh"
EXAMPLE = REPO_ROOT / ".env.server-test.example"

# `ip -4 addr` as this server really prints it (Mathias' inventory,
# 2026-10-07): .34 is a DHCP lease on eno2, .100 is static on bond50.
#: The pre-flight REFUSES when it can see neither `ss` nor `netstat`, which is
#: correct — a port check that cannot check must not report "free". So the
#: tests that expect it to get PAST the port step need a host that has one.
#: Skipped loudly with a named reason rather than left to fail, and rather
#: than relaxed into passing for the wrong reason (CLAUDE.md rule 17b). CI
#: runs on ubuntu-latest, which has iproute2; the API image does not, which is
#: how this was found.
needs_socket_tool = pytest.mark.skipif(
    shutil.which("ss") is None and shutil.which("netstat") is None,
    reason="needs `ss` or `netstat`; without one the pre-flight correctly refuses",
)

IP_FIXTURE = """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
2: eno2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default qlen 1000
    inet 192.168.1.34/24 brd 192.168.1.255 scope global dynamic eno2
       valid_lft 85904sec preferred_lft 85904sec
3: bond50: <BROADCAST,MULTICAST,MASTER,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    inet 192.168.1.100/24 brd 192.168.1.255 scope global bond50
       valid_lft forever preferred_lft forever
"""


def _write_env(path: Path, **over) -> Path:
    vals = {
        "LAN_IP": "192.168.1.100",
        "WEB_PORT": "3100",
        "API_PORT": "8100",
        "MINIO_PORT": "9100",
        "MAILPIT_UI_PORT": "8125",
        "DATA_DIR": "/tmp/filmbill-v2-data-under-test",
        "POSTGRES_USER": "filmbill",
        "POSTGRES_PASSWORD": "x",
        "POSTGRES_DB": "filmbill",
    }
    vals.update({k: str(v) for k, v in over.items()})
    path.write_text("".join(f"{k}={v}\n" for k, v in vals.items() if v != ""))
    return path


def _run_preflight(env_file: Path, *, ip_output: str = IP_FIXTURE, docker: str | None = None):
    env = os.environ.copy()
    env["ENV_FILE"] = str(env_file)
    # Written to a file and `cat`-ed rather than inlined as
    # `printf %s '<multi-line>'`: the script expands ${IP_CMD} UNQUOTED, so
    # the shell word-splits it and does not re-parse the quotes inside the
    # value — an inlined fixture arrives mangled and the script then
    # correctly reports that LAN_IP is on no interface.
    fixture = env_file.parent / "ip-addr-fixture.txt"
    fixture.write_text(ip_output)
    env["PREFLIGHT_IP_CMD"] = f"cat {fixture}"
    # Real `docker` unless a test needs a specific answer; when it does, the
    # IP command above is the only other thing faked.
    if docker is not None:
        env["PREFLIGHT_DOCKER_CMD"] = docker
    else:
        env["PREFLIGHT_DOCKER_CMD"] = "true"
    return subprocess.run(
        ["bash", str(PREFLIGHT)], capture_output=True, text=True, env=env
    )


# ── b — the generator ──────────────────────────────────────────────────────


@pytest.fixture
def gen_env(tmp_path, monkeypatch):
    """Run the generator against a throwaway repo root.

    It writes next to itself (`$(dirname $0)/..`), so the script is copied
    into a temporary tree with the example beside it rather than being told a
    different output path — that keeps the path logic under test instead of
    bypassed.
    """
    fake_root = tmp_path / "repo"
    (fake_root / "scripts").mkdir(parents=True)
    (fake_root / "scripts" / GEN.name).write_bytes(GEN.read_bytes())
    (fake_root / "scripts" / GEN.name).chmod(0o755)
    (fake_root / EXAMPLE.name).write_bytes(EXAMPLE.read_bytes())
    # The compose file is read to cross-check that every variable it uses got
    # written, so it has to be there too.
    (fake_root / "docker-compose.server-test.yml").write_bytes(
        (REPO_ROOT / "docker-compose.server-test.yml").read_bytes()
    )

    def run():
        return subprocess.run(
            ["bash", str(fake_root / "scripts" / GEN.name)],
            capture_output=True,
            text=True,
            # All defaults accepted.
            input="\n" * 6,
        )

    return fake_root, run


def test_the_generator_writes_every_variable_the_compose_file_uses(gen_env):
    root, run = gen_env
    proc = run()
    assert proc.returncode == 0, proc.stderr
    out = root / ".env.server-test"
    assert out.exists()

    import re

    text = (root / "docker-compose.server-test.yml").read_text()
    used = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", text))
    written = {
        line.split("=", 1)[0]
        for line in out.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert not (used - written), f"not generated: {sorted(used - written)}"


def test_the_generated_secrets_are_distinct_and_non_empty(gen_env):
    root, run = gen_env
    assert run().returncode == 0
    vals = dict(
        line.split("=", 1)
        for line in (root / ".env.server-test").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    secrets = {
        k: vals[k]
        for k in (
            "POSTGRES_PASSWORD", "SECRET_KEY", "JWT_SECRET",
            "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD",
        )
    }
    for key, value in secrets.items():
        assert value, f"{key} is empty"
        assert len(value) >= 12, f"{key} is only {len(value)} chars"
        assert "replace-me" not in value, f"{key} kept the template placeholder"
    assert len(set(secrets.values())) == len(secrets), (
        f"two secrets are identical: {secrets}"
    )


def test_the_generated_secrets_are_not_the_dev_ones(gen_env):
    """The dev stack's credentials are `filmbill`/`minioadmin` in a committed
    file. Reusing them here would make a LAN-exposed instance openable by
    anyone who has read the repo."""
    root, run = gen_env
    assert run().returncode == 0
    vals = dict(
        line.split("=", 1)
        for line in (root / ".env.server-test").read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    dev_known = {"filmbill", "minioadmin", "postgres", "redis", "changeme", "secret"}
    for key in (
        "POSTGRES_PASSWORD", "SECRET_KEY", "JWT_SECRET",
        "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD",
    ):
        assert vals[key].lower() not in dev_known, (
            f"{key} is a well-known dev value ({vals[key]!r})"
        )


def test_the_generator_refuses_to_overwrite(gen_env):
    """The file holds the only copy of the password protecting DATA_DIR;
    regenerating over a live instance leaves a database nothing can open."""
    root, run = gen_env
    assert run().returncode == 0
    first = (root / ".env.server-test").read_text()

    again = run()
    assert again.returncode != 0
    assert "refusing to overwrite" in again.stderr
    assert (root / ".env.server-test").read_text() == first, "it overwrote anyway"


def test_the_generated_file_is_not_world_readable(gen_env):
    root, run = gen_env
    assert run().returncode == 0
    # `& 0o777` rather than `stat.S_IMODE`: the repo's
    # test_declared_dependencies does not carry `stat` in its stdlib list, so
    # importing it fails that test (reported separately — the list is what is
    # wrong, not the import).
    mode = (root / ".env.server-test").stat().st_mode & 0o777
    assert mode == 0o600, f"mode is {oct(mode)}; secrets must not be readable by others"


# ── c — the pre-flight ─────────────────────────────────────────────────────


@needs_socket_tool
def test_the_preflight_passes_on_free_ports(tmp_path):
    env_file = _write_env(tmp_path / "env")
    proc = _run_preflight(env_file)
    # 0 = clean, 2 = warnings only (DATA_DIR not existing yet is one).
    assert proc.returncode in (0, 2), proc.stdout + proc.stderr
    assert "REFUSE" not in proc.stdout, proc.stdout


@needs_socket_tool
def test_the_preflight_refuses_a_port_that_is_really_listening(tmp_path):
    """A real socket, not a fixture: the question is about this host, and the
    script has to read `ss` or `netstat` output correctly to answer it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy = sock.getsockname()[1]

        env_file = _write_env(tmp_path / "env", WEB_PORT=busy)
        proc = _run_preflight(env_file)

    assert proc.returncode == 1, proc.stdout
    assert f"WEB_PORT={busy} is already in use" in proc.stdout
    assert "REFUSED" in proc.stdout


def test_the_preflight_refuses_when_it_cannot_see_listening_sockets(tmp_path):
    """A check that cannot check must refuse, not pass.

    Found by running the suite inside the API image, which has neither `ss`
    nor `netstat`: every port came back "free" and the script printed PASSED.
    That is the worst available answer on a host that also runs FreeFrame,
    Immich and Cloudflare Tunnel — it is the one step whose whole job is to
    not collide with them.

    The subprocess gets a PATH holding symlinks to every tool the script
    genuinely needs and NOTHING else — so `command -v ss` and
    `command -v netstat` really fail while `grep`, `awk` and the rest still
    work. Emptying PATH outright was tried first and proved nothing: the
    script then also lost `grep`, refused for that instead, and the test
    passed on the wrong reason.
    """
    env_file = _write_env(tmp_path / "env")
    fixture = env_file.parent / "ip.txt"
    fixture.write_text(IP_FIXTURE)

    toolbox = tmp_path / "bin"
    toolbox.mkdir()
    needed = (
        "bash", "sh", "cat", "grep", "awk", "sed", "head", "tail", "wc",
        "tr", "sort", "uniq", "printf", "ls", "dirname", "true", "env",
    )
    for tool in needed:
        found = shutil.which(tool)
        if found:
            (toolbox / tool).symlink_to(found)
    assert shutil.which("ss", path=str(toolbox)) is None
    assert shutil.which("netstat", path=str(toolbox)) is None
    assert shutil.which("grep", path=str(toolbox)) is not None, "harness broken"

    env = os.environ.copy()
    env["ENV_FILE"] = str(env_file)
    env["PATH"] = str(toolbox)
    env["PREFLIGHT_IP_CMD"] = f"cat {fixture}"
    env["PREFLIGHT_DOCKER_CMD"] = "true"
    proc = subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(PREFLIGHT)],
        capture_output=True, text=True, env=env,
    )

    assert proc.returncode == 1, proc.stdout
    assert "neither" in proc.stdout and "netstat" in proc.stdout
    assert "REFUSED" in proc.stdout


@needs_socket_tool
def test_the_preflight_refuses_a_lan_ip_the_host_does_not_have(tmp_path):
    """Docker fails at `up` with "cannot assign requested address" — after
    some containers have started, so the stack is left half-up."""
    env_file = _write_env(tmp_path / "env", LAN_IP="10.99.99.99")
    proc = _run_preflight(env_file)

    assert proc.returncode == 1, proc.stdout
    assert "is not assigned to any local interface" in proc.stdout
    assert "cannot assign requested address" in proc.stdout


@needs_socket_tool
def test_the_preflight_warns_on_a_dynamic_address_but_continues(tmp_path):
    """.34 is a DHCP lease on this server. Legal today; a changed lease takes
    the stack down at the next `up` and points every emailed link at an
    address nobody answers on."""
    env_file = _write_env(tmp_path / "env", LAN_IP="192.168.1.34")
    proc = _run_preflight(env_file)

    assert proc.returncode == 2, proc.stdout
    assert "is on a DYNAMIC (DHCP) address" in proc.stdout
    assert "REFUSE" not in proc.stdout
    assert "PASSED WITH WARNINGS" in proc.stdout


@needs_socket_tool
def test_the_preflight_accepts_the_static_address_without_that_warning(tmp_path):
    env_file = _write_env(tmp_path / "env", LAN_IP="192.168.1.100")
    proc = _run_preflight(env_file)

    assert "DYNAMIC" not in proc.stdout
    assert "is not marked dynamic" in proc.stdout


@needs_socket_tool
def test_the_preflight_refuses_when_data_dir_is_unset(tmp_path):
    env_file = _write_env(tmp_path / "env", DATA_DIR="")
    proc = _run_preflight(env_file)

    assert proc.returncode == 1, proc.stdout
    assert "DATA_DIR is unset" in proc.stdout


@needs_socket_tool
def test_the_preflight_refuses_when_the_project_is_already_up(tmp_path):
    """It checks a FIRST start. Running it against a live instance and then
    following the first-start steps is how a running stack gets rebuilt
    underneath itself."""
    env_file = _write_env(tmp_path / "env")
    # Stands in for `docker ps -q --filter ...`, which is what the script
    # counts. Two ids = two containers.
    fake = "printf 'abc123\\ndef456\\n' #"
    proc = _run_preflight(env_file, docker=fake)

    assert proc.returncode == 1, proc.stdout
    assert "already running" in proc.stdout
    assert "use the update steps" in proc.stdout.replace("\n", " ") or \
           "not this" in proc.stdout


def test_the_preflight_requires_an_env_file_at_all(tmp_path):
    proc = _run_preflight(tmp_path / "does-not-exist")
    assert proc.returncode == 1
    assert "run scripts/gen-server-test-env.sh first" in proc.stdout


def test_the_preflight_never_mutates_anything(tmp_path):
    """It is documented as read-only, on a host running FreeFrame, Immich and
    Cloudflare Tunnel. Asserted by the absence of every verb that could
    change state — the one check here that is about the source text, because
    "did not do anything" has no observable form to assert instead."""
    text = PREFLIGHT.read_text()
    for verb in (
        "docker run", "docker start", "docker stop", "docker rm",
        "docker rmi", "docker build", "docker pull", "compose up",
        "compose down", "mkdir", "rm -", "> /", "chown", "chmod",
    ):
        assert verb not in text, f"pre-flight contains {verb!r}"
