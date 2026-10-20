#!/usr/bin/env bash
#
# pg_dump the LAN test instance into ${DATA_DIR}/backups/, keep the last N,
# print the restore command. Nothing else.
#
# It does not stop the stack, touch any other container, or delete anything
# outside its own backups directory.
#
# **The dumps are 0600 and the directory 0700.** A pg_dump of this database
# contains every bcrypt password hash and every bank account number in the
# clear. The drill of 2026-10-20 found them `-rw-r--r-- root:apps` on the
# TrueNAS host — world-readable to every account on a shared NAS. `umask 077`
# below covers the file at creation (so there is no window where it is
# readable) and the explicit `chmod` covers the case where a umask was
# inherited from somewhere unexpected.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env.server-test}"
COMPOSE="${DOCKER_COMPOSE:-/usr/libexec/docker/cli-plugins/docker-compose}"
KEEP="${KEEP:-14}"

[[ -f "${ENV_FILE}" ]] || { echo "no ${ENV_FILE}" >&2; exit 1; }
get() { sed -n "s/^$1=//p" "${ENV_FILE}" | head -1; }

DATA_DIR="$(get DATA_DIR)"
PGUSER="$(get POSTGRES_USER)"
PGDB="$(get POSTGRES_DB)"
[[ -n "${DATA_DIR}" ]] || { echo "DATA_DIR is unset in ${ENV_FILE}" >&2; exit 1; }

# Before anything is created. 077 means the file is born 0600 and the
# directory 0700 — there is no instant at which a half-written dump of every
# password hash in the database is readable by anybody else.
umask 077

DEST="${DATA_DIR}/backups"
mkdir -p "${DEST}"
# Tightened whether or not `mkdir` created it: on the server the directory
# already existed, 0755, from the first run of an earlier version of this
# script. `chmod` on an existing directory is what fixes that.
chmod 700 "${DEST}"
STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
FILE="${DEST}/filmbill-v2-${PGDB}-${STAMP}.sql.gz"

echo "dumping ${PGDB} -> ${FILE}"
# Through compose exec so it uses the running container's own client and
# socket; no password handling, no host psql needed. `-T` because there is no
# terminal in a cron or an SSH one-liner.
"${COMPOSE}" -f "${REPO_ROOT}/docker-compose.server-test.yml" \
  --env-file "${ENV_FILE}" -p filmbill-v2 \
  exec -T postgres pg_dump -U "${PGUSER}" -d "${PGDB}" --clean --if-exists \
  | gzip -9 > "${FILE}"

# Belt and braces over `umask`. The redirection above already created the
# file under 077, and this is what makes the mode true even if a caller
# exported something odd or a future edit moves the umask.
chmod 600 "${FILE}"

# A dump that failed mid-stream still leaves a file, and gzip of nothing is
# ~20 bytes — so an empty-ish result is reported rather than counted as a
# backup and rotated over a good one.
size="$(wc -c < "${FILE}" | tr -d ' ')"
if (( size < 1000 )); then
  echo "FAILED: ${FILE} is only ${size} bytes — not treating this as a backup" >&2
  echo "the stack may be down; check: ${COMPOSE} -f docker-compose.server-test.yml --env-file ${ENV_FILE} -p filmbill-v2 ps" >&2
  exit 1
fi
echo "wrote ${FILE} (${size} bytes)"
# Printed so a cron mail says what the mode actually is, rather than what the
# script intended. `%a` is GNU stat; BSD/macOS uses `%Lp`, and neither is
# worth failing the backup over if the tool is missing.
mode="$(stat -c '%a' "${FILE}" 2>/dev/null || stat -f '%Lp' "${FILE}" 2>/dev/null || echo '?')"
echo "mode ${mode} (expected 600 — it contains password hashes and IBANs)"

# Rotation, oldest first, and only ever files this script's own naming
# produces — a glob that matched more widely is how a backup script deletes
# something else.
mapfile -t all < <(ls -1t "${DEST}"/filmbill-v2-*.sql.gz 2>/dev/null || true)
if (( ${#all[@]} > KEEP )); then
  for old in "${all[@]:${KEEP}}"; do
    echo "pruning ${old}"
    rm -f -- "${old}"
  done
fi
echo "keeping the newest ${KEEP}; ${#all[@]} present before pruning"

# `sudo` on every printed compose line. Without it a normal user gets
# "permission denied while trying to connect to the Docker daemon socket",
# which reads as a broken instruction rather than as a missing prefix — and
# the drill of 2026-10-20 hit exactly that.
cat <<RESTORE

To restore this dump (it DROPs and recreates the objects it contains):

  gunzip -c ${FILE} \\
    | sudo ${COMPOSE} -f ${REPO_ROOT}/docker-compose.server-test.yml \\
        --env-file ${ENV_FILE} -p filmbill-v2 \\
        exec -T postgres psql -U ${PGUSER} -d ${PGDB}

Stop the api, worker, email_worker and beat services first so nothing writes
during the restore:

  sudo ${COMPOSE} -f ${REPO_ROOT}/docker-compose.server-test.yml --env-file ${ENV_FILE} -p filmbill-v2 stop api worker email_worker beat
  (restore, then)
  sudo ${COMPOSE} -f ${REPO_ROOT}/docker-compose.server-test.yml --env-file ${ENV_FILE} -p filmbill-v2 start api worker email_worker beat

A backup is only PROVEN by restoring data you can recognise. A dump that
exists proves nothing: the first two taken in the 2026-10-20 drill held an
empty company_bank_accounts and would have restored an empty table just as
happily. See docs/deploy/server-test.md, Step 9.
RESTORE
