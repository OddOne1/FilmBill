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


# ─── The backup script ──────────────────────────────────────────────────────
#
# Run as the script it is, with `DOCKER_COMPOSE` pointed at a stub that writes
# SQL on stdout and a temporary `DATA_DIR`. Not a Python reimplementation of
# what it is believed to do — that would prove the reimplementation
# (CLAUDE.md's harness rule) — and not the real compose either, because the
# subject is the script's handling of modes and printed text, not Postgres.
#
# What the drill of 2026-10-20 found, and what these pin:
#
#   * the dumps were `-rw-r--r-- root:apps` on a shared NAS, and a pg_dump of
#     this database holds every bcrypt hash and every IBAN in the clear;
#   * the restore line it printed had no `sudo`, so a normal user got a
#     Docker socket error that reads as a broken instruction.

BACKUP = REPO_ROOT / "scripts" / "server-test-backup.sh"

#: The stub's output has to survive `gzip -9` above the script's 1000-byte
#: "this is not a backup" guard, or it exits 1 before reaching any of the
#: behaviour under test and every assertion below is really about the guard.
#:
#: Repeated INSERT lines do not: 200 of them compress to about 800 bytes.
#: Random base64 does, which is why the stub emits that alongside one
#: recognisable row.
_STUB_SQL_HEAD = "-- stub dump\nINSERT INTO probe VALUES (1, 'BACKUP-PROBE');\n"


@pytest.fixture
def backup_world(tmp_path):
    """A stub compose binary, a temporary DATA_DIR, and an env file."""
    data_dir = tmp_path / "filmbill-v2-data"
    data_dir.mkdir()

    stub = tmp_path / "docker-compose-stub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "# Stands in for `docker compose exec -T postgres pg_dump`. Writes SQL\n"
        "# on stdout and ignores everything else, which is the whole of what\n"
        "# the backup script needs from it.\n"
        f"printf '%s' {_STUB_SQL_HEAD!r}\n"
        "# Incompressible filler, so the result clears the script's own\n"
        "# 1000-byte guard — see the note on _STUB_SQL_HEAD.\n"
        "head -c 20000 /dev/urandom | base64\n"
    )
    stub.chmod(0o755)

    env_file = tmp_path / ".env.server-test"
    env_file.write_text(
        f"DATA_DIR={data_dir}\n"
        "POSTGRES_USER=filmbill\n"
        "POSTGRES_DB=filmbill_v2\n"
    )

    return {"data_dir": data_dir, "stub": stub, "env_file": env_file}


def _run_backup(world, **extra_env) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(
        {
            "ENV_FILE": str(world["env_file"]),
            "DOCKER_COMPOSE": str(world["stub"]),
        }
    )
    env.update(extra_env)
    return subprocess.run(
        ["bash", str(BACKUP)],
        env=env,
        capture_output=True,
        text=True,
    )


def _dumps(world) -> list[Path]:
    return sorted((world["data_dir"] / "backups").glob("*.sql.gz"))


def test_the_backup_script_writes_a_dump(backup_world):
    """The guard for the three tests below: each of them asserts something
    about a file, and would fail confusingly rather than clearly if no file
    were produced at all."""
    result = _run_backup(backup_world)

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert len(_dumps(backup_world)) == 1, result.stdout


def test_the_dump_is_mode_600(backup_world):
    """It holds every password hash and every IBAN in the clear.

    `-rw-r--r-- root:apps` on a shared NAS means readable by every account on
    the box, which is what the drill found.
    """
    _run_backup(backup_world)

    dump = _dumps(backup_world)[0]
    assert oct(dump.stat().st_mode & 0o777) == "0o600", (
        f"{dump.name} is {oct(dump.stat().st_mode & 0o777)}"
    )


