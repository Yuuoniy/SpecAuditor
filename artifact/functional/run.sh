#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_FILE="${ROOT_DIR}/artifact/config/llm.env"
KERNEL_PATH="${KERNEL_PATH:-/root/linux}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/artifact/results/functional}"
MODEL="${MODEL:-claude-sonnet-4-20250514}"
MODE="${MODE:-demo-assisted}"
RUN_VALIDATOR=1
MAX_WORKERS="${MAX_WORKERS:-4}"
MAX_MATCHES="${MAX_MATCHES:-20}"

usage() {
  cat <<'EOF'
Usage: bash artifact/functional/run.sh [options]

Options:
  --kernel-path PATH            Linux kernel repository to analyze
  --output-dir PATH             Directory for generated stage outputs
  --model MODEL                 LLM model for stage1/2/4/5
  --mode MODE                   demo-assisted (default) or live
  --skip-validation             Skip stage1 specification validation
  --max-workers N               Worker count for stage4/stage5
  --max-matches N               Max localized functions to analyze per specification
  --help                        Show this message

Default behavior:
  - If artifact/config/llm.env exists, this script loads it
    automatically before running the pipeline.
  - demo-assisted mode reuses the packaged stage3 retrieval CSV and
    fills missing packaged stage4 targets from the shipped reference.
  - live mode runs stage3 retrieval and keeps only live stage4 outputs.
    It additionally requires artifact/config/embedding.env.
  - For the packaged c158cf914713 case, if the live generalized wording
    fails to retrieve the packaged target, stage3 retries with the
    shipped original generalized query:
    "Function that acquires a reference to a firmware node".
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
    --mode)
      MODE="$2"
      shift 2
      ;;
    --skip-validation)
      RUN_VALIDATOR=0
      shift
      ;;
    --max-workers)
      MAX_WORKERS="$2"
      shift 2
      ;;
    --max-matches)
      MAX_MATCHES="$2"
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

ARGS=(
  --kernel-path "$KERNEL_PATH"
  --output-dir "$OUTPUT_DIR"
  --model "$MODEL"
  --mode "$MODE"
  --max-workers "$MAX_WORKERS"
  --max-matches "$MAX_MATCHES"
)

if [[ "$RUN_VALIDATOR" -eq 0 ]]; then
  ARGS+=(--skip-validation)
fi

python3 "$ROOT_DIR/artifact/functional/run.py" "${ARGS[@]}"
