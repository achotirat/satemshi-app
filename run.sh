#!/bin/bash
# Service entry point. The LaunchDaemon runs this rather than the
# interpreter directly, for two reasons.
#
# The cd is the first. The app reads the .env sitting in its working
# directory, and a clone that ended up one level inside a directory of
# the same name has two of them — so anchor the working directory to
# this script's own location instead of trusting whatever launchd, or a
# shell, happened to hand us. Startup logs the full path it read; that
# line is the thing to check when a setting looks missing.
#
# The second is that .env is deliberately *not* sourced here. The app
# loads it itself. Exporting the same values first would make it report
# every one of them as shadowed by the environment — the single startup
# message that is supposed to mean "something outside the file is
# overriding it", which would then be printed on every healthy boot.
set -euo pipefail
cd "$(dirname "$0")"

# KeepAlive restarts this on failure, so an unusable checkout would
# otherwise spin silently. Say which of the two setup steps is missing.
if [ ! -x .venv/bin/python ]; then
    echo "No .venv in $PWD — run: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
    exit 1
fi
if [ ! -f .env ]; then
    echo "No .env in $PWD — run: cp .env.example .env && chmod 600 .env" >&2
    exit 1
fi

exec .venv/bin/python -m satemshi
