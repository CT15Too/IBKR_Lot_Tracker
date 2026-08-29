#!/usr/bin/env bash
# Assemble the IBKR Lot Tracker AppImage from a PyInstaller build.
#
# Expects the PyInstaller one-folder output at dist/IBKR Lot Tracker and
# appimagetool on PATH (supplied by CI). Produces a self-contained,
# relocatable AppImage that the desktop updater replaces atomically.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIST="$ROOT/dist"
APP_NAME="IBKR Lot Tracker"
BUILD_DIR="$DIST/$APP_NAME"
APPDIR="$ROOT/IBKR_Lot_Tracker.AppDir"

if [[ ! -d "$BUILD_DIR" ]]; then
  echo "error: PyInstaller output not found at $BUILD_DIR" >&2
  exit 1
fi

command -v appimagetool >/dev/null 2>&1 || {
  echo "error: appimagetool not found on PATH" >&2
  exit 1
}

rm -rf "$APPDIR"
mkdir -p "$APPDIR"

# AppRun forwards all arguments — including --smoke-test and --apply-update —
# to the bundled console binary.
cp -a "$BUILD_DIR/." "$APPDIR/usr/"
cat > "$APPDIR/AppRun" <<EOF
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
exec "\$HERE/usr/$APP_NAME" "\$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/$APP_NAME.desktop" <<EOF
[Desktop Entry]
Name=$APP_NAME
Exec=AppRun
Icon=$APP_NAME
Type=Application
Categories=Finance;Utility;
Terminal=false
EOF

cp "$ROOT/assets/icon.png" "$APPDIR/$APP_NAME.png"

ARCH="$(uname -m)"
OUT="$DIST/IBKR-Lot-Tracker-$ARCH.AppImage"
appimagetool "$APPDIR" "$OUT"
echo "Wrote $OUT"
