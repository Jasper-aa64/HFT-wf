#!/usr/bin/env bash
set -u

# TWAP-specific remote runner for the optimization harness.
#
# This script intentionally does not reuse the Psi parquet compare path. It
# validates TWAP correctness with TWAP runtime tools and measures push latency
# with twap_position_push_perf_test.

ROOT="${ROOT:-/root/work/Code2}"
CONTROL_ROOT="${CONTROL_ROOT:-}"
ENV_FILE="${ENV_FILE:-/root/work/.toolchain/psi-env-code2.sh}"
RUN_ID="${RUN_ID:-twap_headless_$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-/tmp/twap_headless_runs/$RUN_ID}"
ENDPOINT="${ENDPOINT:-192.168.170.62:18321}"
HOST_KEY="${HOST_KEY:-17062}"
USER_ID="${USER_ID:-dc548fe6083e4523a918aaef1a68b857}"
JWT_SECRET="${JWT_SECRET:-ssss}"
RUNTIME_DIR="${RUNTIME_DIR:-/tmp/code2-twap-runtime}"
MEASURE_CASES="${MEASURE_CASES:-100:50:120 500:20:180 1000:20:240 500:5:240}"
BUILD_TARGETS="${BUILD_TARGETS:-PsiGrpcServer PsiTraderRunner twap_current_task_runtime_test twap_position_push_perf_test}"
CANDIDATE_ID="${CANDIDATE_ID:-${PSI_CANDIDATE_ID:-manual_twap_candidate}}"
TWAP_CORRECTNESS_MODE="${TWAP_CORRECTNESS_MODE:-push_only}"
MIN_NORMAL_P95_IMPROVEMENT_MS="${MIN_NORMAL_P95_IMPROVEMENT_MS:-1.0}"
MAX_STRESS_P95_REGRESSION_MS="${MAX_STRESS_P95_REGRESSION_MS:-5.0}"
ACTIVE_RUNNER_ROOT=""

mkdir -p "$RUN_DIR" "$RUN_DIR/logs"
: > "$RUN_DIR/timing_samples.tsv"
printf "case\trole\tcount\tinterval_ms\ttimeout_seconds\tsent\treceived\tlost\tunknown_pushes\tavg_ms\tp50_ms\tp95_ms\tp99_ms\tmax_ms\tstatus\tlog_file\n" \
  > "$RUN_DIR/timing_samples.tsv"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$RUN_DIR/summary.txt"
}

json_escape() {
  python3 - "$1" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1]))
PY
}

write_state() {
  local build_status="$1"
  local correctness_status="$2"
  local timing_status="$3"
  local decision="$4"
  local reason="$5"
  BUILD_STATUS="$build_status" \
  CORRECTNESS_STATUS="$correctness_status" \
  TIMING_STATUS="$timing_status" \
  DECISION="$decision" \
  REASON="$reason" \
  ROOT="$ROOT" \
  CONTROL_ROOT="$CONTROL_ROOT" \
  RUN_DIR="$RUN_DIR" \
  CANDIDATE_ID="$CANDIDATE_ID" \
  HOST_KEY="$HOST_KEY" \
  ENDPOINT="$ENDPOINT" \
  USER_ID="$USER_ID" \
  python3 - <<'PY' > "$RUN_DIR/run_state.json"
import json
import os
from datetime import datetime, timezone

payload = {
    "schema": "twap_headless_run_state_v1",
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "candidate_id": os.environ["CANDIDATE_ID"],
    "root": os.environ["ROOT"],
    "control_root": os.environ["CONTROL_ROOT"],
    "run_dir": os.environ["RUN_DIR"],
    "host_key": os.environ["HOST_KEY"],
    "endpoint": os.environ["ENDPOINT"],
    "user_id": os.environ["USER_ID"],
    "build_status": os.environ["BUILD_STATUS"],
    "correctness_status": os.environ["CORRECTNESS_STATUS"],
    "timing_status": os.environ["TIMING_STATUS"],
    "decision": os.environ["DECISION"],
    "reason": os.environ["REASON"],
    "comparison_summary_path": os.path.join(os.environ["RUN_DIR"], "comparison_summary.json"),
    "timing_samples_path": os.path.join(os.environ["RUN_DIR"], "timing_samples.tsv"),
}
print(json.dumps(payload, indent=2, ensure_ascii=False))
PY
}

