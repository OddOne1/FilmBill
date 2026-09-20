# FilmBill

Self-hosted billing, accounting and production-resource software for film,
photo and media production companies.

Quotes, invoices and credit notes with a single calculation engine; gap-free
numbering; versioned documents that are never overwritten; an archive built
around what a tax advisor actually asks for; and Rentman-grade equipment and
crew handling as a first-class part of the product rather than an add-on.

Built by [YON Studio OG](https://yon.studio) (Vienna) for its own productions,
and open source so anyone else can run it on their own server.

> **Status: early.** P0a — the platform foundation — is what exists today:
> accounts, two-factor authentication, invites, notifications, email, site
> settings and branding, plus the container stack that the business features
> are built on. There are no invoices yet. See
> [`docs/prompts/INDEX.md`](docs/prompts/INDEX.md) for what has been built and
> [`docs/SCOPE.md`](docs/SCOPE.md) for where it is going.

---

## Why it exists

Production companies sit between two kinds of software that both fit badly.
General accounting tools know nothing about shoot days, rental factors, gear
that is double-booked, or an invoice whose service period is three weeks in
the future. Rental and planning tools know all of that and then hand the
numbers off to something else, where they are retyped.

FilmBill is one system for both halves, with the accounting side taken as
seriously as the planning side: Decimal money end to end, an audit trail from
the first release, and documents that are versioned rather than edited.

## What is here today

- **Accounts** — password and magic-code sign-in, invites, first-run setup
- **Two-factor authentication** — TOTP or email as a second factor, backup
  codes, optional per user and enforceable instance-wide
- **Users and administration** — roles, deactivation, admin 2FA recovery
- **Notifications** — in-app, with per-user email preferences
- **Email** — SMTP or SES, configurable at runtime, credentials encrypted at
  rest
- **Branding** — workspace name, per-theme logos, favicon, theme colours
- **PDF rendering** — Gotenberg wired in and smoke-tested at `/health/pdf`

## Quick start (development)

Requires Docker Desktop.

```bash
git clone git@github.com:OddOne1/filmbill.git
cd filmbill
cp .env.example .env
docker compose -f docker-compose.dev.yml up --build
```

Then open <http://localhost:3100> and complete the setup wizard to create the
first administrator.

| Service | URL |
|---|---|
| Web | <http://localhost:3100> |
| API docs | <http://localhost:8100/docs> |
| Mailpit (catches all outgoing mail) | <http://localhost:8125> |
| MinIO console | <http://localhost:9101> |
| Gotenberg | internal network only, no published port |

Every port is offset from FreeFrame's defaults so both stacks can run side by
side on one machine.

### Running the tests

```bash
# API — inside the image, which is the environment they are written against
docker compose -f docker-compose.dev.yml run --rm api python -m pytest -q

# Web
pnpm install
pnpm --filter web test
pnpm --filter web lint
pnpm --filter web typecheck
```

## Production

```bash
cp .env.example .env.prod          # then edit it
docker compose --env-file .env.prod -f docker-compose.prod.yml build --no-cache
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d
```

Build and up are always two separate commands, and every buildable service is
rebuilt together — a stale worker image next to a fresh API is a failure mode
this project has already paid for once.

Set `DOMAIN` and `ACME_EMAIL` for automatic Let's Encrypt certificates via
Traefik; without them it serves plain HTTP.

## Documentation

| | |
|---|---|
| [`docs/SCOPE.md`](docs/SCOPE.md) | Product scope, data model, and the decisions log |
| [`docs/prompts/INDEX.md`](docs/prompts/INDEX.md) | What has been built, phase by phase |
| [`CLAUDE.md`](CLAUDE.md) | The engineering contract this repository is held to |

## Licence

AGPL-3.0-or-later — see [`LICENSE`](LICENSE).

The platform modules (auth, users, notifications, email, settings, branding)
are derived from [FreeFrame](https://github.com/Techiebutler/freeframe), which
is MIT-licensed. That licence still applies to those portions and is kept in
[`LICENSES/MIT-FreeFrame.txt`](LICENSES/MIT-FreeFrame.txt); [`NOTICE`](NOTICE)
records the exact upstream commit and what was copied.

If you run a modified FilmBill as a network service, the AGPL requires you to
offer your users the source of your modified version.
