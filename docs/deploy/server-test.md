# FilmBill — LAN-only test instance on the TrueNAS server

A temporary FilmBill you can open from the office LAN, running on the TrueNAS
server next to FreeFrame. **Not production, not reachable from the internet,
not behind Cloudflare.** Its data survives rebuilds.

Everything below runs **in a terminal on your Mac, over SSH to the server**,
unless a step says otherwise. Each step gives the full command — copy the
whole line — and says what to paste back.

**Where you end up:** `http://192.168.1.100:3100`

---

## What this does and does not touch

It creates its own Compose project (`filmbill-v2`), its own network, its own
images and its own data directory. It does not stop, restart, rebuild or
reconfigure FreeFrame, Immich, Cloudflare Tunnel, DockMon, Dockge or anything
else on that host, and no step in this document does either.

Two warnings worth reading once:

- **Mailpit has no authentication.** Anyone on the office LAN who can reach
  `http://192.168.1.100:8125` can read every message this instance has sent —
  including magic codes and invite links, which is the same as being able to
  sign in as anyone on it. Fine for test data. Never put real customer mail
  through it.
- **Do not enable auto-update for the `filmbill-v2-*` containers in DockMon.**
  Every service carries `com.centurylinklabs.watchtower.enable=false`, but
  that label is Watchtower's convention and DockMon does not read it. The
  images are built on the server and exist in no registry, so there is nothing
  legitimate to update them from.

---

## Step 1 — a read-only deploy key (server shell)

The server pulls the code itself. Give it a key that can read and nothing
else, separate from the key on your Mac.

```
ssh truenas_admin@192.168.1.34
```

```
ssh-keygen -t ed25519 -C "filmbill-v2-server-test" -f ~/.ssh/filmbill_deploy -N ""
```

```
cat ~/.ssh/filmbill_deploy.pub
```

**Paste back:** the whole `ssh-ed25519 …` line.

Then, in a browser on your Mac:

1. Open `https://github.com/OddOne1/filmbill/settings/keys`
2. Click **Add deploy key**
3. Title: `truenas server test (read-only)`
4. Key: paste the line above
5. **Leave "Allow write access" UNCHECKED**
6. Click **Add key**

Back in the server shell, tell SSH to use that key for GitHub:

```
printf 'Host github-filmbill\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/filmbill_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
```

```
ssh -T git@github.com -i ~/.ssh/filmbill_deploy
```

**Paste back:** the reply. `Hi OddOne1/filmbill! You've successfully
authenticated, but GitHub does not provide shell access.` is success.

---

## Step 2 — clone the repo (server shell)

**As your normal user, not with `sudo`.** FreeFrame is pulled with
`sudo git pull` and that habit makes git refuse later with *"detected dubious
ownership in repository"* — because the files end up owned by root while you
run git as yourself. Cloning as yourself avoids it entirely.

```
cd /mnt/HDDs/Applications/Dockers/stacks/
```

