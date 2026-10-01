# Claude Code prompt — FilmBill: dev mail is never delivered (SMTP mode `none`)

> Run in: **Terminal → `cd ~/Claude/Projects/FilmBill/repo` → `claude`**. Save as `docs/prompts/16-dev-mail-smtp-none.md`, add an INDEX line.
> Reproduced by Claude Code on 2026-10-01 before this prompt existed: the dev stack's effective mail config is `implicit_tls` against Mailpit, and a real send fails with `[SSL: WRONG_VERSION_NUMBER]`.

---

PROBLEM (you already reproduced it): the dev stack resolves SMTP security to implicit_tls against Mailpit's plaintext port 1025 -> [SSL: WRONG_VERSION_NUMBER], so no dev mail is ever delivered. smtp_security_from maps use_tls=False -> implicit_tls deliberately (behaviour-preserving, P0b-0). Do NOT change that mapping. Do NOT change production defaults.

FIX
1. docker-compose.dev.yml, in the &api_env block (worker, email_worker, beat inherit it): add SMTP_SECURITY: "none". Rewrite the comment above the block to say why (use_tls=false means implicit TLS; Mailpit is plaintext), so nobody tidies it back. Remove SMTP_USE_TLS from that block only if nothing else reads it; report which.
2. Confirm with `docker compose -f docker-compose.dev.yml config` that the resolved environment of api, worker, email_worker and beat all carry SMTP_SECURITY=none even though .env sets SMTP_USE_TLS (compose environment: overrides env_file).
3. .env.example, README, docs/: anywhere a plaintext relay is described via SMTP_USE_TLS=false, correct it to SMTP_SECURITY=none. Anything in prod files that would hit the same trap: report only, change nothing.
4. Any repo doc saying "the code arrives in Mailpit" as an acceptance step for P0b-0/1/2: add a one-line note that it was unreachable until this commit.

TESTS (behavioural, rule 11, no source-text assertions)
1. Parse docker-compose.dev.yml as YAML (follow the anchor), take the resolved api environment, feed ONLY those variables through the real settings/MailConfig loading path, assert security mode "none", host mailpit, port 1025. Must show a real FAIL line on the parent commit (rule 12).
2. worker, email_worker and beat resolve to the same mail settings as api. A later service with its own env block must fail this.
3. One real delivery: through the real email service path (no mock transport) to Mailpit, then fetch it from Mailpit's API by recipient and assert subject + recipient. If it cannot run in CI without the dev stack, skip it loudly with a named reason; run it yourself for the report. Also drive one send through the Celery email_worker path, which you did not check.
Mutation checks, real FAIL line each: remove SMTP_SECURITY from the block (1); give email_worker its own env block without it (2). Revert, show green.

REPORT-ONLY, do not fix here: filmbill_web shows (unhealthy) in `docker compose ps` while serving on 3100. Find what its healthcheck probes, whether that path returns 200, and propose the fix.

State plainly in the report that P0b-0 fixed the mechanism and its acceptance never exercised the dev compose against Mailpit. Do not extend CLAUDE.md 17b; record this as one more instance for the P0b-3 consolidation. Nothing pushed to any registry (rule 18).
