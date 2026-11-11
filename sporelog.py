#!/usr/bin/env python3
"""SporeLog - a mushroom foraging log, habitat notes and seasonal recurrence report.

Standard library only, single file. Run ``python3 sporelog.py --help``.

Data lives in one JSON document (default ``./sporelog.json``, override with
``--store`` or ``$SPORELOG_STORE``). Writes are atomic: the document is written
to a temporary file in the same directory and swapped in with ``os.replace``, so
an interrupted run never leaves a truncated store behind.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, NoReturn, Sequence

PROG = "sporelog.py"
VERSION = "1.0.0"
SCHEMA_VERSION = 1
STORE_ENV_VAR = "SPORELOG_STORE"
DEFAULT_STORE_PATH = "./sporelog.json"
ISO_DATE_FORMAT = "%Y-%m-%d"
MONTH_ABBR = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
NOTES_COLUMN_WIDTH = 34

# Committed design tokens (calm-precise / terminal dark / compact mono).
# The accent is the single colour this tool ever emits; every use also carries a
# word, so status is never signalled by colour alone.
DESIGN = {
    "accent_oklch": "oklch(0.58 0.12 198)",
    "accent_hex": "#008f94",
    "surface": "terminal dark",
    "type_family": "system monospace stack",
    "density": "compact",
    "signature": "aligned columns, box-drawing separators, one accent for primary status",
}
_ACCENT_RGB = (0x00, 0x8F, 0x94)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class CliError(Exception):
    """A user-facing failure, always printed as exactly one stderr line."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = " ".join(message.split())
        self.exit_code = exit_code