write_failure() {
  local reason="$1"
  local build_status="${2:-unknown}"
  local correctness_status="${3:-not_run}"
  local timing_status="${4:-not_run}"
  log "ERROR $reason"
  if [ -n "$ACTIVE_RUNNER_ROOT" ]; then
    stop_runner "$ACTIVE_RUNNER_ROOT" || true
  fi
  write_state "$build_status" "$correctness_status" "$timing_status" "rejected" "$reason"
  write_summary "$build_status" "$correctness_status" "$timing_status" "rejected" "$reason"
  exit 1
}

build_root() {
  local role="$1"
  local root="$2"
  local build_log="$RUN_DIR/${role}_build.log"
  if [ ! -d "$root" ]; then
    write_failure "${role}_root_missing:$root" "failed" "not_run" "not_run"
  fi
  if [ ! -f "$ENV_FILE" ]; then
    write_failure "missing_env_file:$ENV_FILE" "failed" "not_run" "not_run"
  fi
  log "building $role root=$root"
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  if [ ! -d "$root/build" ] || [ ! -f "$root/build/CMakeCache.txt" ]; then
    log "configure build directory $role/root=$root"
    if ! cmake -S "$root" -B "$root/build" \
      -DCMAKE_BUILD_TYPE=Release \
      -DGRPC_INSTALL_DIR=/root/work/.toolchain/grpc-install-real \
      -DLEVELDB_ROOT=/usr/local \
      -DATOMIC_QUEUE_DIR=/root/work/.toolchain/atomic_queue-1.6.3 \
      >> "$build_log" 2>&1; then
      tail -120 "$build_log" > "$RUN_DIR/${role}_build_tail.txt" 2>/dev/null || true
      write_failure "${role}_configure_failed" "failed" "not_run" "not_run"
    fi
  fi
  for target in $BUILD_TARGETS; do
    log "build target $role/$target"
    if ! (cd "$root" && cmake --build build --target "$target" -j 4 >> "$build_log" 2>&1); then
      tail -120 "$build_log" > "$RUN_DIR/${role}_build_tail.txt" 2>/dev/null || true
      write_failure "${role}_build_failed:$target" "failed" "not_run" "not_run"
    fi
  done
  log "build passed $role"
}

runner_path() {
  local root="$1"
  echo "$root/build/build_/Release/bin/PsiTraderRunner/PsiTraderRunner"
}

tool_path() {
  local root="$1"
  local tool="$2"
  echo "$root/build/PsiGrpcServer/$tool"
}

stop_runner() {
  local root="$1"
  local runner
  runner="$(runner_path "$root")"
  local oldpid
  oldpid="$(cat "$RUNTIME_DIR/runner.pid" 2>/dev/null || true)"
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    kill "$oldpid" 2>/dev/null || true
    sleep 2
    if kill -0 "$oldpid" 2>/dev/null; then
      kill -9 "$oldpid" 2>/dev/null || true
    fi
  fi
  pkill -f "$runner" 2>/dev/null || true
}

