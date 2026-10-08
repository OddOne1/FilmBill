# Claude Code prompt — FilmBill: replace the dead MinIO images, and guard against it happening again

> Pasted inline. Save this entire message as the next free slot in `docs/prompts/` (likely `18-`), add the INDEX line, then work.
> Origin: your own investigation report (both `minio/minio` and `minio/mc` are gone from Docker Hub; quay.io needs authentication). Decision by Mathias (2026-10-07): **accept your recommendation**, with the additions below.
> Independent check done by Cowork: Docker Hub lists `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` (pushed about two months ago); the maintainer's own write-up says AGPL-3.0, one maintainer, "track CVEs and fix bugs", explicitly **no commercial SLA**. That is acceptable for dev and a test instance because production uses real S3 (`docker-compose.prod.yml` has no MinIO). It is **not** a reason to depend on this fork for production storage; say so in the docs you touch.

---

## Build (one commit unless a part is large)
1. **Image swap** in `docker-compose.server-test.yml` and `docker-compose.dev.yml`: `minio` → `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` (pinned, no `:latest`). Keep the compose service name `minio` so nothing else changes.
2. **Remove the `minio-init` service** (and its `minio/mc` image) from both files, and its `depends_on` entries. Replace them with `depends_on: minio: condition: service_healthy` where the API waited for the init job before. **Give `minio` a healthcheck that works inside this image** (check which tool the image actually contains; do not assume `curl` or `wget`; `127.0.0.1`, not `localhost`). Prove it by showing the service reach `healthy`.
3. **Grep for every other reference** to `minio/mc`, `minio-init`, `mc alias`, `mc mb` and the old image tag in scripts, docs, README, `docs/deploy/server-test.md`, SCOPE and CI. Update or remove each; list them.
4. **What the API does if S3 is unreachable at startup.** `ensure_bucket_exists` runs at startup and your report says it re-raises. State exactly what happens (crash, retry, or degraded start) and whether compose ordering alone is enough now that the init job is gone. Fix only what is needed so the API tolerates MinIO becoming healthy a few seconds later.

## Guard: image references must resolve (new)
A scheduled GitHub Actions workflow (weekly, plus `workflow_dispatch`, plus on changes to compose files and Dockerfiles) that parses every image reference from all compose files and every Dockerfile `FROM`, skips locally built `filmbill-v2-*` tags, and runs `docker manifest inspect` on each. It fails with the list of references that do not resolve. The parser is a small script with its own tests (compose `image:` with and without a tag, `${VAR}` references, multi-stage `FROM … AS`, build-arg `FROM`).
**Mutation, a real FAIL line:** add a reference to a tag that does not exist → the check fails naming it. Revert.
It must **not** run on every push without the path filter (registry flakiness should not block unrelated work); say what you chose and why.

## Verification — cold cache (17b: the Mac's warm cache is how this was missed)
a. Remove the local `pgsty/minio` image on the Mac and bring the stack up so the **pull really happens**. Say which other images were still cached and could not be cleared.
b. **No init job:** after the first start, upload through the API and show the object exists; show the bucket was created by the API, not by a separate job.
c. **Existing dev volume:** do NOT touch `filmbill_miniodata`. Copy it to a scratch volume, start `pgsty/minio` against the copy, and read an object that the old MinIO wrote. Report the result either way. A scratch copy that fails is information, not an emergency.
d. Re-run the server-test smoke (setup, magic code in Mailpit, invite link carries the LAN URL, company with an IBAN survives `down` and `up`, no `-v`) on the Mac through its LAN IP.
e. The effective-config tests from the server-test prompt still pass (labels, ports, no external networks); update them if they referenced `minio-init`, and say what you changed.
f. Full API and web suites with the environment declared (database, Redis, Mailpit) and the number of skips with reasons.

## Docs
- Note in `docs/deploy/server-test.md` that the S3 server is a community fork, who maintains it, and that production storage must be real S3.
- Add the NOTICE / third-party line for the fork if the repo keeps such a list (separate container; AGPL-3.0; rule 8 concerns code copied into FilmBill, not a service we run).
- In your report give one sentence for the harness-traps file that P0b-3 will write: the **ninth instance** of the 17b family — a warm local Docker cache hid dead images, so a green smoke test said nothing about a fresh host.

## Report format
A 1–3 sentence plain-language summary first. Then three tiers: run and observed, statically checked only, not checked. State what can only be verified on the server (the pull from the server's network, the bind-mount ownership, the first `up`). Push when green. No registry push of any image.
