#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
export SETUPTOOLS_USE_DISTUTILS=stdlib
export GDK_BACKEND=x11
exec "$REPO_DIR/.venv-let/bin/python" -m handwriting.let.main
