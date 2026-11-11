"""SporeLog test suite.

Every test drives the real CLI through a subprocess, exactly the way a forager
uses it, against a throwaway store in `tmp_path`. Nothing here touches the
repo's own ./sporelog.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "sporelog.py"
DEMO_SCRIPT = REPO_ROOT / "demo" / "session.sh"


def cli(*args: object, store: Path | None = None, cwd: Path | None = None,
        env: dict[str, str] | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(TOOL)]
    if store is not None:
        argv.extend(["--store", str(store)])
    argv.extend(str(argument) for argument in args)
    run_env = dict(os.environ)
    run_env.pop("SPORELOG_STORE", None)
    run_env.pop("NO_COLOR", None)
    if env:
        run_env.update(env)
    return subprocess.run(
        argv,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
    )


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    return tmp_path / "field-log.json"


def document(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def add_find(store: Path, species: str, entry_date: str, *extra: object) -> subprocess.CompletedProcess:
    return cli("add", "--species", species, "--date", entry_date, *extra, store=store)


def assert_single_error(proc: subprocess.CompletedProcess, expected_code: int = 1) -> str:
    """The contract: exactly one stderr line, no traceback, nothing on stdout."""
    assert proc.returncode == expected_code, proc.stderr
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, proc.stderr
    assert lines[0].startswith("error: "), lines[0]
    assert proc.stdout == "", proc.stdout
    assert "Traceback" not in proc.stderr
    return lines[0]


# --------------------------------------------------------------------------- #
# F1 - foraging log: add + filtered list
# --------------------------------------------------------------------------- #
def test_add_then_list_shows_one_row(store: Path) -> None:
    added = add_find(store, "Chanterelle", "2023-07-14", "--location", "oak ridge", "--count", "3")
    assert added.returncode == 0, added.stderr

    listed = cli("list", store=store)
    assert listed.returncode == 0, listed.stderr
    row = [line for line in listed.stdout.splitlines() if "Chanterelle" in line]
    assert len(row) == 1, listed.stdout
    assert "2023-07-14" in row[0]
    assert "oak ridge" in row[0]
    assert "3" in row[0]

    stored = document(store)
    assert len(stored["entries"]) == 1
    assert stored["schema_version"] == 1
    assert stored["entries"][0]["species"] == "Chanterelle"
    assert stored["entries"][0]["date"] == "2023-07-14"
    assert stored["entries"][0]["count"] == 3


def test_notes_persist_and_are_displayed(store: Path) -> None:
    assert add_find(store, "Chanterelle", "2024-07-02", "--notes", "under log").returncode == 0
    assert document(store)["entries"][0]["notes"] == "under log"
    listed = cli("list", store=store)
    assert "under log" in listed.stdout


def test_add_prints_the_saved_entry(store: Path) -> None:
    added = add_find(store, "Oyster", "2024-09-01", "--notes", "on a fallen birch")
    assert "Oyster" in added.stdout
    assert "2024-09-01" in added.stdout
    assert "on a fallen birch" in added.stdout


def test_count_defaults_to_one(store: Path) -> None:
    assert add_find(store, "Hedgehog", "2024-09-15").returncode == 0
    assert document(store)["entries"][0]["count"] == 1


def test_list_species_filter(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Oyster", "2023-10-02")
    add_find(store, "Chanterelle", "2024-07-21", "--location", "oak ridge")

    listed = cli("list", "--species", "Chanterelle", store=store)
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.count("Chanterelle") == 2
    assert "Oyster" not in listed.stdout


def test_list_species_filter_is_case_insensitive(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    listed = cli("list", "--species", "chanterELLE", store=store)
    assert listed.returncode == 0
    assert "Chanterelle" in listed.stdout


def test_list_month_filter(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Oyster", "2023-10-02")

    july = cli("list", "--month", "7", store=store)
    assert july.returncode == 0, july.stderr
    assert "2023-07-14" in july.stdout
    assert "2023-10-02" not in july.stdout
    assert "Oyster" not in july.stdout


def test_list_combines_species_and_month_filters(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Chanterelle", "2023-09-14")
    add_find(store, "Oyster", "2023-07-02")

    listed = cli("list", "--species", "Chanterelle", "--month", "7", store=store)
    assert listed.returncode == 0, listed.stderr
    assert "2023-07-14" in listed.stdout
    assert "2023-09-14" not in listed.stdout
    assert "Oyster" not in listed.stdout


def test_list_with_filters_that_match_nothing_is_not_the_empty_store_message(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    listed = cli("list", "--species", "Morel", store=store)
    assert listed.returncode == 0
    assert "No entries match" in listed.stdout
    assert "No entries yet" not in listed.stdout
    assert "Morel" in listed.stdout


def test_custom_store_is_isolated_from_the_default_store(tmp_path: Path) -> None:
    custom = tmp_path / "custom.json"
    added = cli("add", "--species", "Oyster", "--date", "2024-09-01", "--store", str(custom),
                cwd=tmp_path)
    assert added.returncode == 0, added.stderr
    assert document(custom)["entries"][0]["species"] == "Oyster"
    assert not (tmp_path / "sporelog.json").exists()

    listed = cli("list", "--store", str(custom), cwd=tmp_path)
    assert "Oyster" in listed.stdout


def test_default_store_path_is_relative_to_the_working_directory(tmp_path: Path) -> None:
    # No --store and no $SPORELOG_STORE: the default ./sporelog.json applies.
    added = subprocess.run(
        [sys.executable, str(TOOL), "add", "--species", "Morel", "--date", "2025-04-12"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=120,
        env={**os.environ, "SPORELOG_STORE": ""},
    )
    assert added.returncode == 0, added.stderr
    assert (tmp_path / "sporelog.json").exists()
    assert document(tmp_path / "sporelog.json")["entries"][0]["species"] == "Morel"


def test_store_env_var_overrides_the_default(tmp_path: Path) -> None:
    env_path = tmp_path / "from-env.json"
    added = cli("add", "--species", "Wood Ear", "--date", "2024-12-07",
                cwd=tmp_path, env={"SPORELOG_STORE": str(env_path)})
    assert added.returncode == 0, added.stderr
    assert document(env_path)["entries"][0]["species"] == "Wood Ear"


def test_store_directory_is_created_on_demand(tmp_path: Path) -> None:
    nested = tmp_path / "field" / "notes" / "log.json"
    assert add_find(nested, "Chanterelle", "2023-07-14").returncode == 0
    assert document(nested)["entries"]


def test_store_is_valid_json_after_repeated_writes(store: Path) -> None:
    for month in range(1, 6):
        assert add_find(store, "Chanterelle", f"2024-{month:02d}-05").returncode == 0
    stored = document(store)
    assert len(stored["entries"]) == 5
    assert len({entry["id"] for entry in stored["entries"]}) == 5
    leftovers = [path.name for path in store.parent.iterdir() if path.name != store.name]
    assert leftovers == [], leftovers


# --------------------------------------------------------------------------- #
# F1 - validation and failure paths
# --------------------------------------------------------------------------- #
def test_invalid_date_is_rejected_and_store_untouched(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    before = store.read_text(encoding="utf-8")

    bad = add_find(store, "Chanterelle", "2023-13-40")
    message = assert_single_error(bad)
    assert "2023-13-40" in message
    assert "YYYY-MM-DD" in message
    assert store.read_text(encoding="utf-8") == before


def test_non_canonical_date_is_rejected(store: Path) -> None:
    assert_single_error(add_find(store, "Chanterelle", "2024-7-2"))
    assert not store.exists()


def test_negative_count_is_rejected(store: Path) -> None:
    message = assert_single_error(add_find(store, "Chanterelle", "2024-07-02", "--count", "-3"))
    assert "count" in message
    assert not store.exists()


def test_non_numeric_count_is_rejected(store: Path) -> None:
    message = assert_single_error(add_find(store, "Chanterelle", "2024-07-02", "--count", "three"))
    assert "--count" in message


def test_missing_species_is_rejected_with_the_example(store: Path) -> None:
    message = assert_single_error(cli("add", store=store))
    assert "--species is required" in message
    assert "add --species Chanterelle" in message
    assert not store.exists()


def test_blank_species_is_rejected(store: Path) -> None:
    assert_single_error(add_find(store, "   ", "2024-07-02"))


def test_missing_date_is_rejected(store: Path) -> None:
    message = assert_single_error(cli("add", "--species", "Chanterelle", store=store))
    assert "--date is required" in message


def test_invalid_month_is_rejected(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    assert_single_error(cli("list", "--month", "13", store=store))
    assert_single_error(cli("list", "--month", "July", store=store))


def test_corrupt_store_reports_one_line_for_every_read_command(store: Path) -> None:
    store.write_text("{ this is not json", encoding="utf-8")
    for command in (("list",), ("season",), ("habitat", "list")):
        message = assert_single_error(cli(*command, store=store))
        assert "corrupt store" in message


def test_corrupt_store_blocks_add_without_overwriting_it(store: Path) -> None:
    store.write_text("garbage", encoding="utf-8")
    assert_single_error(add_find(store, "Chanterelle", "2023-07-14"))
    assert store.read_text(encoding="utf-8") == "garbage"


def test_store_written_by_a_newer_version_is_refused(store: Path) -> None:
    store.write_text(json.dumps({"schema_version": 99, "entries": [], "habitats": []}), encoding="utf-8")
    message = assert_single_error(cli("list", store=store))
    assert "schema_version 99" in message


def test_malformed_entry_inside_the_store_is_reported_as_corrupt(store: Path) -> None:
    store.write_text(json.dumps({"schema_version": 1, "entries": [{"date": "2024-01-01"}],
                                 "habitats": []}), encoding="utf-8")
    message = assert_single_error(cli("list", store=store))
    assert "corrupt store" in message


def test_empty_store_list_invites_the_first_add(store: Path) -> None:
    listed = cli("list", store=store)
    assert listed.returncode == 0
    assert "No entries yet" in listed.stdout
    assert "sporelog.py add --species Chanterelle" in listed.stdout


# --------------------------------------------------------------------------- #
# F2 - habitat notes per location
# --------------------------------------------------------------------------- #
def test_habitat_set_then_list(store: Path) -> None:
    saved = cli("habitat", "set", "oak ridge", "--terrain", "slope", "--trees", "oak",
                "--notes", "damp gully", store=store)
    assert saved.returncode == 0, saved.stderr

    listed = cli("habitat", "list", store=store)
    assert listed.returncode == 0, listed.stderr
    assert "oak ridge" in listed.stdout
    assert "slope" in listed.stdout
    assert "oak" in listed.stdout
    assert "damp gully" in listed.stdout

    notes = document(store)["habitats"]
    assert len(notes) == 1
    assert notes[0]["location"] == "oak ridge"
    assert notes[0]["terrain"] == "slope"


def test_habitat_set_upserts_in_place(store: Path) -> None:
    cli("habitat", "set", "oak ridge", "--terrain", "slope", "--trees", "oak",
        "--notes", "damp gully", store=store)
    updated = cli("habitat", "set", "oak ridge", "--terrain", "ridge", "--trees", "beech", store=store)
    assert updated.returncode == 0, updated.stderr
    assert "Updated" in updated.stdout

    notes = document(store)["habitats"]
    assert len(notes) == 1
    assert notes[0]["terrain"] == "ridge"
    assert notes[0]["trees"] == "beech"
    assert notes[0]["notes"] == "damp gully"  # an omitted field is never erased
    assert cli("habitat", "list", store=store).stdout.count("oak ridge") == 1


def test_habitat_notes_are_kept_per_location(store: Path) -> None:
    cli("habitat", "set", "oak ridge", "--terrain", "slope", store=store)
    cli("habitat", "set", "birch hollow", "--terrain", "hollow", store=store)
    listed = cli("habitat", "list", store=store)
    assert "oak ridge" in listed.stdout
    assert "birch hollow" in listed.stdout
    assert len(document(store)["habitats"]) == 2


def test_habitat_set_requires_a_location(store: Path) -> None:
    message = assert_single_error(cli("habitat", "set", store=store))
    assert "location is required" in message
    assert not store.exists()


def test_habitat_set_rejects_a_blank_location(store: Path) -> None:
    assert_single_error(cli("habitat", "set", "  ", "--terrain", "slope", store=store))


def test_habitat_needs_an_action(store: Path) -> None:
    proc = cli("habitat", store=store)
    assert proc.returncode == 2
    assert proc.stderr.count("\n") == 1
    assert "set or list" in proc.stderr


def test_empty_habitat_list_invites_an_example(store: Path) -> None:
    listed = cli("habitat", "list", store=store)
    assert listed.returncode == 0
    assert "No habitat notes yet" in listed.stdout
    assert "habitat set" in listed.stdout


# --------------------------------------------------------------------------- #
# F3 - seasonal recurrence report
# --------------------------------------------------------------------------- #
def test_season_shows_finds_and_distinct_years_per_month(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Chanterelle", "2024-07-21")

    report = cli("season", store=store)
    assert report.returncode == 0, report.stderr
    july = [line for line in report.stdout.splitlines() if "Jul" in line]
    assert len(july) == 1, report.stdout
    assert "2 finds" in july[0]
    assert "2023, 2024" in july[0]


def test_season_groups_a_block_per_species(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Oyster", "2023-10-02")

    report = cli("season", store=store)
    assert "Chanterelle" in report.stdout
    assert "Oyster" in report.stdout
    chanterelle_block = report.stdout.split("Chanterelle", 1)[1].split("Oyster", 1)[0]
    assert "Jul" in chanterelle_block
    assert "Oct" not in chanterelle_block


def test_season_species_filter_narrows_to_one_block(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Oyster", "2023-10-02")

    report = cli("season", "--species", "Oyster", store=store)
    assert report.returncode == 0, report.stderr
    assert "Oyster" in report.stdout
    assert "Chanterelle" not in report.stdout


def test_season_marks_only_months_seen_in_two_or_more_years(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Chanterelle", "2024-07-21")
    add_find(store, "King Bolete", "2024-07-02")

    report = cli("season", store=store)
    chanterelle_block = report.stdout.split("Chanterelle", 1)[1].split("King Bolete", 1)[0]
    bolete_block = report.stdout.split("King Bolete", 1)[1]
    assert "recurring" in chanterelle_block
    assert "recurring" not in bolete_block


def test_season_with_no_matching_species_says_so(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    report = cli("season", "--species", "Morel", store=store)
    assert report.returncode == 0
    assert "No entries for species" in report.stdout


def test_empty_store_season_invites_the_first_add(store: Path) -> None:
    report = cli("season", store=store)
    assert report.returncode == 0
    assert "Nothing to summarise yet" in report.stdout
    assert "sporelog.py add" in report.stdout


# --------------------------------------------------------------------------- #
# Usage, help and error-path polish
# --------------------------------------------------------------------------- #
def test_help_lists_every_command_and_the_store_option() -> None:
    proc = cli("--help")
    assert proc.returncode == 0
    for command in ("add", "list", "habitat", "season"):
        assert command in proc.stdout
    assert "--store PATH" in proc.stdout
    assert "examples:" in proc.stdout


def test_every_subcommand_has_help() -> None:
    for command in ("add", "list", "habitat", "season", "seed", "habitat set", "habitat list"):
        proc = cli(*command.split(), "--help")
        assert proc.returncode == 0, command
        assert "usage:" in proc.stdout


def test_version_is_reported() -> None:
    proc = cli("--version")
    assert proc.returncode == 0
    assert "1.0.0" in proc.stdout


def test_unknown_command_is_one_line_and_nonzero(store: Path) -> None:
    proc = cli("nonsense", "--store", str(store))
    assert proc.returncode == 2
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, proc.stderr
    assert "nonsense" in lines[0]


def test_unknown_option_is_one_line_and_nonzero(store: Path) -> None:
    proc = cli("list", "--bogus", store=store)
    assert proc.returncode == 2
    lines = [line for line in proc.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, proc.stderr


def test_store_option_is_accepted_before_and_after_the_command(store: Path) -> None:
    assert cli("--store", str(store), "add", "--species", "Morel", "--date", "2025-04-12").returncode == 0
    assert cli("add", "--species", "Oyster", "--date", "2025-09-20", "--store", str(store)).returncode == 0
    listed = cli("list", "--store", str(store))
    assert "Morel" in listed.stdout
    assert "Oyster" in listed.stdout


def test_piped_output_has_no_ansi_escapes(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Chanterelle", "2024-07-21")
    for command in (("list",), ("season",), ("habitat", "list")):
        assert "\x1b[" not in cli(*command, store=store).stdout


def test_bare_invocation_prints_help_and_exits_zero() -> None:
    proc = cli()
    assert proc.returncode == 0
    assert "usage:" in proc.stdout
    assert "bash demo.sh" in proc.stdout


# --------------------------------------------------------------------------- #
# Machine-readable contract
# --------------------------------------------------------------------------- #
def test_list_json_contract(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14", "--location", "oak ridge", "--count", "3",
             "--notes", "under log")
    proc = cli("list", "--json", store=store)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == 1
    assert payload["count"] == 1
    entry = payload["entries"][0]
    assert set(entry) == {"id", "species", "date", "location", "count", "notes", "created_at"}
    assert entry["species"] == "Chanterelle"
    assert entry["count"] == 3


def test_season_json_contract(store: Path) -> None:
    add_find(store, "Chanterelle", "2023-07-14")
    add_find(store, "Chanterelle", "2024-07-21")
    proc = cli("season", "--json", store=store)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    species_block = payload["species"][0]
    assert species_block["species"] == "Chanterelle"
    assert species_block["total_finds"] == 2
    month = species_block["months"][0]
    assert month["month"] == 7
    assert month["finds"] == 2
    assert month["years"] == [2023, 2024]
    assert month["recurring"] is True


def test_habitat_list_json_contract(store: Path) -> None:
    cli("habitat", "set", "oak ridge", "--terrain", "slope", "--trees", "oak",
        "--notes", "damp gully", store=store)
    proc = cli("habitat", "list", "--json", store=store)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["count"] == 1
    assert payload["habitats"][0]["location"] == "oak ridge"
    assert set(payload["habitats"][0]) == {"location", "terrain", "trees", "notes", "updated_at"}


def test_add_json_returns_the_saved_record(store: Path) -> None:
    proc = cli("add", "--species", "Chanterelle", "--date", "2023-07-14", "--count", "3",
               "--notes", "under log", "--json", store=store)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == 1
    assert payload["entry"]["id"]
    assert payload["entry"]["species"] == "Chanterelle"
    assert document(store)["entries"][0]["id"] == payload["entry"]["id"]


def test_habitat_set_json_reports_create_then_update(store: Path) -> None:
    created = cli("habitat", "set", "oak ridge", "--terrain", "slope", "--json", store=store)
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["created"] is True

    updated = cli("habitat", "set", "oak ridge", "--trees", "oak", "--json", store=store)
    assert updated.returncode == 0, updated.stderr
    payload = json.loads(updated.stdout)
    assert payload["created"] is False
    assert payload["habitat"]["terrain"] == "slope"   # kept from the first call
    assert payload["habitat"]["trees"] == "oak"


# --------------------------------------------------------------------------- #
# Seed + demo + performance
# --------------------------------------------------------------------------- #
def test_seed_fills_every_report_and_is_idempotent(store: Path) -> None:
    first = cli("seed", store=store)
    assert first.returncode == 0, first.stderr
    seeded = document(store)
    assert len(seeded["entries"]) >= 10
    assert len(seeded["habitats"]) >= 3

    second = cli("seed", store=store)
    assert second.returncode == 0, second.stderr
    assert "Nothing to seed" in second.stdout
    assert len(document(store)["entries"]) == len(seeded["entries"])
    assert len(document(store)["habitats"]) == len(seeded["habitats"])

    assert "Chanterelle" in cli("list", store=store).stdout
    season = cli("season", store=store)
    assert "recurring" in season.stdout
    assert "oak ridge" in cli("habitat", "list", store=store).stdout


def test_seed_never_overwrites_an_existing_habitat_note(store: Path) -> None:
    cli("habitat", "set", "oak ridge", "--terrain", "my own reading", store=store)
    cli("seed", store=store)
    notes = {note["location"]: note for note in document(store)["habitats"]}
    assert notes["oak ridge"]["terrain"] == "my own reading"


def test_demo_transcript_runs_end_to_end() -> None:
    proc = subprocess.run(["bash", str(DEMO_SCRIPT)], capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    output = proc.stdout
    assert "log a new find" in output.lower()
    assert "seasonal recurrence" in output
    assert "corrupt store" in output
    assert "All demo steps exited as expected." in output


def test_list_and_season_stay_fast_with_ten_thousand_entries(store: Path) -> None:
    species_names = ["Chanterelle", "Oyster", "Hedgehog", "King Bolete", "Wood Ear"]
    entries = [
        {
            "id": f"{index:012x}",
            "species": species_names[index % len(species_names)],
            "date": f"{2020 + index % 5}-{index % 12 + 1:02d}-{(index % 27) + 1:02d}",
            "location": f"site {index % 20}",
            "count": index % 7 + 1,
            "notes": "sample note",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        for index in range(10_000)
    ]
    store.write_text(json.dumps({"schema_version": 1, "entries": entries, "habitats": []}), encoding="utf-8")

    started = time.perf_counter()
    listed = cli("list", store=store)
    season = cli("season", store=store)
    elapsed = time.perf_counter() - started

    assert listed.returncode == 0, listed.stderr
    assert season.returncode == 0, season.stderr
    assert "10000 finds" in listed.stdout
    assert elapsed < 5.0, f"list + season took {elapsed:.2f}s"
