#!/usr/bin/env bash
# A test shim that pretends to be ffmpeg.
#
# Reads behavior from env vars set by the test:
#   FAKE_FFMPEG_TICKS  - number of "out_time_us" progress lines to emit (default 3)
#   FAKE_FFMPEG_SLEEP  - seconds to sleep between ticks (default 0.05)
#   FAKE_FFMPEG_EXIT   - exit code (default 0)
#   FAKE_FFMPEG_STDERR - text to write to stderr before exiting (default "")
#
# It parses argv for "-y <output_path>" (the last positional) and creates an empty
# file there on success, simulating a real ffmpeg run.
set -u

ticks="${FAKE_FFMPEG_TICKS:-3}"
sleep_s="${FAKE_FFMPEG_SLEEP:-0.05}"
exit_code="${FAKE_FFMPEG_EXIT:-0}"
stderr_text="${FAKE_FFMPEG_STDERR:-}"

# The last argv element is the output path (build_command places it last after -y).
output_path="${!#}"

# Emit progress lines on stdout.
for i in $(seq 1 "$ticks"); do
    out_time_us=$(( i * 1000000 ))
    printf 'out_time_us=%s\n' "$out_time_us"
    printf 'speed=1.0x\n'
    printf 'progress=continue\n'
    sleep "$sleep_s"
done
printf 'progress=end\n'

if [ "$exit_code" = "0" ]; then
    : > "$output_path"  # touch the output file
else
    printf '%s' "$stderr_text" >&2
fi

exit "$exit_code"
