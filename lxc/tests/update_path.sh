#!/usr/bin/env bash
#
# Checks for the source-update path in lxc/install.sh.
#
#   ./lxc/tests/update_path.sh
#
# Needs only git and bash. Builds a throwaway upstream repository and a
# throwaway "installation" in a temp directory, exercises obtain_source()
# against them, and exits non-zero on the first wrong answer.
#
# This exists because the update path has now broken twice in ways that both
# reported success:
#
#   1. obtain_source() returned early when run from inside $APP_DIR, so the
#      documented update command rebuilt the same commit and said nothing.
#   2. The dirty-tree guard counted the .env that drop_stray_env() deletes
#      itself, so every update after the first refused to run.
#
# Neither was visible without reading the source, and neither would have been
# caught by anything else in the tree.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$HERE/../install.sh"

[ -f "$INSTALLER" ] || { echo "cannot find install.sh next to this script" >&2; exit 2; }

ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

REPO="$ROOT/upstream"
APP="$ROOT/app"

# Identity and hooks, so the checks do not depend on the caller's git config.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com

failures=0
check() {
    local name=$1 ok=$2 detail=${3:-}
    if [ "$ok" = "1" ]; then
        printf 'ok    %s\n' "$name"
    else
        printf 'FAIL  %s%s\n' "$name" "${detail:+  — $detail}"
        failures=$((failures + 1))
    fi
}

# --------------------------------------------------------------------------
# An upstream with two branches, and a tracked .env - which is what the real
# repository has, and the reason the second bug existed at all.
# --------------------------------------------------------------------------
mkdir -p "$REPO/backend" "$REPO/lxc"
git -C "$REPO" init -q -b main
touch "$REPO/docker-compose.yml" "$REPO/backend/keep"
printf 'SECRET=development\n' > "$REPO/.env"
cp "$INSTALLER" "$REPO/lxc/install.sh"
echo one > "$REPO/marker"
git -C "$REPO" add -A
git -C "$REPO" commit -qm one

git -C "$REPO" checkout -q -b feature/thing
echo two > "$REPO/marker"
git -C "$REPO" commit -qam two
SECOND=$(git -C "$REPO" rev-parse --short HEAD)

# An installation one commit behind, shallow, as a real one is: cloned with
# --depth 1 from a branch that has since moved on.
git -C "$REPO" branch -qf behind "$(git -C "$REPO" rev-parse HEAD~1)"
git clone -q --depth 1 --branch behind "file://$REPO" "$APP" 2>/dev/null
git -C "$APP" checkout -q -B feature/thing

check "the installation starts out shallow" \
    "$([ -f "$APP/.git/shallow" ] && echo 1 || echo 0)"
check "the installation starts one commit behind" \
    "$([ "$(cat "$APP/marker")" = one ] && echo 1 || echo 0)"

# --------------------------------------------------------------------------
# obtain_source(), invoked the way a re-run from inside the installation
# invokes it: as a script living at $APP/lxc/, because that is what
# ${BASH_SOURCE[0]} is read from to decide "am I running inside $APP_DIR".
# Sourcing a copy from elsewhere would silently exercise a different branch -
# and did, until this was fixed.
#
# main() is replaced by a call to obtain_source alone, so the checks do not
# need PostgreSQL, Redis, nginx or npm.
# --------------------------------------------------------------------------
run_obtain() {
    local probe="$APP/lxc/probe-obtain-source.sh"
    mkdir -p "$APP/lxc"
    sed 's|^main "\$@"|obtain_source; printf "revision=%s branch=%s\\n" "$SOURCE_REVISION" "$SOURCE_BRANCH"|' \
        "$INSTALLER" > "$probe"

    ( cd "$APP" && env "$@" APP_DIR="$APP" REPO_URL="file://$REPO" bash "$probe" 2>&1 )
    local rc=$?

    # An untracked file would not affect the dirty check, but leaving it lying
    # around in a fixture is untidy and shows up in the reported status.
    rm -f "$probe"
    return "$rc"
}