start_runner() {
  local role="$1"
  local root="$2"
  local runner
  runner="$(runner_path "$root")"
  if [ ! -x "$runner" ]; then
    write_failure "${role}_runner_missing:$runner" "pass" "failed" "not_run"
  fi
  mkdir -p "$RUNTIME_DIR"
  stop_runner "$root"
  local out="$RUN_DIR/${role}_runner.out"
  log "starting runner role=$role runner=$runner"
  ACTIVE_RUNNER_ROOT="$root"
  (
    cd "$RUNTIME_DIR" || exit 1
    setsid "$runner" > "$out" 2>&1 < /dev/null &
    echo $! > "$RUNTIME_DIR/runner.pid"
  )
  for _ in $(seq 1 80); do
    if ss -lntp | grep -q "$ENDPOINT"; then
      log "runner listening role=$role endpoint=$ENDPOINT pid=$(cat "$RUNTIME_DIR/runner.pid" 2>/dev/null || true)"
      return 0
    fi
    sleep 1
  done
  tail -160 "$out" > "$RUN_DIR/${role}_runner_tail.txt" 2>/dev/null || true
  write_failure "${role}_runner_not_listening:$ENDPOINT" "pass" "failed" "not_run"
}

make_token() {
  JWT_SECRET="$JWT_SECRET" USER_ID="$USER_ID" python3 - <<'PY'
import base64
import hashlib
import hmac
import json
import os
import time

secret = os.environ["JWT_SECRET"].encode()
now = int(time.time())
header = {"alg": "HS256", "typ": "JWT"}
payload = {
    "iss": "auth-server",
    "typ": "JWT",
    "iat": now,
    "exp": now + 86400,
    "username": "gatekeeper",
    "userId": os.environ["USER_ID"],
}

def b64(obj):
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=")

msg = b64(header) + b"." + b64(payload)
sig = base64.urlsafe_b64encode(hmac.new(secret, msg, hashlib.sha256).digest()).rstrip(b"=")
print((msg + b"." + sig).decode())
PY
}

parse_perf_log() {
  local case_name="$1"
  local role="$2"
  local count="$3"
  local interval="$4"
  local timeout="$5"
  local log_file="$6"
  CASE_NAME="$case_name" ROLE="$role" COUNT="$count" INTERVAL="$interval" TIMEOUT="$timeout" LOG_FILE="$log_file" \
  python3 - <<'PY'
import os
from pathlib import Path

data = {}
for line in Path(os.environ["LOG_FILE"]).read_text(errors="replace").splitlines():
    if ":" not in line:
        continue
    key, value = line.split(":", 1)
    data[key.strip()] = value.strip()

fields = [
    os.environ["CASE_NAME"],
    os.environ["ROLE"],
    os.environ["COUNT"],
    os.environ["INTERVAL"],
    os.environ["TIMEOUT"],
    data.get("sent", ""),
    data.get("received", ""),
    data.get("lost", ""),
    data.get("unknownPushes", ""),
    data.get("latencyMs.avg", ""),
    data.get("latencyMs.p50", ""),
    data.get("latencyMs.p95", ""),
    data.get("latencyMs.p99", ""),
    data.get("latencyMs.max", ""),
    data.get("status", ""),
    os.environ["LOG_FILE"],
]
print("\t".join(fields))
PY
}

run_correctness() {
  local root="$1"
  local token="$2"
  local tool
  tool="$(tool_path "$root" twap_current_task_runtime_test)"
  local out="$RUN_DIR/correctness.log"
  if [ ! -x "$tool" ]; then
    write_failure "correctness_tool_missing:$tool" "pass" "failed" "not_run"
  fi
  log "running TWAP correctness smoke mode=$TWAP_CORRECTNESS_MODE"
  "$tool" --endpoint "$ENDPOINT" --token "$token" --user-id "$USER_ID" > "$out" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$TWAP_CORRECTNESS_MODE" != "push_only" ]; then
    tail -160 "$out" > "$RUN_DIR/correctness_tail.txt" 2>/dev/null || true
    write_failure "correctness_failed" "pass" "failed" "not_run"
  fi
  if ! grep -q "PASS aggregation push payload" "$out"; then
    write_failure "correctness_missing_aggregation_pass_marker" "pass" "failed" "not_run"
  fi
  if [ "$rc" -ne 0 ]; then
    log "WARN correctness tool rc=$rc, but push_only marker passed; ignoring non-push checks"
  fi
  log "correctness passed"
}

