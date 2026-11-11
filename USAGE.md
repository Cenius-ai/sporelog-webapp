# Using SporeLog

Every example below is copy-pasteable from the repository root and uses the
default store (`./sporelog.json`) unless it says otherwise.

## 1. Log a find

```bash
python3 sporelog.py add --species Chanterelle --date 2025-07-14 \
  --location "oak ridge" --count 3 --notes "under log"
```

```
Saved Chanterelle ─ 2025-07-14 ─ oak ridge ─ 3 ─ under log
store: ./sporelog.json  (id 8c1d90f2a4b3)
```

- `--species` and `--date` are required; the date must be a real ISO date
  (`YYYY-MM-DD`, zero padded).
- `--location`, `--count` and `--notes` are optional; `--count` defaults to 1
  and must be a whole number of 1 or more.
- A bad argument prints one line and exits 1 **without writing the store**:

```bash
python3 sporelog.py add --species Chanterelle --date 2025-13-40
# error: --date '2025-13-40' is not a valid YYYY-MM-DD date (example: 2025-07-14)
```

## 2. Read the log back

```bash
python3 sporelog.py list
```

```
SporeLog ─ 14 finds ─ all months
SPECIES        DATE        LOCATION       COUNT  NOTES
───────────────────────────────────────────────────────────────────────────────
King Bolete    2025-08-16  pine knoll         2  after three days of rain
Meadow Waxcap  2025-06-29  church meadow     12  unmown strip by the gate
```

- Newest first; long notes are shortened with `…` in the table — use `--json`
  for the full text.
- Empty log: `No entries yet.` plus the example `add` command.
- Filters that match nothing say so and never pretend the log is empty:
  `No entries match --species Morel. 14 finds in the log; drop a filter or run …`

### Narrow by species

```bash
python3 sporelog.py list --species Chanterelle
python3 sporelog.py list --species chanterelle   # case-insensitive
```

### Narrow by month

```bash
python3 sporelog.py list --month 7
python3 sporelog.py list --species Chanterelle --month 7
```

`--month` takes 1–12; `--month 13` is one error line and exit 1.

## 3. Keep habitat notes

One reusable profile per location — terrain, trees and a free-text note that
carries everything else worth remembering.

```bash
python3 sporelog.py habitat set "oak ridge" --terrain "gentle east slope" \
  --trees "oak, hazel" --notes "damp gully along the north fence"
python3 sporelog.py habitat list
```

```
SporeLog ─ 5 habitat notes
LOCATION       TERRAIN                  TREES                  NOTES
───────────────────────────────────────────────────────────────────────────────────────────────
birch hollow   shaded hollow            silver birch, elder    Fallen birch held off the ground
church meadow  level meadow             hawthorn hedge, grass  Unmown strip left along the wall
moss bank      steep north-facing bank  hemlock, moss          Deer trail crosses the mid-slope
oak ridge      gentle east slope        oak, hazel             Damp gully along the north fence
pine knoll     sandy rise               scots pine, heather    Thin sand soil, drains fast
```

Running `set` again on the same location updates it in place:

```bash
python3 sporelog.py habitat set "oak ridge" --terrain ridge
# Updated habitat ─ oak ridge ─ terrain ridge ─ trees oak, hazel ─ damp gully along the north fence
```

A field you omit keeps its previous value, so a correction never erases the
note. Clear a field deliberately with an empty string: `--notes ""`.

A habitat note can exist before, after, or without any entries at that
location — locations are plain names, not registered entities.

```bash
python3 sporelog.py habitat set
# error: a location is required; example: sporelog.py habitat set "oak ridge" --terrain slope --trees oak --notes "damp gully"
```

## 4. See the season

```bash
python3 sporelog.py season
```

```
SporeLog seasonal recurrence ─ 7 species ─ 14 finds ─ 4 recurring months

Chanterelle
  Jul   3 finds  years 2022, 2023, 2024  ↻ recurring
  Sep    1 find  years 2024

Hedgehog
  Sep   2 finds  years 2023, 2024        ↻ recurring
```

- One block per species, one line per month found in that species.
- Each line: month, number of finds, the distinct years seen.
- `↻ recurring` marks a month seen in two or more distinct years. The word is
  always printed, so the marker never depends on colour.
- `--species NAME` narrows the report to one block.
- Empty log: `Nothing to summarise yet.` plus the example `add` command.

## 5. Script against it

```bash
python3 sporelog.py season --species Oyster --json
```

```json
{
  "schema_version": 1,
  "filters": { "species": "Oyster" },
  "species": [
    {
      "species": "Oyster",
      "total_finds": 3,
      "months": [
        { "month": 10, "month_name": "Oct", "finds": 2, "years": [2023, 2024], "recurring": true }
      ]
    }
  ]
}
```

Write commands accept `--json` too, so a script never has to parse the human
line: `add --json` returns `{schema_version, entry}`, `habitat set --json`
returns `{schema_version, created, habitat}`, `list --json` returns
`{schema_version, filters, count, entries}` and `habitat list --json` returns
`{schema_version, count, habitats}`. Results go to stdout, so
`python3 sporelog.py list --json | jq '.count'` works.

## 6. Keep several logs

```bash
python3 sporelog.py --store ~/field-log.json add --species Oyster --date 2025-10-04
python3 sporelog.py --store ~/field-log.json list
export SPORELOG_STORE=~/field-log.json     # or set it once per shell
python3 sporelog.py list                   # same store
```

## 7. When something is wrong

| Symptom | What you get |
| --- | --- |
| corrupt / unreadable store | one line: `error: corrupt store <path>: … ; fix or move the file, then retry`, exit 1 |
| store from a newer SporeLog | one line naming the `schema_version`, exit 1, file untouched |
| unknown command / option | one line, exit 2 — `error: unknown command 'nonsense'; try: sporelog.py --help` |
| bad `--species`, `--date`, `--count`, `--month`, missing location | one line naming the bad argument, exit 1, store untouched |

No traceback is ever printed, and no error is ever written to stdout.

## 8. The guided demo

```bash
bash demo.sh
```

Runs every command above against a throwaway store in a temp directory, prints
the command and its output, and checks the exit codes — including the corrupt
store and bad-argument cases. Exit 0 means every step behaved as documented.