```
git clone github-filmbill:OddOne1/filmbill.git filmbill-v2-src
```

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && git log --oneline -1
```

**Paste back:** the one-line commit.

---

## Step 3 — the data directory (server shell)

This is the one thing that must survive everything else. It lives **outside**
the clone, so deleting and re-cloning the source never touches it.

```
sudo mkdir -p /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-data/{postgres,redis,minio,mailpit,backups}
```

Postgres inside its container runs as uid 999 and refuses to start on a
directory it cannot own:

```
sudo chown -R 999:999 /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-data/postgres
```

```
sudo chown -R 999:1000 /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-data/redis
```

MinIO and Mailpit run as root in their images and need no change. (Checked for
the fork named below, not assumed: `pgsty/minio` declares no `USER` and `id`
inside it reports uid 0.)

> **The S3 server here is a community fork, and that is a deliberate,
> bounded choice.**
>
> MinIO withdrew `minio/minio` and `minio/mc` from Docker Hub, and the quay.io
> mirror now requires authentication, so the images this stack used no longer
> resolve anywhere we can reach. The replacement is
> **`pgsty/minio:RELEASE.2026-08-04T00-00-00Z`** — a fork maintained by the
> Pigsty project: AGPL-3.0, **one maintainer**, whose stated scope is to
> "track CVEs and fix bugs", with **no commercial SLA**.
>
> That is acceptable *for this instance and for dev* because this is a LAN
> test environment, and because **production storage is real S3** —
> `docker-compose.prod.yml` runs no MinIO at all. A fork going quiet costs a
> test environment and never a customer's documents.
>
> It is **not** a reason to put this fork behind production storage. If a
> production deployment ever needs self-hosted object storage, that is its own
> decision with its own review, not an inheritance from this file.
>
> `.github/workflows/image-references.yml` now checks weekly that every image
> this repository names still resolves. That check did not exist when the
> MinIO images were withdrawn, which is why nobody noticed for weeks: every
> machine that ran the stack already had the layers cached.

```
ls -la /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-data/
```

**Paste back:** that listing.

> **Snapshots.** Open the TrueNAS UI → **Data Protection → Periodic Snapshot
> Tasks** and check whether a task covers the dataset this path sits in. Tell
> me what you find — I have not assumed either way. If it is not covered,
> Step 9's backup script is the only copy of this instance's data.

---

## Step 4 — generate the environment (server shell)

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && ./scripts/gen-server-test-env.sh
```

It asks six questions. **Press Enter for every one** — the defaults are this
server's values:

| Question | Default |
|---|---|
| Server LAN IP | `192.168.1.100` |
| Web port | `3100` |
| API port | `8100` |
| MinIO S3 port | `9100` |
| Mailpit UI port | `8125` |
| DATA_DIR | `/mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-data` |

It writes `.env.server-test` with fresh random secrets, mode 600, and
**refuses to run twice** — that file holds the only copy of the database
password for the data in Step 3.

**Paste back:** the last two lines it prints.

> **Why `192.168.1.100` and not `192.168.1.34`.** The server has both. `.34`
> is a DHCP lease on `eno2`; `.100` is static on `bond50`. Every port here is
> published on one specific address, and if that address ever stops being on
> the host, `up` fails with **"cannot assign requested address"** — and every
> link already emailed points somewhere nobody answers. `.34` still works for
> SSH; it is just not what the stack should bind to.

---

## Step 5 — pre-flight (server shell)

Read-only. It creates, starts, stops and removes nothing.

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && ./scripts/server-test-preflight.sh
```

**Paste back:** all of its output.

It refuses if a port is taken, if `LAN_IP` is not actually on the host, if
`DATA_DIR` is unset, or if this project is already running (in which case use
Step 8, not Step 6). It warns — and continues — if the address is a DHCP
lease. It also **lists** anything else on the host named `filmbill` without
touching it; if a v1 turns up there, stop and tell me.

---

## Step 6 — first build and start (server shell)

Two separate commands. Never `up --build`: on this host a combined invocation
has silently reused a stale image before, and a stale worker has broken email
in FreeFrame more than once.

**Build** (several minutes; the web image compiles a production Next build):

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 build --no-cache
```

**Then start:**

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 up -d
```

**Paste back:** the last 20 lines of the build, and all of the `up` output.

Check what is running:

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 ps
```

**Paste back:** that table. Every service should be `Up`; `web` and `api` take
up to a minute to report `(healthy)`.

If something is not up:

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 logs --tail=80 api
```

(swap `api` for whichever service, and paste it back)

Database migrations run automatically when `api` starts — there is no separate
step and you should never run one by hand.

---

## Step 7 — first-run setup (your browser, on the office LAN)

Open:

```
http://192.168.1.100:3100
```

A fresh instance has no accounts, so it sends you to the setup screen. Create
the first superadmin there: your name, your email, and a password that
satisfies the policy (12+ characters with an upper-case letter, a lower-case
letter, a digit and a symbol — it tells you if it is short).

Then test that mail works, because almost everything else depends on it:

1. Sign out.
2. On the login screen choose the email-code option and enter your address.
3. Open `http://192.168.1.100:8125` — the Mailpit inbox.
4. The message should be there, and **the link in it must start
   `http://192.168.1.100:3100`**. If it says `localhost`, stop and tell me:
   `FRONTEND_URL` has not reached the API.

