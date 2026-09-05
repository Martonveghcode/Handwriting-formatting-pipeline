#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"

python3 -m compileall -q \
  "$REPO_DIR/handwriting/hst" "$REPO_DIR/handwriting/line_graph" \
  "$REPO_DIR/ddp" "$REPO_DIR/gbp" "$REPO_DIR/graph_cuts" \
  "$REPO_DIR/frf" "$REPO_DIR/ply2" "$REPO_DIR/utils"

(
  cd "$REPO_DIR/handwriting/hst"
  python3 -c 'import composite, costs, generate; print("HST imports: OK")'
  xvfb-run -a python3 -c 'from hst import HST; w=HST(); print("HST GUI: OK"); w.destroy()'
)

(
  cd "$REPO_DIR"
  export SETUPTOOLS_USE_DISTUTILS=stdlib
  "$REPO_DIR/.venv-let/bin/python" -m compileall -q "$REPO_DIR/handwriting/let"
  xvfb-run -a "$REPO_DIR/.venv-let/bin/python" -c \
    'from handwriting.let.let import LET; w=LET(); print("LET GUI: OK"); w.destroy()'
)

if [[ $# -gt 0 ]]; then
  output="${2:-/tmp/hst-modern-smoke.png}"
  (
    cd "$REPO_DIR/handwriting/hst"
    python3 "$SCRIPT_DIR/hst_smoke.py" "$1" "$output"
  )
fi

echo "Modern HELIT verification passed."