# --------------------------------------------------------------------------- #
# Terminal styling: colour and glyphs degrade safely when redirected / ascii
# --------------------------------------------------------------------------- #
class Ink:
    """The committed accent, used only where a line carries primary status."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def accent(self, text: str) -> str:
        if not self.enabled:
            return text
        red, green, blue = _ACCENT_RGB
        return f"\x1b[38;2;{red};{green};{blue}m{text}\x1b[0m"

    def dim(self, text: str) -> str:
        if not self.enabled:
            return text
        return f"\x1b[2m{text}\x1b[0m"


@dataclass(frozen=True)
class Glyphs:
    rule: str
    dot: str
    recurring: str
    ellipsis: str


UNICODE_GLYPHS = Glyphs(rule="\u2500", dot="\u00b7", recurring="\u21bb", ellipsis="\u2026")
ASCII_GLYPHS = Glyphs(rule="-", dot="*", recurring="*", ellipsis="...")


def _stdout_encoding() -> str:
    return (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "").replace("_", "")


def unicode_supported() -> bool:
    """Box drawing needs a utf-8 capable stdout; ascii terminals get safe glyphs."""
    return _stdout_encoding().startswith("utf")


def color_supported() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def ink() -> Ink:
    return Ink(color_supported())


def glyphs() -> Glyphs:
    return UNICODE_GLYPHS if unicode_supported() else ASCII_GLYPHS


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Entry:
    id: str
    species: str
    date: str
    location: str
    count: int
    notes: str
    created_at: str

    def month(self) -> int:
        return int(self.date[5:7])

    def to_document(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "species": self.species,
            "date": self.date,
            "location": self.location,
            "count": self.count,
            "notes": self.notes,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class HabitatNote:
    location: str
    terrain: str
    trees: str
    notes: str
    updated_at: str

    def to_document(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "terrain": self.terrain,
            "trees": self.trees,
            "notes": self.notes,
            "updated_at": self.updated_at,
        }


@dataclass
class Store:
    path: str
    entries: list[Entry]
    habitats: list[HabitatNote]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Store: one JSON document, read whole, written atomically
# --------------------------------------------------------------------------- #
def _corrupt(path: str, detail: str) -> CliError:
    return CliError(f"corrupt store {path}: {detail}; fix or move the file, then retry")


def _text_field(document: dict[str, Any], key: str) -> str:
    value = document.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _entry_from_document(path: str, document: Any, position: int) -> Entry:
    if not isinstance(document, dict):
        raise _corrupt(path, f"entry #{position + 1} is not a JSON object")
    try:
        species = _text_field(document, "species").strip()
        raw_date = _text_field(document, "date").strip()
        location = _text_field(document, "location").strip()
        notes = _text_field(document, "notes").strip()
        identifier = _text_field(document, "id").strip() or uuid.uuid4().hex[:12]
        created_at = _text_field(document, "created_at").strip() or _now_iso()
        count = document.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise TypeError("count must be a whole number of 1 or more")
    except TypeError as exc:
        raise _corrupt(path, f"entry #{position + 1} is malformed ({exc})") from None
    if not species:
        raise _corrupt(path, f"entry #{position + 1} has no species")
    if not _is_iso_date(raw_date):
        raise _corrupt(path, f"entry #{position + 1} has date {raw_date!r}, expected YYYY-MM-DD")
    return Entry(
        id=identifier,
        species=species,
        date=raw_date,
        location=location,
        count=count,
        notes=notes,
        created_at=created_at,
    )


def _habitat_from_document(path: str, document: Any, position: int) -> HabitatNote:
    if not isinstance(document, dict):
        raise _corrupt(path, f"habitat note #{position + 1} is not a JSON object")
    try:
        location = _text_field(document, "location").strip()
        terrain = _text_field(document, "terrain").strip()
        trees = _text_field(document, "trees").strip()
        notes = _text_field(document, "notes").strip()
        updated_at = _text_field(document, "updated_at").strip() or _now_iso()
    except TypeError as exc:
        raise _corrupt(path, f"habitat note #{position + 1} is malformed ({exc})") from None
    if not location:
        raise _corrupt(path, f"habitat note #{position + 1} has no location")
    return HabitatNote(
        location=location,
        terrain=terrain,
        trees=trees,
        notes=notes,
        updated_at=updated_at,
    )


def _store_from_document(path: str, raw: Any) -> Store:
    if not isinstance(raw, dict):
        raise _corrupt(path, "expected a JSON object at the top level")
    if raw and not {"schema_version", "entries", "habitats"} & set(raw):
        raise _corrupt(path, "unrecognised layout (no schema_version, entries or habitats)")
    version = raw.get("schema_version", SCHEMA_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _corrupt(path, "schema_version must be a positive integer")
    if version > SCHEMA_VERSION:
        raise CliError(
            f"store {path} was written by a newer SporeLog (schema_version {version}); "
            f"this build understands schema_version {SCHEMA_VERSION}"
        )
    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise _corrupt(path, "entries must be a list")
    raw_habitats = raw.get("habitats", [])
    if not isinstance(raw_habitats, list):
        raise _corrupt(path, "habitats must be a list")
    return Store(
        path=path,
        entries=[_entry_from_document(path, item, index) for index, item in enumerate(raw_entries)],
        habitats=[_habitat_from_document(path, item, index) for index, item in enumerate(raw_habitats)],
    )


def load_store(path: str) -> Store:
    """Read the whole store; a missing file is an empty store, a bad one is an error."""
    if not os.path.exists(path):
        return Store(path=path, entries=[], habitats=[])
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        return Store(path=path, entries=[], habitats=[])
    except json.JSONDecodeError as exc:
        raise _corrupt(path, f"{exc.msg} at line {exc.lineno} column {exc.colno}") from None
    except UnicodeDecodeError:
        raise _corrupt(path, "the file is not valid UTF-8 text") from None
    except IsADirectoryError:
        raise CliError(f"cannot read store {path}: that path is a directory") from None
    except OSError as exc:
        raise CliError(f"cannot read store {path}: {exc.strerror or exc}") from None
    return _store_from_document(path, raw)


def _remove_temp_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def save_store(store: Store) -> None:
    """Write the whole document atomically: temp file in place, then os.replace."""
    directory = os.path.dirname(os.path.abspath(store.path)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise CliError(f"cannot create store directory {directory}: {exc.strerror or exc}") from None
    document = {
        "schema_version": SCHEMA_VERSION,
        "entries": [entry.to_document() for entry in store.entries],
        "habitats": [note.to_document() for note in store.habitats],
    }
    try:
        handle_fd, temp_path = tempfile.mkstemp(prefix=".sporelog-", suffix=".tmp", dir=directory)
    except OSError as exc:
        raise CliError(f"cannot write store {store.path}: {exc.strerror or exc}") from None
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, store.path)
    except OSError as exc:
        _remove_temp_file(temp_path)
        raise CliError(f"cannot write store {store.path}: {exc.strerror or exc}") from None


# --------------------------------------------------------------------------- #
# Input validation - every failure is one stderr line and exits 1
# --------------------------------------------------------------------------- #
def _is_iso_date(value: str) -> bool:
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        return False
    try:
        datetime.strptime(value, ISO_DATE_FORMAT)
    except ValueError:
        return False
    return True


def require_species(value: str | None, example: str) -> str:
    species = (value or "").strip()
    if not species:
        raise CliError(f"--species is required; example: {example}")
    return species


def require_date(value: str | None) -> str:
    if value is None or not value.strip():
        raise CliError("--date is required, format YYYY-MM-DD (example: --date 2025-07-14)")
    text = value.strip()
    if not _is_iso_date(text):
        raise CliError(f"--date {value.strip()!r} is not a valid YYYY-MM-DD date (example: 2025-07-14)")
    return text


def parse_count(value: str | int | None) -> int:
    if value is None:
        return 1
    text = str(value).strip()
    try:
        count = int(text, 10)
    except ValueError:
        raise CliError(f"--count {value!r} is not a whole number (example: --count 3)") from None
    if count < 1:
        raise CliError(f"--count must be 1 or more (got {count})")
    return count


def parse_month(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    try:
        month = int(text, 10)
    except ValueError:
        raise CliError(f"--month {value!r} is not a number from 1 to 12 (example: --month 7)") from None
    if not 1 <= month <= 12:
        raise CliError(f"--month must be between 1 and 12 (got {month})")
    return month


def require_location(value: str | None) -> str:
    location = (value or "").strip()
    if not location:
        raise CliError(
            'a location is required; example: '
            f'{PROG} habitat set "oak ridge" --terrain slope --trees oak --notes "damp gully"'
        )
    return location


def resolve_store_path(args: argparse.Namespace) -> str:
    chosen = getattr(args, "store", None) or os.environ.get(STORE_ENV_VAR) or DEFAULT_STORE_PATH
    return os.path.expanduser(chosen)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def shorten(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    marker = glyphs().ellipsis
    return text[: max(width - len(marker), 0)].rstrip() + marker


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    right_align: Iterable[int] = (),
) -> list[str]:
    """Aligned monospace columns - the whole point of the committed layout."""
    align_right = set(right_align)
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = ["  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)).rstrip()]
    lines.append(glyphs().rule * (sum(widths) + 2 * (len(headers) - 1)))
    for row in rows:
        cells = [
            cell.rjust(widths[index]) if index in align_right else cell.ljust(widths[index])
            for index, cell in enumerate(row)
        ]
        lines.append("  ".join(cells).rstrip())
    return lines


def heading(title: str, detail: str = "") -> list[str]:
    line = title if not detail else f"{title} {glyphs().rule} {detail}"
    return [line]


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural_form or singular + 's'}"


# --------------------------------------------------------------------------- #
# Commands: add
# --------------------------------------------------------------------------- #
def cmd_add(args: argparse.Namespace) -> int:
    if not any((args.species, args.date, args.location, args.count, args.notes)):
        raise CliError(
            "--species is required; example: "
            f'{PROG} add --species Chanterelle --date 2025-07-14 --location "oak ridge" '
            '--count 3 --notes "under log"'
        )
    species = require_species(args.species, f'{PROG} add --species Chanterelle --date 2025-07-14')
    entry_date = require_date(args.date)
    count = parse_count(args.count)
    store_path = resolve_store_path(args)
    store = load_store(store_path)
    entry = Entry(
        id=uuid.uuid4().hex[:12],
        species=species,
        date=entry_date,
        location=(args.location or "").strip(),
        count=count,
        notes=(args.notes or "").strip(),
        created_at=_now_iso(),
    )
    store.entries.append(entry)
    save_store(store)
    if getattr(args, "json_output", False):
        print(json.dumps({"schema_version": SCHEMA_VERSION, "entry": entry.to_document()},
                         indent=2, ensure_ascii=False))
        return 0
    print(f"Saved {entry.species} {glyphs().rule} {entry.date} {glyphs().rule} "
          f"{entry.location or 'no location'} {glyphs().rule} {entry.count} {glyphs().rule} "
          f"{entry.notes or 'no notes'}")
    print(ink().dim(f"store: {store_path}  (id {entry.id})"))
    return 0


# --------------------------------------------------------------------------- #
# Commands: list
# --------------------------------------------------------------------------- #
def _select_entries(entries: Sequence[Entry], species: str | None, month: int | None) -> list[Entry]:
    wanted_species = (species or "").strip().casefold()
    selected = [
        entry
        for entry in entries
        if (not wanted_species or entry.species.casefold() == wanted_species)
        and (month is None or entry.month() == month)
    ]
    return sorted(selected, key=lambda entry: (entry.date, entry.species.casefold()), reverse=True)


def cmd_list(args: argparse.Namespace) -> int:
    month = parse_month(args.month)
    species = (args.species or "").strip()
    store = load_store(resolve_store_path(args))
    selected = _select_entries(store.entries, species, month)

    if getattr(args, "json_output", False):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "filters": {"species": species or None, "month": month},
            "count": len(selected),
            "entries": [entry.to_document() for entry in selected],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not store.entries:
        print("No entries yet.")
        print(f'Add your first find: {PROG} add --species Chanterelle --date 2025-07-14 '
              f'--location "oak ridge" --count 3 --notes "under log"')
        return 0
    if not selected:
        filters = []
        if species:
            filters.append(f"--species {species}")
        if month is not None:
            filters.append(f"--month {month}")
        print(f"No entries match {' '.join(filters)}. {plural(len(store.entries), 'find')} in the log; "
              f"drop a filter or run `{PROG} list`.")
        return 0

    month_label = MONTH_ABBR[month - 1] if month is not None else "all months"
    lines = heading("SporeLog", f"{plural(len(selected), 'find')} {glyphs().rule} {month_label}")
    rows = [
        [
            entry.species,
            entry.date,
            entry.location or "-",
            str(entry.count),
            shorten(entry.notes, NOTES_COLUMN_WIDTH) or "-",
        ]
        for entry in selected
    ]
    lines.extend(render_table(["SPECIES", "DATE", "LOCATION", "COUNT", "NOTES"], rows, right_align={3}))
    print("\n".join(lines))
    return 0


# --------------------------------------------------------------------------- #
# Commands: habitat set / habitat list
# --------------------------------------------------------------------------- #
def cmd_habitat_set(args: argparse.Namespace) -> int:
    """Upsert: the first `set` creates the note, a later one updates it in place.

    A field you leave off is kept as it was, so re-running `set` to correct the
    terrain never quietly erases the free-text note. Pass an empty string
    (`--notes ""`) to clear a field on purpose.
    """
    location = require_location(args.location)
    store = load_store(resolve_store_path(args))
    existing = next(
        (item for item in store.habitats if item.location.casefold() == location.casefold()),
        None,
    )

    def merged(new_value: str | None, old_value: str) -> str:
        return old_value if new_value is None else new_value.strip()

    note = HabitatNote(
        location=existing.location if existing else location,
        terrain=merged(args.terrain, existing.terrain if existing else ""),
        trees=merged(args.trees, existing.trees if existing else ""),
        notes=merged(args.notes, existing.notes if existing else ""),
        updated_at=_now_iso(),
    )
    if existing is None:
        store.habitats.append(note)
        action = "Saved"
    else:
        store.habitats = [note if item is existing else item for item in store.habitats]
        action = "Updated"
    store.habitats.sort(key=lambda item: item.location.casefold())
    save_store(store)
    if getattr(args, "json_output", False):
        print(json.dumps(
            {"schema_version": SCHEMA_VERSION, "created": existing is None,
             "habitat": note.to_document()},
            indent=2, ensure_ascii=False,
        ))
        return 0
    print(f"{action} habitat {glyphs().rule} {note.location} {glyphs().rule} "
          f"terrain {note.terrain or '-'} {glyphs().rule} trees {note.trees or '-'} "
          f"{glyphs().rule} {note.notes or 'no notes'}")
    return 0


def cmd_habitat_list(args: argparse.Namespace) -> int:
    store = load_store(resolve_store_path(args))
    if getattr(args, "json_output", False):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "count": len(store.habitats),
            "habitats": [note.to_document() for note in store.habitats],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not store.habitats:
        print("No habitat notes yet.")
        print(f'Create one: {PROG} habitat set "oak ridge" --terrain slope --trees oak --notes "damp gully"')
        return 0
    lines = heading("SporeLog", f"{plural(len(store.habitats), 'habitat note')}")
    rows = [
        [
            note.location,
            note.terrain or "-",
            note.trees or "-",
            shorten(note.notes, NOTES_COLUMN_WIDTH) or "-",
        ]
        for note in store.habitats
    ]
    lines.extend(render_table(["LOCATION", "TERRAIN", "TREES", "NOTES"], rows))
    print("\n".join(lines))
    return 0


# --------------------------------------------------------------------------- #
# Commands: season
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MonthTally:
    month: int
    finds: int
    years: tuple[int, ...]

    @property
    def recurring(self) -> bool:
        return len(self.years) >= 2


def group_by_species(entries: Sequence[Entry], species: str | None) -> dict[str, list[MonthTally]]:
    """One pass over the entries: (species, month) -> finds + distinct years."""
    wanted = (species or "").strip().casefold()
    buckets: dict[str, dict[int, dict[str, Any]]] = {}
    for entry in entries:
        if wanted and entry.species.casefold() != wanted:
            continue
        months = buckets.setdefault(entry.species, {})
        bucket = months.setdefault(entry.month(), {"finds": 0, "years": set()})
        bucket["finds"] += 1
        bucket["years"].add(int(entry.date[:4]))
    grouped: dict[str, list[MonthTally]] = {}
    for name in sorted(buckets, key=str.casefold):
        grouped[name] = [
            MonthTally(month=month, finds=bucket["finds"], years=tuple(sorted(bucket["years"])))
            for month, bucket in sorted(buckets[name].items())
        ]
    return grouped


def cmd_season(args: argparse.Namespace) -> int:
    species = (args.species or "").strip()
    store = load_store(resolve_store_path(args))
    grouped = group_by_species(store.entries, species)

    if getattr(args, "json_output", False):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "filters": {"species": species or None},
            "species": [
                {
                    "species": name,
                    "total_finds": sum(tally.finds for tally in tallies),
                    "months": [
                        {
                            "month": tally.month,
                            "month_name": MONTH_ABBR[tally.month - 1],
                            "finds": tally.finds,
                            "years": list(tally.years),
                            "recurring": tally.recurring,
                        }
                        for tally in tallies
                    ],
                }
                for name, tallies in grouped.items()
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not store.entries:
        print("Nothing to summarise yet.")
        print(f'Log your first find: {PROG} add --species Chanterelle --date 2025-07-14 '
              f'--location "oak ridge" --count 3 --notes "under log"')
        return 0
    if not grouped:
        print(f"No entries for species {species!r}.")
        print(f"Run `{PROG} list` to see what is in the log, or drop --species.")
        return 0

    total = sum(tally.finds for tallies in grouped.values() for tally in tallies)
    recurring_months = sum(1 for tallies in grouped.values() for tally in tallies if tally.recurring)
    lines = heading(
        "SporeLog seasonal recurrence",
        f"{plural(len(grouped), 'species', 'species')} {glyphs().rule} {plural(total, 'find')} "
        f"{glyphs().rule} {plural(recurring_months, 'recurring month')}",
    )
    def tally_row(tally: MonthTally) -> str:
        years = ", ".join(str(year) for year in tally.years)
        return f"  {MONTH_ABBR[tally.month - 1]}  {plural(tally.finds, 'find'):>8}  years {years}"

    # The recurring marker is a column of its own, so pad every row to the widest
    # tally plus two spaces instead of guessing a fixed width.
    marker_column = 2 + max(len(tally_row(tally)) for tallies in grouped.values() for tally in tallies)
    style = ink()
    marker = glyphs().recurring
    for name, tallies in grouped.items():
        lines.append("")
        lines.append(name)
        for tally in tallies:
            row = tally_row(tally)
            if tally.recurring:
                row = f"{row:<{marker_column}}{style.accent(f'{marker} recurring')}"
            lines.append(row.rstrip())
    print("\n".join(lines))
    return 0


# --------------------------------------------------------------------------- #
# Commands: seed
# --------------------------------------------------------------------------- #
SAMPLE_ENTRIES: tuple[tuple[str, str, str, int, str], ...] = (
    ("Chanterelle", "2022-07-09", "oak ridge", 4, "under leaf litter by the fence"),
    ("Chanterelle", "2023-07-14", "oak ridge", 3, "under log"),
    ("Chanterelle", "2024-07-21", "oak ridge", 2, "same log, two weeks later"),
    ("Chanterelle", "2024-09-08", "birch hollow", 1, "late flush after a wet week"),
    ("Oyster", "2023-10-02", "birch hollow", 6, "stacked on a fallen birch"),
    ("Oyster", "2024-10-19", "birch hollow", 5, "same trunk, lower tier"),
    ("Oyster", "2025-01-11", "birch hollow", 2, "mild winter, still firm"),
    ("Hedgehog", "2023-09-24", "moss bank", 3, "under hemlock, deer trail"),
    ("Hedgehog", "2024-09-15", "moss bank", 4, "scattered along the bank"),
    ("King Bolete", "2024-08-03", "pine knoll", 1, "single button, ants got there"),
    ("King Bolete", "2025-08-16", "pine knoll", 2, "after three days of rain"),
    ("Shaggy Inkcap", "2025-05-18", "lane verge", 5, "gravel shoulder after mowing"),
    ("Wood Ear", "2024-12-07", "birch hollow", 8, "on dead elder"),
    ("Meadow Waxcap", "2025-06-29", "church meadow", 12, "unmown strip by the gate"),
)

SAMPLE_HABITATS: tuple[tuple[str, str, str, str], ...] = (
    ("oak ridge", "gentle east slope", "oak, hazel",
     "Damp gully along the north fence"),
    ("birch hollow", "shaded hollow", "silver birch, elder",
     "Fallen birch held off the ground"),
    ("pine knoll", "sandy rise", "scots pine, heather",
     "Thin sand soil, drains fast"),
    ("moss bank", "steep north-facing bank", "hemlock, moss",
     "Deer trail crosses the mid-slope"),
    ("church meadow", "level meadow", "hawthorn hedge, grass",
     "Unmown strip left along the wall"),
)


def cmd_seed(args: argparse.Namespace) -> int:
    """Load a small realistic sample log; safe to re-run, never overwrites your data."""
    store_path = resolve_store_path(args)
    store = load_store(store_path)
    known_entries = {(entry.species.casefold(), entry.date, entry.location.casefold()) for entry in store.entries}
    known_locations = {note.location.casefold() for note in store.habitats}
    added_entries = 0
    for species, entry_date, location, count, notes in SAMPLE_ENTRIES:
        key = (species.casefold(), entry_date, location.casefold())
        if key in known_entries:
            continue
        store.entries.append(
            Entry(
                id=uuid.uuid4().hex[:12],
                species=species,
                date=entry_date,
                location=location,
                count=count,
                notes=notes,
                created_at=_now_iso(),
            )
        )
        known_entries.add(key)
        added_entries += 1
    added_habitats = 0
    for location, terrain, trees, notes in SAMPLE_HABITATS:
        if location.casefold() in known_locations:
            continue
        store.habitats.append(
            HabitatNote(
                location=location,
                terrain=terrain,
                trees=trees,
                notes=notes,
                updated_at=_now_iso(),
            )
        )
        known_locations.add(location.casefold())
        added_habitats += 1
    if added_entries or added_habitats:
        store.habitats.sort(key=lambda item: item.location.casefold())
        save_store(store)
        print(f"Seeded {plural(added_entries, 'sample find')} and "
              f"{plural(added_habitats, 'habitat note')} into {store_path}")
    else:
        print(f"Nothing to seed: {store_path} already holds "
              f"{plural(len(store.entries), 'find')} and {plural(len(store.habitats), 'habitat note')}.")
    print(f"Next: {PROG} list  {glyphs().dot}  {PROG} season")
    return 0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def _store_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--store",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=f"JSON store path (default: ${STORE_ENV_VAR} or {DEFAULT_STORE_PATH})",
    )
    return parent


def _report_parent() -> argparse.ArgumentParser:
    parent = _store_parent()
    parent.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print machine-readable JSON instead of human text",
    )
    return parent


EXAMPLES = f"""examples:
  {PROG} add --species Chanterelle --date 2025-07-14 --location "oak ridge" \\
      --count 3 --notes "under log"
  {PROG} list --species Chanterelle --month 7
  {PROG} habitat set "oak ridge" --terrain slope --trees oak --notes "damp gully"
  {PROG} habitat list
  {PROG} season --species Chanterelle
  {PROG} list --month 7 --json      machine-readable output for scripting
  {PROG} --store /tmp/field-log.json list
  {PROG} seed            load a small realistic sample log (idempotent)

