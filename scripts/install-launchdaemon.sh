#!/usr/bin/env bash
# Install (or reinstall) the capture server as a LaunchDaemon.
#
#   sudo scripts/install-launchdaemon.sh          # install and start
#   scripts/install-launchdaemon.sh --print       # show the plist, do nothing
#
# Re-running is the supported way to pick up an edit to run.sh or to the
# template — it boots the old job out first, so there is no separate
# reload procedure to remember.

set -euo pipefail

LABEL="com.satemshi.capture"
PLIST="/Library/LaunchDaemons/${LABEL}.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="${REPO}/deploy/${LABEL}.plist.template"

fail() { echo "error: $*" >&2; exit 1; }

[ -f "$TEMPLATE" ] || fail "missing template at $TEMPLATE"

# launchd needs a literal absolute path — no ~, no variables, and
# nothing that would confuse the substitution below.
case "$REPO" in
    /*) ;;
     *) fail "repo path is not absolute: $REPO" ;;
esac
case "$REPO" in
    *"|"*) fail "repo path contains a '|', which this script cannot substitute" ;;
esac

render() {
    local user="$1"
    sed -e "s|__REPO__|${REPO}|g" -e "s|__USER__|${user}|g" "$TEMPLATE"
}

if [ "${1:-}" = "--print" ]; then
    render "${SUDO_USER:-${USER:-<you>}}"
    exit 0
fi

[ "$(uname -s)" = "Darwin" ] || fail "this installs a LaunchDaemon; on Linux write a systemd unit instead"
[ "$(id -u)" -eq 0 ] || fail "run with sudo — $PLIST is root-owned"

# The daemon must run as the human who owns the vault, not as root, or
# every file it writes lands with root ownership. sudo tells us who that
# is; a root login shell does not.
OWNER="${SUDO_USER:-}"
[ -n "$OWNER" ] && [ "$OWNER" != "root" ] \
    || fail "could not tell which user to run as — invoke this as 'sudo scripts/install-launchdaemon.sh' from your own account"
id "$OWNER" >/dev/null 2>&1 || fail "no such user: $OWNER"

# Fail here, with a sentence, rather than in a KeepAlive restart loop
# whose only trace is a line in the .err log.
[ -x "${REPO}/run.sh" ] || fail "${REPO}/run.sh is missing or not executable — run: chmod +x run.sh"
[ -x "${REPO}/.venv/bin/python" ] || fail "no .venv in ${REPO} — run: python3 -m venv .venv && .venv/bin/pip install -e ."
[ -f "${REPO}/.env" ] || fail "no .env in ${REPO} — run: cp .env.example .env && chmod 600 .env"

# launchd creates the log files but not their directory, and it owns
# them as root unless they already belong to the user.
install -d -o "$OWNER" -m 755 "${REPO}/logs"

render "$OWNER" > "$PLIST"
chown root:wheel "$PLIST"
chmod 644 "$PLIST"

# Not loaded yet on a first install; that is not an error.
launchctl bootout "system/${LABEL}" 2>/dev/null || true
launchctl bootstrap system "$PLIST"

echo "Installed $PLIST, running as $OWNER out of $REPO"
echo
echo "  launchctl print system/${LABEL}   # state = running"
echo "  tail -f ${REPO}/logs/satemshi.err # the app logs here"