**Paste back:** whether the code arrived, and the first line of the link.

---

## Step 8 — updating to newer code (server shell)

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && git pull
```

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 build --no-cache
```

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 up -d
```

Your data is untouched by this — it lives in `DATA_DIR`, not in the
containers.

> **If you change `LAN_IP` or `WEB_PORT`,** the `web` image must be rebuilt,
> not just restarted. The browser's API address is compiled into the
> JavaScript bundle at build time, so a restart would appear to work and keep
> serving the old address.

---

## Step 9 — backup and restore (server shell)

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && ./scripts/server-test-backup.sh
```

It writes a timestamped `pg_dump` into `DATA_DIR/backups/`, keeps the newest
14, and prints the exact restore command for the file it just made.

**Paste back:** its last few lines.

To restore, use the command it printed. It stops `api`, `worker`,
`email_worker` and `beat` first so nothing writes mid-restore — and restarts
them after.

---

## Step 10 — stopping, starting, removing

**Stop** (keeps everything, including data):

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 down
```

**Start again:**

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 up -d
```

> ### Never add `-v` to `down`
>
> `down -v` removes volumes. This stack keeps its data in bind mounts
> precisely so that command cannot reach it, but do not rely on that — `-v`
> appears nowhere in this document or in any script here, and it should not
> start now. There is no reason to ever type it for this stack.

**Remove the test instance completely**, leaving everything else on the host
alone. Four steps, in this order:

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 down
```

```
sudo docker images --format '{{.Repository}}:{{.Tag}}' | grep '^filmbill-v2-' | xargs -r sudo docker rmi
```

```
sudo rm -rf /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src
```

```
sudo rm -rf /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-data
```

The `grep '^filmbill-v2-'` is anchored on purpose: it matches only images this
stack built and cannot match a `filmbill` or `oddone1/filmbill` belonging to a
v1. The last command deletes the data for good — take a backup first if you
want to keep it.

---

## When it will not come up

**`Error ... cannot assign requested address`** — `LAN_IP` is not on the host
any more (a changed DHCP lease, or a network reconfiguration). Check:

```
ip -4 addr | grep inet
```

Then set `LAN_IP` in `.env.server-test` to an address that is listed,
`down`, rebuild `web` (the address is compiled in) and `up -d`.

**`port is already allocated`** — something else took the port since the
pre-flight. Find the owner:

```
sudo lsof -nP -iTCP:3100 -sTCP:LISTEN
```

**`web` stuck `(unhealthy)` but the site works** — tell me rather than
ignoring it. The probe deliberately uses `http://127.0.0.1:3000/login` and not
`localhost`, because `localhost` inside these images resolves to IPv6 first
while the server listens on IPv4 only; FilmBill's dev stack sat
`(unhealthy)` for 213 consecutive checks on exactly that.

**`postgres` exits immediately** — ownership on the data directory. Re-run the
`chown` in Step 3 and paste back:

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 logs --tail=40 postgres
```

**A magic-code link says `localhost`** — `FRONTEND_URL` did not reach the API.
Paste back:

```
cd /mnt/HDDs/Applications/Dockers/stacks/filmbill-v2-src && sudo /usr/libexec/docker/cli-plugins/docker-compose -f docker-compose.server-test.yml --env-file .env.server-test -p filmbill-v2 exec api printenv FRONTEND_URL
```

---

## Things that behave differently on plain `http://`

`http://192.168.1.100:3100` is not a "secure context" the way `https://` and
`http://localhost` are, so a few browser APIs are unavailable. What that
means here:

- **Copying an invite link** falls back to showing you the link to copy by
  hand, instead of putting it on the clipboard. Expected on this instance, not
  a bug.
- Sign-in, two-factor codes, invites, PDFs and everything else work normally:
  the session is a bearer token, and no cookie here is marked `Secure`.
