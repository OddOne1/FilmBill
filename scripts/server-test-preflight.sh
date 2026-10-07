#!/usr/bin/env bash
#
# Read-only checks before the LAN test instance's FIRST start.
#
# It changes nothing: no container is created, started, stopped or removed, no
# image is built or pulled, no file outside this repo is written. It exists so
# that `up` either works or has already been refused with a reason — on a host
# that also runs FreeFrame, Immich, Cloudflare Tunnel and several others,
# "just try it and see" is not an acceptable first move.
#
# Exit 0 = safe to start. Exit 1 = refused, with the reason. Exit 2 = warnings
# only (printed, but it continues).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env.server-test}"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.server-test.yml"
PROJECT="filmbill-v2"

# Overridable so the tests can feed fixtures instead of touching the host.
# Defaults are the real commands; a test that replaced both would be testing
# itself, so each test overrides exactly one (CLAUDE.md rule 17b).
IP_CMD="${PREFLIGHT_IP_CMD:-ip -4 addr}"
DOCKER_CMD="${PREFLIGHT_DOCKER_CMD:-docker}"

fail=0
warn=0
say()  { printf '%s\n' "$*"; }
ok()   { printf '  ok      %s\n' "$*"; }
bad()  { printf '  REFUSE  %s\n' "$*"; fail=1; }
warny(){ printf '  warn    %s\n' "$*"; warn=1; }

say "FilmBill LAN test instance — pre-flight (read-only)"
say ""

# ── 1. environment file ────────────────────────────────────────────────────
if [[ ! -f "${ENV_FILE}" ]]; then
  bad "no ${ENV_FILE} — run scripts/gen-server-test-env.sh first"
  exit 1
fi
ok "found ${ENV_FILE}"

# Read it without executing it: a `$(...)` in a value would otherwise run.
get() { sed -n "s/^$1=//p" "${ENV_FILE}" | head -1; }

LAN_IP="$(get LAN_IP)"
WEB_PORT="$(get WEB_PORT)"
API_PORT="$(get API_PORT)"
MINIO_PORT="$(get MINIO_PORT)"
MAILPIT_UI_PORT="$(get MAILPIT_UI_PORT)"
DATA_DIR="$(get DATA_DIR)"

# ── 2. DATA_DIR ────────────────────────────────────────────────────────────
if [[ -z "${DATA_DIR}" ]]; then
  bad "DATA_DIR is unset — without it the bind mounts resolve to relative
          paths inside the repo and the data would not survive a re-clone"
else
  ok "DATA_DIR=${DATA_DIR}"
  if [[ ! -d "${DATA_DIR}" ]]; then
    warny "${DATA_DIR} does not exist yet — create it with the ownership in
          docs/deploy/server-test.md before the first start"
  fi
fi

# ── 3. LAN_IP must be an address this host actually has ────────────────────
say ""
say "Local IPv4 addresses:"
addr_lines="$(${IP_CMD} 2>/dev/null)"
printf '%s\n' "${addr_lines}" | sed -n 's/^.*inet \([0-9.]*\)\/[0-9]* \(.*\)$/    \1    \2/p'

if [[ -z "${LAN_IP}" ]]; then
  bad "LAN_IP is unset"
