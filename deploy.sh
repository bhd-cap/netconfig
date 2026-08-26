#!/usr/bin/env bash
#
# Deprecated: superseded by install.sh
#
# This script built the images, slept a fixed 60 seconds, then ran the
# migrations and printed a hardcoded password that no longer matches how the
# admin user is created. install.sh does the same job, generates real secrets,
# and waits for the API to actually report healthy instead of guessing.
#
# Kept as an entry point so existing muscle memory and documentation keep
# working.

set -Eeuo pipefail

cd "$(dirname "$0")"

printf 'deploy.sh is deprecated; running install.sh instead.\n\n'

exec ./install.sh "$@"
