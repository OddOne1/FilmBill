#!/usr/bin/env bash
#
# pg_dump the LAN test instance into ${DATA_DIR}/backups/, keep the last N,
# print the restore command. Nothing else.
#
# It does not stop the stack, touch any other container, or delete anything
# outside its own backups directory.
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

DEST="${DATA_DIR}/backups"
mkdir -p "${DEST}"
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

cat <<RESTORE

To restore this dump (it DROPs and recreates the objects it contains):

  gunzip -c ${FILE} \\
    | ${COMPOSE} -f ${REPO_ROOT}/docker-compose.server-test.yml \\
        --env-file ${ENV_FILE} -p filmbill-v2 \\
        exec -T postgres psql -U ${PGUSER} -d ${PGDB}

Stop the api, worker, email_worker and beat services first so nothing writes
during the restore:

  ${COMPOSE} -f ${REPO_ROOT}/docker-compose.server-test.yml --env-file ${ENV_FILE} -p filmbill-v2 stop api worker email_worker beat
  (restore, then)
  ${COMPOSE} -f ${REPO_ROOT}/docker-compose.server-test.yml --env-file ${ENV_FILE} -p filmbill-v2 start api worker email_worker beat
RESTORE
