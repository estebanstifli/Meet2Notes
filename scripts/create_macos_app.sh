#!/usr/bin/env bash
# Build a double-clickable Meet2Notes.app bundle for macOS.
#
# The bundle is a thin launcher around the source install: it resolves this
# checkout, then runs .venv/bin/meet2notes so the browser opens on
# http://127.0.0.1:8765. It uses only tools that ship with macOS
# (sips, iconutil), so no extra dependencies are downloaded.
#
# Usage:
#   ./scripts/create_macos_app.sh              # build Meet2Notes.app in the repo root
#   ./scripts/create_macos_app.sh --install    # also copy it to /Applications
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Meet2Notes"
SOURCE_PNG="${ROOT}/assets/macos/Meet2Notes-1024.png"
APP="${ROOT}/${APP_NAME}.app"
INSTALL_TO_APPLICATIONS="false"

usage() {
  sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for argument in "$@"; do
  case "${argument}" in
    --install) INSTALL_TO_APPLICATIONS="true" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: ${argument}" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "create_macos_app.sh only builds macOS bundles; detected '$(uname -s)'." >&2
  exit 1
fi
if [[ ! -f "${SOURCE_PNG}" ]]; then
  echo "Missing icon source: ${SOURCE_PNG}" >&2
  exit 1
fi
for tool in sips iconutil; do
  command -v "${tool}" >/dev/null 2>&1 || {
    echo "'${tool}' was not found. Install the Xcode command line tools: xcode-select --install" >&2
    exit 1
  }
done
if [[ ! -x "${ROOT}/.venv/bin/meet2notes" ]]; then
  echo "No installation found at ${ROOT}/.venv. Run ./install.sh first." >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

# 1. icon: 1024 px source -> .icns (macOS wants every size explicitly)
ICONSET="${STAGE}/${APP_NAME}.iconset"
mkdir -p "${ICONSET}"
for size in 16 32 128 256 512; do
  sips -z "${size}" "${size}" "${SOURCE_PNG}" --out "${ICONSET}/icon_${size}x${size}.png" >/dev/null
  sips -z "$((size * 2))" "$((size * 2))" "${SOURCE_PNG}" \
    --out "${ICONSET}/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "${ICONSET}" -o "${STAGE}/AppIcon.icns"

# 2. bundle layout
rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS" "${APP}/Contents/Resources"
cp "${STAGE}/AppIcon.icns" "${APP}/Contents/Resources/AppIcon.icns"

cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>${APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>${APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>local.meet2notes.launcher</string>
  <key>CFBundleExecutable</key><string>${APP_NAME}</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
</dict>
</plist>
PLIST

# 3. launcher: Finder starts processes with a minimal PATH, so set one and
#    keep a log for launches that have no terminal attached.
cat > "${APP}/Contents/MacOS/${APP_NAME}" <<'LAUNCHER'
#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
REPO="__REPO_ROOT__"
if [[ ! -x "${REPO}/.venv/bin/meet2notes" ]]; then
  osascript -e 'display alert "Meet2Notes is not installed" message "Run ./install.sh inside the Meet2Notes folder, then open this app again."' >/dev/null 2>&1 || true
  echo "Missing ${REPO}/.venv/bin/meet2notes" >&2
  exit 1
fi
cd "${REPO}"
LOG="${HOME}/Library/Logs/Meet2Notes.log"
if [[ -t 1 ]]; then
  exec "${REPO}/.venv/bin/meet2notes" "$@"
fi
mkdir -p "$(dirname "${LOG}")"
exec "${REPO}/.venv/bin/meet2notes" "$@" >>"${LOG}" 2>&1
LAUNCHER
sed -i '' "s|__REPO_ROOT__|${ROOT}|" "${APP}/Contents/MacOS/${APP_NAME}"
chmod +x "${APP}/Contents/MacOS/${APP_NAME}"

# locally built bundles are unsigned; clear any quarantine bit so Gatekeeper
# does not block the first launch
xattr -dr com.apple.quarantine "${APP}" 2>/dev/null || true

if [[ "${INSTALL_TO_APPLICATIONS}" == "true" ]]; then
  rm -rf "/Applications/${APP_NAME}.app"
  cp -R "${APP}" "/Applications/${APP_NAME}.app"
  echo "Installed /Applications/${APP_NAME}.app"
fi

echo "Built ${APP}"
echo "Open it with: open '${APP}'   (or drag it to /Applications in Finder)"
