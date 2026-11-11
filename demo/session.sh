#!/usr/bin/env bash
# SporeLog demo transcript: exercises every command against a throwaway store so
# the repo's own ./sporelog.json is never touched. Non-interactive, no network.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

PYTHON=${PYTHON:-python3}
WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/sporelog-demo.XXXXXX")
trap 'rm -rf "$WORKDIR"' EXIT

STORE="$WORKDIR/field-log.json"
BROKEN="$WORKDIR/broken-store.json"
FAILURES=0

step() { printf '\n== %s\n' "$*"; }

quote() {
  case $1 in
    *[!A-Za-z0-9_./:=@-]*) printf "'%s'" "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

run() {
  printf '\n$ %s sporelog.py --store "$STORE"' "$PYTHON"
  for argument in "$@"; do
    printf ' '
    quote "$argument"
  done
  printf '\n'
  "$PYTHON" sporelog.py --store "$STORE" "$@"
  local status=$?
  if [ "$status" -ne 0 ]; then
    FAILURES=$((FAILURES + 1))
    printf '!! unexpected exit %d\n' "$status"
  fi
}

expect_one_line_failure() {
  printf '\n$ %s sporelog.py --store "$BROKEN"' "$PYTHON"
  for argument in "$@"; do
    printf ' '
    quote "$argument"
  done
  printf ' 2>&1\n'
  local output status
  output=$("$PYTHON" sporelog.py --store "$BROKEN" "$@" 2>&1)
  status=$?
  printf '%s\n' "$output"
  local lines
  lines=$(printf '%s\n' "$output" | wc -l | tr -d ' ')
  if [ "$status" -eq 0 ]; then
    FAILURES=$((FAILURES + 1))
    printf '!! expected a non-zero exit\n'
  fi
  if [ "$lines" -ne 1 ]; then
    FAILURES=$((FAILURES + 1))
    printf '!! expected exactly one error line, got %s\n' "$lines"
  fi
}

printf 'SporeLog demo - foraging log, habitat notes, seasonal recurrence\n'
printf 'Commands run against a throwaway store: %s\n' "$STORE"

step '1. Load a small realistic sample log (idempotent seed)'
run seed

step '2. Log a new find with a note'
run add --species "Winter Chanterelle" --date 2025-11-02 --location "moss bank" \
  --count 4 --notes "under hemlock, frost on the caps"

step '3. List every find (newest first)'
run list

step '4. Narrow the list by species'
run list --species Chanterelle

step '5. Narrow the list by month'
run list --month 10

step '6. Keep a habitat note for a location'
run habitat set "moss bank" --terrain "steep north-facing bank" \
  --trees "hemlock, moss" --notes "deer trail mid-slope; damp all year"

step '7. List habitat notes'
run habitat list

step '8. Seasonal recurrence for every species'
run season

step '9. Seasonal recurrence for one species'
run season --species Oyster

step '10. Machine-readable output for scripting'
printf '\n$ %s sporelog.py --store "$STORE" season --species Oyster --json\n' "$PYTHON"
"$PYTHON" sporelog.py --store "$STORE" season --species Oyster --json
if [ $? -ne 0 ]; then
  FAILURES=$((FAILURES + 1))
fi

step '11. A corrupt store fails with exactly one line and exit 1'
printf 'this is not json\n' >"$BROKEN"
expect_one_line_failure season
expect_one_line_failure list

step '12. A bad argument fails the same way, leaving the store untouched'
expect_one_line_failure list --month 13

printf '\nDemo complete. The throwaway store is deleted on exit.\n'
printf 'Your own log lives at ./sporelog.json by default, e.g.\n'
printf '  %s sporelog.py add --species Chanterelle --date 2025-07-14 --location "oak ridge" --notes "under log"\n' "$PYTHON"

if [ "$FAILURES" -ne 0 ]; then
  printf '\n%s demo step(s) did not behave as expected\n' "$FAILURES" >&2
  exit 1
fi
printf 'All demo steps exited as expected.\n'