run_perf_case() {
  local role="$1"
  local root="$2"
  local token="$3"
  local count="$4"
  local interval="$5"
  local timeout="$6"
  local case_name="${count}_i${interval}"
  local tool
  tool="$(tool_path "$root" twap_position_push_perf_test)"
  local out="$RUN_DIR/${role}_${case_name}.log"
  if [ ! -x "$tool" ]; then
    write_failure "${role}_perf_tool_missing:$tool" "pass" "pass" "failed"
  fi
  log "running perf role=$role case=$case_name"
  if ! "$tool" --endpoint "$ENDPOINT" --token "$token" --user-id "$USER_ID" --count "$count" --interval-ms "$interval" --timeout-seconds "$timeout" > "$out" 2>&1; then
    tail -120 "$out" > "$RUN_DIR/${role}_${case_name}_tail.txt" 2>/dev/null || true
    write_failure "${role}_perf_failed:$case_name" "pass" "pass" "failed"
  fi
  parse_perf_log "$case_name" "$role" "$count" "$interval" "$timeout" "$out" >> "$RUN_DIR/timing_samples.tsv"
}

run_perf_suite_for_role() {
  local role="$1"
  local root="$2"
  local token="$3"
  start_runner "$role" "$root"
  local spec count interval timeout
  for spec in $MEASURE_CASES; do
    IFS=: read -r count interval timeout <<EOF
$spec
EOF
    run_perf_case "$role" "$root" "$token" "$count" "$interval" "$timeout"
  done
  stop_runner "$root"
}

