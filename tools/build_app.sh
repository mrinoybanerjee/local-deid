#!/bin/zsh
# Build the thin menu-bar app bundle (docs/PHASE2_PLAN.md section 5):
#   ~/Applications/Local DeID.app  ->  <checkout>/.venv/bin/python -m localdeid.app
#
#   tools/build_app.sh                  # bundle for this checkout
#   tools/build_app.sh --out DIR        # put the .app in DIR instead of ~/Applications
#
# The bundle holds no code: it runs the checkout's virtual environment, so
# `git pull` and `uv sync` update the app. It is signed with the "LocalDeID Dev"
# certificate if the Keychain has one, else ad hoc. Hotkeys use Carbon, so the
# app needs no Accessibility grant.
set -euo pipefail
checkout=${0:A:h:h}
out=~/Applications
while (( $# )); do
  case $1 in
    --out) out=$2; shift 2 ;;
    *) echo "usage: tools/build_app.sh [--out DIR]" >&2; exit 2 ;;
  esac
done
[[ -x $checkout/.venv/bin/python ]] || { echo "No virtual environment in $checkout: run uv sync --extra mlx --extra app" >&2; exit 1; }
$checkout/.venv/bin/python -c "import localdeid.app.api, webview, cryptography" \
  || { echo "The app extra is missing: run uv sync --extra mlx --extra app" >&2; exit 1; }

version=$(sed -n 's/^version = "\(.*\)"/\1/p' $checkout/pyproject.toml | head -1)
app="$out/Local DeID.app"
tmp=$(mktemp -d)
mkdir -p "$tmp/Local DeID.app/Contents/MacOS"

cat > "$tmp/Local DeID.app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.mrinoy.localdeid</string>
  <key>CFBundleName</key><string>Local DeID</string>
  <key>CFBundleDisplayName</key><string>Local DeID</string>
  <key>CFBundleExecutable</key><string>LocalDeID</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# The launcher. It starts the app in the background and exits: on macOS 26 a menu bar
# item never appears when the launcher exec()s Python in its own place (DECISIONS 28).
# Its log keeps only the last run and never holds note text: the app logs error codes
# and exception names only.
cat > "$tmp/Local DeID.app/Contents/MacOS/LocalDeID" <<LAUNCH
#!/bin/zsh
checkout=${(q)checkout}
logs=~/Library/Logs/LocalDeID
mkdir -p \$logs && chmod 700 \$logs
if [[ ! -x \$checkout/.venv/bin/python ]]; then
  osascript -e 'display alert "Local DeID cannot start" message "The app files are missing. Ask Mrinoy to reinstall it."'
  exit 1
fi
export LOCALDEID_MODELS=\$checkout/models
\$checkout/.venv/bin/python -m localdeid.app > \$logs/last-run.log 2>&1 < /dev/null &!
exit 0
LAUNCH
chmod 755 "$tmp/Local DeID.app/Contents/MacOS/LocalDeID"

identity=-
if security find-identity -v -p codesigning 2>/dev/null | grep -q '"LocalDeID Dev"'; then identity="LocalDeID Dev"; fi
codesign --force --sign "$identity" "$tmp/Local DeID.app"
codesign --verify "$tmp/Local DeID.app"

mkdir -p "$out"
if [[ -d $app ]]; then rm -rf "$app"; fi
mv "$tmp/Local DeID.app" "$app"
rmdir "$tmp"
echo "Built $app (version $version, signed: ${identity/-/ad hoc}) -> $checkout"