design:
  {DESIGN['signature']} - accent {DESIGN['accent_oklch']} ({DESIGN['accent_hex']}), \
{DESIGN['surface']}

exit codes:
  0 success (including "nothing to show" messages)   1 bad input or a broken store
  2 usage error (unknown command or option)
"""


class SporeLogParser(argparse.ArgumentParser):
    """argparse with one-line errors instead of a usage dump."""

    def error(self, message: str) -> NoReturn:
        raise CliError(_humanise_usage_error(message), exit_code=2)


def _humanise_usage_error(message: str) -> str:
    invalid_choice = re.search(r"invalid choice: '([^']*)'", message)
    if invalid_choice:
        head = message.split(": invalid choice", 1)[0]
        subject = "action" if "action" in head else "command"
        return f"unknown {subject} {invalid_choice.group(1)!r}; try: {PROG} --help"
    missing = re.search(r"the following arguments are required: (.+)", message)
    if missing:
        return f"missing required argument {missing.group(1)}; try: {PROG} --help"
    unrecognised = re.search(r"unrecognized arguments: (.+)", message)
    if unrecognised:
        return f"unrecognized option {unrecognised.group(1)}; try: {PROG} --help"
    return f"{message}; try: {PROG} --help"


def build_parser() -> argparse.ArgumentParser:
    parser = SporeLogParser(
        prog=PROG,
        description=(
            "SporeLog - log mushroom finds, keep habitat notes per location, "
            "and see when each species recurs."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {VERSION}")
    parser.add_argument(
        "--store",
        metavar="PATH",
        default=None,
        help=f"JSON store path (default: ${STORE_ENV_VAR} or {DEFAULT_STORE_PATH})",
    )
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    add_parser = subcommands.add_parser(
        "add",
        parents=[_report_parent()],
        help="record one foraging find",
        description="Record one find with species, date, optional location, count and notes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f'example:\n  {PROG} add --species Chanterelle --date 2025-07-14 '
               f'--location "oak ridge" --count 3 --notes "under log"',
    )
    add_parser.add_argument("--species", metavar="NAME", help="species name, e.g. Chanterelle (required)")
    add_parser.add_argument("--date", metavar="YYYY-MM-DD", help="date of the find (required)")
    add_parser.add_argument("--location", metavar="PLACE", default="",
                            help='where you found it, e.g. "oak ridge"')
    add_parser.add_argument("--count", metavar="N", default=None,
                            help="how many specimens, whole number 1 or more (default 1)")
    add_parser.add_argument("--notes", metavar="TEXT", default="",
                            help='free-text note, e.g. "under log"')
    add_parser.set_defaults(handler=cmd_add)

    list_parser = subcommands.add_parser(
        "list",
        parents=[_report_parent()],
        help="list recorded finds, optionally filtered",
        description="List recorded finds, newest first, optionally filtered by species and month.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"example:\n  {PROG} list --species Chanterelle --month 7",
    )
    list_parser.add_argument("--species", metavar="NAME", help="only this species (case-insensitive)")
    list_parser.add_argument("--month", metavar="1-12", help="only finds dated in this month")
    list_parser.set_defaults(handler=cmd_list)

    habitat_parser = subcommands.add_parser(
        "habitat",
        help="habitat notes per location",
        description="Keep one reusable habitat profile per location: terrain, trees and notes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f'examples:\n  {PROG} habitat set "oak ridge" --terrain slope --trees oak '
               f'--notes "damp gully"\n  {PROG} habitat list',
    )
    subcommands_habitat = habitat_parser.add_subparsers(dest="habitat_command", metavar="<action>")

    habitat_set = subcommands_habitat.add_parser(
        "set",
        parents=[_report_parent()],
        help="create or update the habitat note for a location",
        description="Create or update (never duplicate) the habitat note for one location.",
    )
    habitat_set.add_argument("location", nargs="?", default=None, metavar="LOCATION",
                             help='location name, e.g. "oak ridge" (required)')
    habitat_set.add_argument("--terrain", metavar="TEXT", default=None,
                             help="e.g. slope, damp hollow (kept as-is when omitted)")
    habitat_set.add_argument("--trees", metavar="TEXT", default=None,
                             help="e.g. oak, beech (kept as-is when omitted)")
    habitat_set.add_argument("--notes", metavar="TEXT", default=None,
                             help='free text, e.g. "damp gully" (kept as-is when omitted)')
    habitat_set.set_defaults(handler=cmd_habitat_set)

    habitat_list = subcommands_habitat.add_parser(
        "list",
        parents=[_report_parent()],
        help="list habitat notes",
        description="List every habitat note: location, terrain, trees and notes.",
    )
    habitat_list.set_defaults(handler=cmd_habitat_list)

    season_parser = subcommands.add_parser(
        "season",
        parents=[_report_parent()],
        help="seasonal recurrence report per species",
        description=(
            "Group findings by species and month: find count, distinct years seen, "
            "and a recurring marker for months seen in two or more years."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"example:\n  {PROG} season --species Chanterelle",
    )
    season_parser.add_argument("--species", metavar="NAME", help="only this species (case-insensitive)")
    season_parser.set_defaults(handler=cmd_season)

    seed_parser = subcommands.add_parser(
        "seed",
        parents=[_store_parent()],
        help="load a small realistic sample log (idempotent)",
        description=(
            "Load sample finds and habitat notes into the store so the report has content. "
            "Existing rows and your own habitat notes are left untouched."
        ),
    )
    seed_parser.set_defaults(handler=cmd_seed)

    return parser


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def _print_error(error: CliError) -> None:
    sys.stderr.write(f"error: {error.message}\n")
    sys.stderr.flush()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except CliError as error:
        _print_error(error)
        return error.exit_code

    command = getattr(args, "command", None)
    if command is None:
        parser.print_help()
        print(f"\nNew here? {PROG} seed && {PROG} list  {glyphs().dot}  guided walkthrough: bash demo.sh")
        return 0
    if command == "habitat" and getattr(args, "habitat_command", None) is None:
        _print_error(CliError(f"habitat needs an action: set or list; try: {PROG} habitat --help", exit_code=2))
        return 2

    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except CliError as error:
        _print_error(error)
        return error.exit_code
    except KeyboardInterrupt:
        sys.stderr.write("\ninterrupted\n")
        return 130
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except OSError:
            return 0
        return 0


if __name__ == "__main__":
    sys.exit(main())
