#!/usr/bin/env bash
# A test shim that pretends to be ffprobe.
#
# Env vars:
#   FAKE_FFPROBE_DURATION - duration in seconds (e.g. "3.5"). Empty = no output, exit 1.
set -u
dur="${FAKE_FFPROBE_DURATION:-}"
if [ -z "$dur" ]; then
    exit 1
fi
printf '%s\n' "$dur"
exit 0
