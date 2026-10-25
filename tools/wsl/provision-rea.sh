#!/usr/bin/env bash
# Provision REA (morluto/rea, npm "rea-agents") + Ghidra 12.1.4 + JDK 21 inside WSL2 (run as root:
#   wsl -d <distro> -u root -- bash /mnt/c/.../tools/wsl/provision-rea.sh
# ). Idempotent. REA's Ghidra provider on native Windows only admits PE/6502 targets, so ARM code from
# DS ROMs (ELF objects from `dsd delink`, raw module binaries) has to be analysed from Linux.
# REA is installed with `npm ci` from tools/wsl/rea/package-lock.json (exact versions + integrity hashes)
# into REA_DIR and linked into REA_BIN_DIR; REA_LOCK_DIR points at that folder (factory passes it).
set -euo pipefail

GHIDRA_VER="${GHIDRA_VER:-12.1.4}"
GHIDRA_DATE="${GHIDRA_DATE:-20260921}"
REA_LOCK_DIR="${REA_LOCK_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/rea}"
REA_DIR="${REA_DIR:-/opt/rea-agents}"
REA_BIN_DIR="${REA_BIN_DIR:-/usr/local/bin}"
export DEBIAN_FRONTEND=noninteractive

echo "[rea] pacotes base (JDK 21, unzip, procps, curl)…"
apt-get update -qq
if ! apt-get install -y -qq openjdk-21-jdk-headless unzip procps curl ca-certificates >/dev/null; then
  apt-get install -y -qq openjdk-25-jdk-headless unzip procps curl ca-certificates >/dev/null
fi
command -v npm >/dev/null || apt-get install -y -qq npm >/dev/null

JAVA_HOME_DIR="$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")"
echo "[rea] JAVA_HOME=${JAVA_HOME_DIR}"

GHIDRA_DIR="/opt/ghidra_${GHIDRA_VER}_PUBLIC"
if [ ! -x "${GHIDRA_DIR}/support/analyzeHeadless" ]; then
  echo "[rea] baixando Ghidra ${GHIDRA_VER}…"
  curl -fsSL -o /tmp/ghidra.zip \
    "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VER}_build/ghidra_${GHIDRA_VER}_PUBLIC_${GHIDRA_DATE}.zip"
  unzip -q -o /tmp/ghidra.zip -d /opt
  rm -f /tmp/ghidra.zip
fi
ln -sfn "${GHIDRA_DIR}" /opt/ghidra

echo "[rea] instalando rea-agents com npm ci (${REA_LOCK_DIR}/package-lock.json → ${REA_DIR})…"
mkdir -p "${REA_DIR}" "${REA_BIN_DIR}"
cp "${REA_LOCK_DIR}/package.json" "${REA_LOCK_DIR}/package-lock.json" "${REA_DIR}/"
(cd "${REA_DIR}" && npm ci --omit=dev --no-audit --no-fund >/dev/null)
ln -sfn "${REA_DIR}/node_modules/.bin/rea" "${REA_BIN_DIR}/rea"

cat > /etc/profile.d/ds-mods-rea.sh <<EOF
export GHIDRA_INSTALL_DIR=/opt/ghidra
export JAVA_HOME=${JAVA_HOME_DIR}
export REA_ANALYSIS_PROVIDER=ghidra
export REA_MCP_INPUT_SCHEMA_PROFILE=compact
export REA_GHIDRA_STARTUP_TIMEOUT_MS=\${REA_GHIDRA_STARTUP_TIMEOUT_MS:-600000}
EOF
chmod 644 /etc/profile.d/ds-mods-rea.sh

echo "[rea] versões:"
node -v
"${REA_BIN_DIR}/rea" --version || true
"${GHIDRA_DIR}/support/analyzeHeadless" -help >/dev/null 2>&1 && echo "ghidra ${GHIDRA_VER} ok"
echo "[rea] pronto"
