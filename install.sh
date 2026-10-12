#!/bin/bash
# Install or update Local DeID on an Apple Silicon Mac. Paste this one line into Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/mrinoybanerjee/local-deid/main/install.sh | bash
#
# Run the same line again to update. Add `-s -- --measure` after `bash` to repeat the
# speed and memory check.
#
# What it does, in order:
#   1. Checks the Mac (Apple Silicon, free disk) and stops if Local DeID is running.
#   2. Installs uv (pinned version) in ~/.local/bin, if missing.
#   3. Downloads the latest code (one fixed commit) into ~/Library/LocalDeID, installs
#      the locked Python packages, downloads the two shipping model files, and checks
#      their SHA-256 hashes.
#   4. Runs the app self-test. On the first install, it also runs the SPEC 3 speed and
#      memory check (about 7 minutes; it opens the window and uses the clipboard).
#   5. Builds ~/Applications/Local DeID.app, starts it at login, and opens it.
#
# No git, Xcode tools, or GitHub account are needed. The network is used only here
# (GitHub, PyPI, astral.sh for uv, Hugging Face and openaipublic for the models); the app
# itself makes no network connections. Updates keep the caseload, sessions, and settings.
#
# Test overrides: LOCALDEID_CHECKOUT (code folder), LOCALDEID_APPS (where the .app goes).

main() {
  set -euo pipefail

  local repo=mrinoybanerjee/local-deid
  local checkout=${LOCALDEID_CHECKOUT:-$HOME/Library/LocalDeID}
  local apps=${LOCALDEID_APPS:-$HOME/Applications}
  local uv_version=0.9.27
  local measure=0

  while [ $# -gt 0 ]; do
    case $1 in
      --measure) measure=1; shift ;;
      *) echo "usage: install.sh [--measure]" >&2; exit 2 ;;
    esac
  done

  step "Checking this Mac"
  [ "$(uname -m)" = arm64 ] || die "Local DeID needs an Apple Silicon Mac (M1 or newer)."
  local free_gb
  free_gb=$(df -g "$HOME" | awk 'NR==2 {print $4}')
  [ "$free_gb" -ge 5 ] || die "Free at least 5 GB of disk space first (now ${free_gb} GB free)."
  if pgrep -f "python -m localdeid[.]app" >/dev/null; then
    die "Quit Local DeID first (menu bar: DeID > Quit Local DeID), then run this again."
  fi

  local uv
  uv=$(command -v uv || true)
  if [ -z "$uv" ] && [ -x "$HOME/.local/bin/uv" ]; then uv=$HOME/.local/bin/uv; fi
  if [ -z "$uv" ]; then
    step "Installing uv $uv_version (Python package manager)"
    curl -fsSL "https://astral.sh/uv/$uv_version/install.sh" | env UV_NO_MODIFY_PATH=1 sh
    uv=$HOME/.local/bin/uv
  fi

  step "Downloading the code"
  local sha
  sha=$(curl -fsSL -H "Accept: application/vnd.github.sha" "https://api.github.com/repos/$repo/commits/main")
  [[ $sha =~ ^[0-9a-f]{40}$ ]] || die "Could not read the latest version from GitHub. Check the internet connection."
  # Run the check until it has produced a result once (a failed first install retries it).
  [ -f "$checkout/harness/out/app_memory_air.json" ] || measure=1
  tmp=$(mktemp -d)  # global: the EXIT trap runs after main returns
  trap 'rm -rf "${tmp:-}"' EXIT
  curl -fsSL "https://codeload.github.com/$repo/tar.gz/$sha" | tar -xz -C "$tmp" --strip-components 1
  mkdir -p "$checkout"
  # Replace the code; keep the Python environment, the model files, and measurement output.
  rsync -a --delete --exclude /.venv/ --exclude '/models/*/' --exclude /harness/out/ \
    --exclude /.installed-commit "$tmp/" "$checkout/"
  echo "$sha" > "$checkout/.installed-commit"
  echo "Version ${sha:0:7} in $checkout"
  cd "$checkout"

  step "Installing the Python packages (locked versions)"
  UV_PYTHON_PREFERENCE=only-managed "$uv" sync --locked --no-dev --extra mlx --extra app

  step "Downloading and checking the model files (about 1.5 GB the first time)"
  .venv/bin/python tools/fetch_models.py --only pf-mlx-8bit o200k-bpe

  step "Running the app self-test"
  mkdir -p harness/out
  if ! .venv/bin/python -m localdeid.app --selftest-exit > harness/out/selftest_air.json; then
    cat harness/out/selftest_air.json
    die "The self-test failed. Send the text above to Mrinoy."
  fi
  echo "Self-test passed."

  if [ $measure = 1 ]; then
    step "Measuring speed and model memory (about 5 minutes)"
    .venv/bin/python harness/memspeed.py speed --only mlx_8bit:w8192:clear --tag air_pf
    step "Measuring whole-app memory with the window open (about 2 minutes; it uses the clipboard)"
    harness/app_memory.sh air >/dev/null || true
    .venv/bin/python harness/summary.py harness/out/speed_air_pf.json harness/out/app_memory_air.json || true
    echo "The full results are in $checkout/harness/out/."
  fi

  step "Building the app"
  tools/build_app.sh --out "$apps"
  local app="$apps/Local DeID.app"

  # Start at login with a LaunchAgent (no permission prompt). macOS lists it in
  # System Settings > General > Login Items, where it can be turned off.
  local agent=$HOME/Library/LaunchAgents/com.mrinoy.localdeid.launcher.plist
  mkdir -p "$(dirname "$agent")"
  cat > "$agent" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.mrinoy.localdeid.launcher</string>
  <key>ProgramArguments</key><array><string>/usr/bin/open</string><string>$app</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
PLIST
  plutil -lint -s "$agent"

  open "$app"
  step "Done"
  echo "Local DeID is running: look for \"DeID\" in the menu bar."
  echo "Copy a note and press Control-Option-D. Daily use: $checkout/docs/INSTALL.md"
  echo "If macOS asks whether Python may use the Keychain, click Always Allow."
}

step() { printf '\n==> %s\n' "$1"; }
die() { printf '\nError: %s\n' "$1" >&2; exit 1; }

# Everything runs from main, so bash has read the whole script before any command runs
# (it arrives through a pipe). Its stdin is closed so no command can read the script.
main "$@" < /dev/null
