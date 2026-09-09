#!/bin/bash
# Build Project Signal macOS PKG for Intune (unmanaged pkg pilot).
#
# Architecture:
#   /Applications/Project Signal.app/.../signal-agent  (Included Apps detection)
#   /usr/local/bin/signal-agent -> app binary          (CLI entry point)
#
# No package scripts. CLI symlink is delivered in the payload.
# BundleIsRelocatable=false so the app always lands in /Applications.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${SIGNAL_AGENT_BIN:-}" ]; then
  SRC_BIN="$SIGNAL_AGENT_BIN"
elif [ -x "$ROOT/signal-agent" ]; then
  SRC_BIN="$ROOT/signal-agent"
elif [ -x "$ROOT/../signal-agent" ]; then
  SRC_BIN="$ROOT/../signal-agent"
else
  SRC_BIN="$ROOT/signal-agent"
fi
OUT_PKG="${OUT_PKG:-$ROOT/ProjectSignal.pkg}"
VERSION="${VERSION:-}"
if [ -z "$VERSION" ] && [ -f "$ROOT/../../../VERSION" ]; then
  VERSION="$(tr -d '[:space:]' < "$ROOT/../../../VERSION")"
fi
VERSION="${VERSION:-1.0.0}"
IDENTIFIER="com.projectsignal.agent"

if [ ! -x "$SRC_BIN" ]; then
    echo "error: signal-agent binary not found/executable: $SRC_BIN" >&2
    exit 1
fi

WORKDIR=$(mktemp -d /tmp/projectsignal-pkg.XXXXXX)
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

PAYLOAD="$WORKDIR/payload"
mkdir -p "$PAYLOAD/Applications/Project Signal.app/Contents/MacOS" \
         "$PAYLOAD/usr/local/bin"

cat > "$PAYLOAD/Applications/Project Signal.app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDevelopmentRegion</key>
	<string>en</string>
	<key>CFBundleExecutable</key>
	<string>signal-agent</string>
	<key>CFBundleIdentifier</key>
	<string>${IDENTIFIER}</string>
	<key>CFBundleName</key>
	<string>Project Signal</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>${VERSION}</string>
	<key>CFBundleVersion</key>
	<string>${VERSION}</string>
	<key>LSMinimumSystemVersion</key>
	<string>14.0</string>
</dict>
</plist>
EOF

# Preserve binary bytes aside from packaging; strip quarantine/xattrs/resource forks.
ditto --norsrc --noextattr --noqtn "$SRC_BIN" \
  "$PAYLOAD/Applications/Project Signal.app/Contents/MacOS/signal-agent"
chmod 755 "$PAYLOAD/Applications/Project Signal.app/Contents/MacOS/signal-agent"

ln -sfn "/Applications/Project Signal.app/Contents/MacOS/signal-agent" \
  "$PAYLOAD/usr/local/bin/signal-agent"

# Ad-hoc sign app bundle (pilot). Production signing/notarization is a later phase.
codesign --force --deep --sign - "$PAYLOAD/Applications/Project Signal.app"

COMPONENT_PLIST="$WORKDIR/component.plist"
pkgbuild --analyze --root "$PAYLOAD" "$COMPONENT_PLIST"
/usr/libexec/PlistBuddy -c 'Set :0:BundleIsRelocatable false' "$COMPONENT_PLIST"

pkgbuild \
  --root "$PAYLOAD" \
  --component-plist "$COMPONENT_PLIST" \
  --identifier "$IDENTIFIER" \
  --version "$VERSION" \
  --install-location / \
  "$OUT_PKG"

echo "Built: $OUT_PKG"
pkgutil --payload-files "$OUT_PKG" || true
