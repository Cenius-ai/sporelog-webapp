# Installing SporeLog

SporeLog is one Python file with no runtime dependencies. "Installing" means
having Python 3.9+ and the file; there is nothing to build, no service to
start, no database to create.

## 1. Prerequisites (system level — install these yourself)

| Requirement | Why | Check |
| --- | --- | --- |
| Python **3.9 or newer** | runs `sporelog.py` | `python3 --version` |

No compilers, no `libsqlite3-dev`, no package manager packages, no Docker, no
network access. The tool uses only the standard library (`argparse`, `json`,
`datetime`, `os`, `sys`, `tempfile`, `uuid`).

## 2. Project install (one command, then it exits)

```bash
bash install.sh
```

`install.sh` verifies the interpreter version, imports `sporelog` to prove the
file is intact, and — only if `pytest` is not already available — installs the
single test dependency with pip from `requirements.txt`. It installs nothing at
system level, needs no virtualenv, is safe to re-run, and **exits**; it never
starts anything long-running.

If you would rather do nothing at all: skip this step. `python3 sporelog.py`
works as soon as the file is on disk.

## 3. Seed a store (its own step)

The repository ships a sample log at `./sporelog.json` so `list` and `season`
show real content immediately. To load the same sample data into a store of
your choosing (idempotent — safe to re-run, never overwrites your rows or your
own habitat notes):

```bash
python3 sporelog.py seed                                  # into ./sporelog.json
python3 sporelog.py --store ~/field-log.json seed         # into your own store
```

Starting a genuinely empty log instead:

```bash
rm sporelog.json
python3 sporelog.py list        # "No entries yet." + the example add command
```

## 4. Run it

```bash
python3 sporelog.py --help
python3 sporelog.py list
python3 sporelog.py season
python3 sporelog.py add --species Chanterelle --date 2025-07-14 \
  --location "oak ridge" --count 3 --notes "under log"
```

Nothing binds a port and nothing stays resident: every command runs to
completion and exits `0` on success, `1` on bad input or a broken store, `2` on
a usage error.

Optional environment variable (see `.env.example`):

```bash
export SPORELOG_STORE=./sporelog.json   # same as the built-in default
```

No `.env` file is read; the variable is a plain shell environment variable with
a working default, so the tool runs with no configuration at all.

## 5. Tests

```bash
python3 -m pytest
```

57 tests drive the real CLI as a subprocess against throwaway stores: the
add/list round trip, both filters, every validation and corrupt-store path,
habitat upsert, the recurrence report, the `--json` contracts, the seed's
idempotency and the scripted demo. No network, no fixtures outside the test
file.

## 6. The demo transcript

```bash
bash demo.sh          # or: bash demo/session.sh
```

Runs end to end, non-interactively, against a temporary store that is deleted
on exit. It covers `seed`, `add` with `--notes`, `list`, `list --species`,
`list --month`, `habitat set` with `--notes`, `habitat list`, `season`,
`season --species`, `--json`, and the corrupt-store / bad-argument error lines.

## Uninstalling

Delete the directory (and any store file you created). There is no global
state, no daemon and no cache.
