#!/usr/bin/env bash
# Test shim for yt-dlp.
# Env:
#   FAKE_YTDLP_JSON_FILE=path   required for dump mode
#   FAKE_YTDLP_EXIT=0
#   FAKE_YTDLP_STDERR=""
#   FAKE_YTDLP_TICKS=3
#   FAKE_YTDLP_SLEEP=0.05
set -u

exit_code="${FAKE_YTDLP_EXIT:-0}"
stderr_text="${FAKE_YTDLP_STDERR:-}"

is_dump=0
output_path=""
prev=""
for arg in "$@"; do
  if [ "$arg" = "-J" ] || [ "$arg" = "--dump-single-json" ] || [ "$arg" = "--dump-json" ]; then
    is_dump=1
  fi
  if [ "$prev" = "-o" ]; then
    output_path="$arg"
  fi
  prev="$arg"
done

if [ "$is_dump" = "1" ]; then
  if [ "$exit_code" != "0" ]; then
    printf '%s' "$stderr_text" >&2
    exit "$exit_code"
  fi
  if [ -z "${FAKE_YTDLP_JSON_FILE:-}" ] || [ ! -f "$FAKE_YTDLP_JSON_FILE" ]; then
    printf 'FAKE_YTDLP_JSON_FILE missing\n' >&2
    exit 2
  fi
  cat "$FAKE_YTDLP_JSON_FILE"
  exit 0
fi

# download mode
ticks="${FAKE_YTDLP_TICKS:-3}"
sleep_s="${FAKE_YTDLP_SLEEP:-0.05}"
for i in $(seq 1 "$ticks"); do
  pct=$(awk "BEGIN { printf \"%.1f\", ($i / $ticks) * 100 }")
  printf 'PROGRESS percent=%s speed=1.0MiB/s\n' "$pct"
  sleep "$sleep_s"
done

if [ "$exit_code" = "0" ]; then
  if [ -n "$output_path" ]; then
    : > "$output_path"
  fi
else
  printf '%s' "$stderr_text" >&2
fi
exit "$exit_code"