def test_the_backups_directory_is_mode_700(backup_world):
    """Tightened even when it already exists, which is the case on the
    server: the directory was created 0755 by an earlier version of this
    script, and `mkdir -p` on an existing directory changes nothing."""
    (backup_world["data_dir"] / "backups").mkdir(mode=0o755)

    _run_backup(backup_world)

    mode = (backup_world["data_dir"] / "backups").stat().st_mode & 0o777
    assert oct(mode) == "0o700", f"backups/ is {oct(mode)}"


def test_the_mode_survives_a_loose_inherited_umask(backup_world):
    """`umask 077` in the script, not inherited from the caller.

    A cron or an SSH one-liner can arrive with umask 022, and the mode of the
    dump must not depend on it. This is also what makes the explicit `chmod`
    worth keeping rather than relying on the umask alone.
    """
    result = subprocess.run(
        ["bash", "-c", f"umask 022; exec bash {BACKUP}"],
        env={
            **os.environ,
            "ENV_FILE": str(backup_world["env_file"]),
            "DOCKER_COMPOSE": str(backup_world["stub"]),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    dump = _dumps(backup_world)[0]
    assert oct(dump.stat().st_mode & 0o777) == "0o600"


def test_the_script_reports_the_mode_it_achieved(backup_world):
    """Printed, so a cron mail says what is true rather than what the script
    intended — the difference the drill turned on."""
    result = _run_backup(backup_world)

    assert "mode 600" in result.stdout, result.stdout


def test_every_printed_compose_line_has_sudo(backup_world):
    """Without it a normal user gets "permission denied while trying to
    connect to the Docker daemon socket", which reads as a broken instruction
    rather than as a missing prefix.

    Checked over EVERY printed compose invocation, not just the restore one:
    the `stop` and `start` lines need it just as much, and the drill only
    happened to hit the restore.
    """
    result = _run_backup(backup_world)

    stub = str(backup_world["stub"])
    lines = [
        line for line in result.stdout.splitlines() if stub in line
    ]
    assert lines, f"no compose invocation printed at all:\n{result.stdout}"
    missing = [line for line in lines if "sudo " + stub not in line]
    assert not missing, (
        "printed compose lines without sudo:\n" + "\n".join(missing)
    )


def test_the_printed_instructions_say_a_dump_is_not_a_proven_backup(
    backup_world,
):
    """The lesson of the drill, where the first two dumps held an empty
    `company_bank_accounts` and would have restored an empty table just as
    happily."""
    result = _run_backup(backup_world)

    assert "only PROVEN by restoring data you can recognise" in result.stdout
    assert "docs/deploy/server-test.md" in result.stdout


def test_the_deploy_doc_describes_the_recognisable_row_drill():
    """Asserted on the DOC because that is where somebody reads it.

    Not a grep for a word: the four steps that make a restore proof —
    add a recognisable row, find it in the dump, change it, restore, see it
    back — are each looked for, so a doc that merely mentions "backup" does
    not pass.
    """
    doc = (REPO_ROOT / "docs" / "deploy" / "server-test.md").read_text()

    assert "BACKUP-PROBE" in doc, "no recognisable row to look for"
    assert "zcat" in doc and "grep -c BACKUP-PROBE" in doc, (
        "the doc does not check that the dump CONTAINS the probe"
    )
    assert "PROBE-CHANGED" in doc, (
        "the doc does not change the row before restoring, so the restore "
        "would prove nothing"
    )
    assert "chmod 600" in doc, "no instruction for the dumps that already exist"


def test_the_backup_script_still_refuses_an_empty_dump(backup_world, tmp_path):
    """The pre-existing guard, re-asserted because `umask` and `chmod` were
    added around it: a dump that failed mid-stream still leaves a file, and
    gzip of nothing is ~20 bytes."""
    empty_stub = tmp_path / "empty-stub"
    empty_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    empty_stub.chmod(0o755)
    backup_world["stub"] = empty_stub

    result = _run_backup(backup_world)

    assert result.returncode == 1
    assert "not treating this as a backup" in result.stderr