elif ! printf '%s\n' "${addr_lines}" | grep -qE "inet ${LAN_IP}/"; then
  # The reason this is a refusal and not a warning: Docker fails at `up` with
  # "cannot assign requested address", after some containers have already
  # started, leaving the stack half-up.
  bad "LAN_IP=${LAN_IP} is not assigned to any local interface.
          Publishing a port on an address the host does not have fails at
          \`up\` with \"cannot assign requested address\". Pick one of the
          addresses listed above."
else
  ok "LAN_IP=${LAN_IP} is present on this host"
  # `ip -4 addr` prints `dynamic` on a DHCP lease. A lease that changes takes
  # the stack down at the next `up`, so it is worth saying out loud even
  # though it is legal today.
  if printf '%s\n' "${addr_lines}" | grep -E "inet ${LAN_IP}/" | grep -q "dynamic"; then
    warny "LAN_IP=${LAN_IP} is on a DYNAMIC (DHCP) address. If the lease
          changes, \`up\` will fail with \"cannot assign requested address\"
          and emailed links will point at an address nobody answers on.
          Prefer a static address."
  else
    ok "LAN_IP is not marked dynamic"
  fi
fi

# ── 4. ports ───────────────────────────────────────────────────────────────
say ""
# A port check that cannot check must REFUSE, not pass. Found by running this
# in a container with neither tool installed: every port was reported free and
# the script said PASSED, which is the worst possible answer — the whole point
# of the step is to not collide with FreeFrame or anything else on that host.
have_socket_tool=1
if command -v ss >/dev/null 2>&1; then
  listening() { ss -ltnH 2>/dev/null | awk '{print $4}'; }
elif command -v netstat >/dev/null 2>&1; then
  listening() { netstat -an 2>/dev/null | awk '/^tcp.*LISTEN/ {print $4}'; }
else
  have_socket_tool=0
  listening() { :; }
fi
listen_snapshot="$(listening)"

check_port() {
  local name="$1" port="$2"
  if [[ -z "${port}" ]]; then
    bad "${name} is unset"
    return
  fi
  if (( ! have_socket_tool )); then
    bad "cannot check ${name}=${port}: neither \`ss\` nor \`netstat\` is
          available, so this script has no way to see what is already
          listening. Install iproute2 (or net-tools) and run it again rather
          than starting blind on a host that also runs FreeFrame."
    return
  fi
  # Matches ":3100" at the end of an address, so 13100 or 31000 do not count.
  if printf '%s\n' "${listen_snapshot}" | grep -qE "[:.]${port}\$"; then
    bad "${name}=${port} is already in use on this host. Pick another, or
          find the owner with:  sudo lsof -nP -iTCP:${port} -sTCP:LISTEN"
  else
    ok "${name}=${port} is free"
  fi
}
check_port WEB_PORT "${WEB_PORT}"
check_port API_PORT "${API_PORT}"
check_port MINIO_PORT "${MINIO_PORT}"
check_port MAILPIT_UI_PORT "${MAILPIT_UI_PORT}"

# ── 5. is this project already up? ─────────────────────────────────────────
say ""
existing="$(${DOCKER_CMD} ps -q --filter "label=com.docker.compose.project=${PROJECT}" 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${existing}" != "0" ]]; then
  bad "${existing} container(s) of project '${PROJECT}' are already running.
          This script checks a FIRST start. To deploy a change use the update
          steps in docs/deploy/server-test.md (pull, build, up) — not this."
else
  ok "no '${PROJECT}' containers running"
fi

# ── 6. what else on this host answers to the name "filmbill"? ─────────────
#
# LISTED, never touched. If a v1 exists, this is where it shows up, and
# knowing is the whole point: rule 18 is about not colliding with it.
say ""
say "Anything else on this host named *filmbill* (listed only, never touched):"
other_c="$(${DOCKER_CMD} ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null \
            | grep -i filmbill | grep -v 'filmbill-v2' || true)"
other_i="$(${DOCKER_CMD} images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
            | grep -i filmbill | grep -v 'filmbill-v2' || true)"
if [[ -z "${other_c}" && -z "${other_i}" ]]; then
  say "    (none)"
else
  [[ -n "${other_c}" ]] && printf '    container  %s\n' "${other_c}"
  [[ -n "${other_i}" ]] && printf '    image      %s\n' "${other_i}"
  warny "Something named filmbill already exists here. Nothing above was
          touched. Check it is not a v1 this stack could disturb before
          continuing (CLAUDE.md rule 18)."
fi

# ── verdict ────────────────────────────────────────────────────────────────
say ""
if (( fail )); then
  say "REFUSED — fix the items marked REFUSE above, then run this again."
  exit 1
fi
if (( warn )); then
  say "PASSED WITH WARNINGS — read them, then continue with the first start."
  exit 2
fi
say "PASSED — safe to run the first build and start."
exit 0
