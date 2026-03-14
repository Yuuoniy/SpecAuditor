#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_FILE="${ROOT_DIR}/artifact/config/llm.env"
KERNEL_PATH="${KERNEL_PATH:-/root/linux}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/artifact/results/reproduced_generation}"
MODEL="${MODEL:-claude-sonnet-4-20250514}"
MAX_WORKERS="${MAX_WORKERS:-4}"

usage() {
  cat <<'EOF'
Usage: bash artifact/reproduced_generation/run.sh [options]

Options:
  --kernel-path PATH    Linux kernel repository to analyze
  --output-dir PATH     Directory for generated outputs
  --model MODEL         LLM model for stage1/2/4
  --max-workers N       Worker count for stage4 generation
  --help                Show this message

Default behavior:
  - If artifact/config/llm.env exists, this script loads it
    automatically before running the reproduced subset.
  - Stage3 retrieval is reused from the shipped reproduced reference CSV.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kernel-path)
      KERNEL_PATH="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --model)
      MODEL="$2"
      shift 2
      ;;
    --max-workers)
      MAX_WORKERS="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  set +a
fi

python3 "$ROOT_DIR/artifact/reproduced_generation/run.py" \
  --kernel-path "$KERNEL_PATH" \
  --output-dir "$OUTPUT_DIR" \
  --model "$MODEL" \
  --max-workers "$MAX_WORKERS"