# --- the strategy this replaced could not have worked ---------------------
git -C "$APP" fetch -q --depth 1 origin \
    "+refs/heads/feature/thing:refs/remotes/origin/feature/thing" 2>/dev/null
git -C "$APP" merge --ff-only origin/feature/thing >/dev/null 2>&1
check "merge --ff-only cannot update a shallow clone" \
    "$([ "$(cat "$APP/marker")" = one ] && echo 1 || echo 0)" \
    "if this fails, the reset in update_checkout may no longer be needed"

# --- the update itself ----------------------------------------------------
out=$(run_obtain)
check "it updates rather than reporting 'already installed'" \
    "$(grep -q "Updated to feature/thing" <<<"$out" && echo 1 || echo 0)" \
    "$(head -2 <<<"$out")"
check "the working tree reaches the new commit" \
    "$([ "$(cat "$APP/marker")" = two ] && echo 1 || echo 0)" \
    "marker=$(cat "$APP/marker")"
check "it stays on the branch the install came from" \
    "$([ "$(git -C "$APP" rev-parse --abbrev-ref HEAD)" = feature/thing ] && echo 1 || echo 0)" \
    "$(git -C "$APP" rev-parse --abbrev-ref HEAD)"
check "it records the revision for the summary line" \
    "$(grep -q "revision=$SECOND branch=feature/thing" <<<"$out" && echo 1 || echo 0)" \
    "$(grep revision= <<<"$out")"

out=$(run_obtain)
check "a second run with nothing new says so" \
    "$(grep -q "Already at the latest" <<<"$out" && echo 1 || echo 0)" \
    "$(head -2 <<<"$out")"

# --- the installer's own housekeeping must not block the next update ------
check "drop_stray_env removed the tracked .env" \
    "$([ ! -f "$APP/.env" ] && echo 1 || echo 0)"
check "which leaves the checkout dirty as far as plain git is concerned" \
    "$(git -C "$APP" diff --quiet HEAD && echo 0 || echo 1)"

echo three > "$REPO/marker"
git -C "$REPO" commit -qam three
THIRD=$(git -C "$REPO" rev-parse --short HEAD)

out=$(run_obtain)
check "the update still runs despite that deletion" \
    "$([ "$(git -C "$APP" rev-parse --short HEAD)" = "$THIRD" ] && echo 1 || echo 0)" \
    "at $(git -C "$APP" rev-parse --short HEAD), wanted $THIRD"

# --- but a real edit is left alone, and said out loud --------------------
echo "edited by hand" > "$APP/marker"
echo four > "$REPO/marker"
git -C "$REPO" commit -qam four

out=$(run_obtain)
check "an edit to a tracked file blocks the update" \
    "$([ "$(cat "$APP/marker")" = "edited by hand" ] && echo 1 || echo 0)"
check "and the output says the source was not updated" \
    "$(grep -q "was NOT updated" <<<"$out" && echo 1 || echo 0)" \
    "$(head -2 <<<"$out")"
check "and names the file responsible" \
    "$(grep -qE "M +marker" <<<"$out" && echo 1 || echo 0)" \
    "$(grep -A3 "was NOT updated" <<<"$out" | tail -2)"

git -C "$APP" checkout -q -- marker

# --- an explicit BRANCH moves the install deliberately -------------------
out=$(run_obtain BRANCH=main)
check "an explicit BRANCH is honoured" \
    "$([ "$(git -C "$APP" rev-parse --abbrev-ref HEAD)" = main ] && echo 1 || echo 0)" \
    "$(git -C "$APP" rev-parse --abbrev-ref HEAD)"
check "and that branch's content is what lands" \
    "$([ "$(cat "$APP/marker")" = one ] && echo 1 || echo 0)" \
    "marker=$(cat "$APP/marker")"

# --------------------------------------------------------------------------
printf '\n'
if [ "$failures" -eq 0 ]; then
    echo "all checks passed"
else
    echo "$failures check(s) failed"
fi
exit "$((failures > 0))"
