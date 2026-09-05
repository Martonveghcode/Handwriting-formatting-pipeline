#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  if [[ "${ID:-}" != ubuntu || "${VERSION_ID:-}" != 24.04 ]]; then
    echo "Warning: this installer is validated on Ubuntu 24.04; found ${PRETTY_NAME:-unknown}." >&2
  fi
fi

SUDO=()
if [[ $(id -u) -ne 0 ]]; then
  SUDO=(sudo)
fi

"${SUDO[@]}" apt-get update
"${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential software-properties-common pkg-config git xvfb xauth \
  python3 python3-dev python3-venv python3-setuptools \
  python3-numpy python3-scipy python3-pil python3-cairo python3-gi python3-gi-cairo \
  libcairo2-dev libgirepository1.0-dev gir1.2-gtk-3.0

if ! command -v python3.9 >/dev/null 2>&1; then
  "${SUDO[@]}" add-apt-repository -y ppa:deadsnakes/ppa
  "${SUDO[@]}" apt-get update
fi
"${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3.9 python3.9-dev python3.9-venv

python3.9 -m venv "$REPO_DIR/.venv-let"
"$REPO_DIR/.venv-let/bin/python" -m pip install --upgrade 'pip<26' 'setuptools==69.5.1' wheel
"$REPO_DIR/.venv-let/bin/python" -m pip install -r "$REPO_DIR/requirements-let.txt"

(
  cd "$REPO_DIR/handwriting/hst"
  python3 -c 'import composite, costs, generate; from glyph_db import GlyphDB; from chunk_db import ChunkDB; print("HST native modules built")'
)

(
  cd "$REPO_DIR"
  SETUPTOOLS_USE_DISTUTILS=stdlib "$REPO_DIR/.venv-let/bin/python" -c \
    'import handwriting.let.threshold, handwriting.let.skeleton, handwriting.let.infer_alpha; print("LET runtime ready")'
)

echo "Installation complete. Run scripts/verify_modern.sh to verify both applications."
