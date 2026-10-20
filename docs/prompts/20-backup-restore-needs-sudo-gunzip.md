Before anything else, run `git remote -v`. This prompt is for the FilmBill repo (OddOne1/FilmBill). If the remote is anything else, stop, say so, and do nothing.

# Claude Code prompt — FilmBill: the printed restore command still cannot read the dump

> Pasted inline. Save this entire message as the next free slot in `docs/prompts/` (likely `20-`), add the INDEX line, then work. One commit.
> Origin: Mathias's run of `scripts/server-test-backup.sh` on the TrueNAS host (2026-10-20, after P0b-3 Part 6). The script now writes the dump as root with mode 600 into a root-only (700) folder, and prints a restore line that starts `gunzip -c <file> | sudo …docker-compose… exec -T postgres psql …`. Observed on the server: `gunzip -c <file> | head -3` as the normal user → `Permission denied`; `sudo gunzip -c <file> | head -3` → SQL. So the printed restore fails in its first stage for the very user it is printed for.
> Why the P0b-3 test missed it (17b): `test_server_test_scripts.py` asserts the printed text contains `sudo`; it never executes the printed pipeline under the condition that makes it fail (a backup that the invoking user cannot read). A text assertion proved a word existed, not that the command works.

## Fix
1. In `scripts/server-test-backup.sh`, the printed restore command reads the file with `sudo gunzip -c <file> | sudo …docker-compose… exec -T postgres psql …`. Check the other printed lines (stop/start services) are consistent. Do not change the file mode or folder mode; they are right.
2. `docs/deploy/server-test.md`, Step 9 and any other place that shows a restore or lists/reads the backups folder: use `sudo` on every stage that reads it, and say in one sentence why (the folder is root-only, so a `*` glob must be expanded by root: `sudo sh -c '…*.sql.gz'`; a plain `chmod 600 …/*.sql.gz` after the folder became 700 fails with "no matches found", which Mathias hit). Grep the docs and scripts for any other command that expands a glob in that folder as the invoking user.

## Test — execute the printed command, do not read it
Replace or extend the text assertion with a behavioural test in `apps/api/tests/test_server_test_scripts.py` (or its neighbour):
- Temporary `DATA_DIR`; a stub `PATH` directory containing: a fake `sudo` that sets an environment flag and then execs its arguments; a wrapper `gunzip` that exits with `gzip: <file>: Permission denied` unless that flag is set (this stands in for root-only files); a stub compose binary (via the script's existing `DOCKER_COMPOSE` override) whose `exec -T postgres psql …` reads stdin to a temp file.
- Run the backup script, extract the printed restore command, run it with `bash -c` in that PATH, and assert that the stub psql received exactly the SQL that the stub pg_dump wrote (compare content, not just that something arrived).
- A second case: run the same extracted command with the `sudo` flag stripped from the `gunzip` stage and assert it fails with the permission error (this proves the wrapper really models the server).
**Mutation, a real FAIL line:** remove `sudo` before `gunzip` in the printed text → the test fails. Revert.

## Report format
One or two sentences, then three tiers (run and observed, statically checked only, not checked). Say whether the server needs a rebuild for this (it should not: the script is a plain file run from the cloned repo, so `git pull` alone should suffice; confirm that nothing in the change is baked into an image). Push when green and CI is green.