write_summary() {
  local build_status="$1"
  local correctness_status="$2"
  local timing_status="$3"
  local decision="$4"
  local reason="$5"
  BUILD_STATUS="$build_status" \
  CORRECTNESS_STATUS="$correctness_status" \
  TIMING_STATUS="$timing_status" \
  DECISION="$decision" \
  REASON="$reason" \
  RUN_DIR="$RUN_DIR" \
  CANDIDATE_ID="$CANDIDATE_ID" \
  ROOT="$ROOT" \
  CONTROL_ROOT="$CONTROL_ROOT" \
  MIN_NORMAL_P95_IMPROVEMENT_MS="$MIN_NORMAL_P95_IMPROVEMENT_MS" \
  MAX_STRESS_P95_REGRESSION_MS="$MAX_STRESS_P95_REGRESSION_MS" \
  python3 - <<'PY' > "$RUN_DIR/comparison_summary.json"
import csv
import json
import os
from pathlib import Path
from datetime import datetime, timezone

run_dir = Path(os.environ["RUN_DIR"])
rows = []
path = run_dir / "timing_samples.tsv"
if path.exists():
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

def to_float(row, key):
    try:
        return float(row.get(key) or "nan")
    except ValueError:
        return float("nan")

control = {row["case"]: row for row in rows if row.get("role") == "control"}
candidate = {row["case"]: row for row in rows if row.get("role") == "candidate"}
case_deltas = []
for case, cand in candidate.items():
    ctrl = control.get(case)
    if not ctrl:
        continue
    ctrl_p95 = to_float(ctrl, "p95_ms")
    cand_p95 = to_float(cand, "p95_ms")
    case_deltas.append({
        "case": case,
        "control_p95_ms": ctrl.get("p95_ms", ""),
        "candidate_p95_ms": cand.get("p95_ms", ""),
        "p95_delta_ms": "" if ctrl_p95 != ctrl_p95 or cand_p95 != cand_p95 else round(cand_p95 - ctrl_p95, 6),
        "control_lost": ctrl.get("lost", ""),
        "candidate_lost": cand.get("lost", ""),
    })

lost_failures = [
    row for row in rows
    if (row.get("lost") or "") not in {"", "0"} or (row.get("status") or "") not in {"", "PASS"}
]
normal_cases = [delta for delta in case_deltas if delta["case"] in {"500_i20", "1000_i20"}]
stress_cases = [delta for delta in case_deltas if delta["case"] in {"500_i5"}]
min_normal_p95_improvement_ms = float(os.environ["MIN_NORMAL_P95_IMPROVEMENT_MS"])
max_stress_p95_regression_ms = float(os.environ["MAX_STRESS_P95_REGRESSION_MS"])
max_normal_p95_regression_ms = 1.0
normal_improved = bool(normal_cases) and all(
    isinstance(delta.get("p95_delta_ms"), (int, float))
    and delta["p95_delta_ms"] <= -min_normal_p95_improvement_ms
    for delta in normal_cases
)
normal_regression_ok = all(
    isinstance(delta.get("p95_delta_ms"), (int, float))
    and delta["p95_delta_ms"] <= max_normal_p95_regression_ms
    for delta in normal_cases
)
stress_regression_ok = all(
    isinstance(delta.get("p95_delta_ms"), (int, float))
    and delta["p95_delta_ms"] <= max_stress_p95_regression_ms
    for delta in stress_cases
)
has_control = bool(control)
accepted = (
    os.environ["BUILD_STATUS"] == "pass"
    and os.environ["CORRECTNESS_STATUS"] == "pass"
    and os.environ["TIMING_STATUS"] == "pass"
    and not lost_failures
    and has_control
    and normal_improved
    and stress_regression_ok
)
decision = os.environ["DECISION"]
if decision == "auto":
    rejected = bool(lost_failures) or not normal_regression_ok or not stress_regression_ok
    decision = "promotion_candidate" if accepted else "rejected" if rejected else "screening_only"

payload = {
    "schema": "twap_headless_comparison_summary_v1",
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "candidate_id": os.environ["CANDIDATE_ID"],
    "root": os.environ["ROOT"],
    "control_root": os.environ["CONTROL_ROOT"],
    "build_status": os.environ["BUILD_STATUS"],
    "correctness_status": os.environ["CORRECTNESS_STATUS"],
    "timing_status": os.environ["TIMING_STATUS"],
    "decision": decision,
    "reason": os.environ["REASON"],
    "accepted": accepted,
    "lost_failure_count": len(lost_failures),
    "has_control": has_control,
    "normal_frequency_p95_improved": normal_improved,
    "normal_frequency_p95_regression_ok": normal_regression_ok,
    "normal_frequency_min_p95_improvement_ms": min_normal_p95_improvement_ms,
    "normal_frequency_max_p95_regression_ms": max_normal_p95_regression_ms,
    "stress_p95_regression_ok": stress_regression_ok,
    "stress_max_p95_regression_ms": max_stress_p95_regression_ms,
    "case_deltas": case_deltas,
    "timing_samples": rows,
}
print(json.dumps(payload, indent=2, ensure_ascii=False))
PY
}

main() {
  log "TWAP headless remote start root=$ROOT control_root=$CONTROL_ROOT run_dir=$RUN_DIR"
  local token
  token="$(make_token)"

  build_root "candidate" "$ROOT"
  if [ -n "$CONTROL_ROOT" ]; then
    build_root "control" "$CONTROL_ROOT"
  fi

  start_runner "candidate_correctness" "$ROOT"
  run_correctness "$ROOT" "$token"
  stop_runner "$ROOT"

  if [ -n "$CONTROL_ROOT" ]; then
    run_perf_suite_for_role "control" "$CONTROL_ROOT" "$token"
  fi
  run_perf_suite_for_role "candidate" "$ROOT" "$token"

  write_state "pass" "pass" "pass" "auto" "completed"
  write_summary "pass" "pass" "pass" "auto" "completed"
  log "TWAP headless remote completed"
}

main "$@"
