#!/usr/bin/env bash
# Drive the ledger end-to-end through the existing Python services + Camunda.
#
# Producer-side contentId conventions in the upstream services:
#   verification-service:  contentId = "content-<sha256(contentUrl)[:16]>"
#   reporting-service:     contentId = postId
#
# Therefore, for verification + reporting events to land on the same ledger
# entry, this script:
#   1. submits a verification
#   2. reads back the derived contentId from GET /verifications/{id}
#   3. uses that contentId as the postId when filing the report
#
# Two user tasks block the flow at course defaults and are auto-completed
# against the Camunda 8 Run REST API here so the demo is fully unattended:
#   - RegisterUser  : "Check User Background"  -> checkPassed=true
#   - ReportContent : "Check if report is valid" -> report_valid=true
#   - ReportContent : "Review Objection" (manual mode) -> objection_rejected=false
#
# Pre-req: infra (Kafka) up, c8run running, all services in docker-compose.yml started.
set -euo pipefail

USER_SVC=${USER_SVC:-http://localhost:8001}
VERIF_SVC=${VERIF_SVC:-http://localhost:8002}
REPORT_SVC=${REPORT_SVC:-http://localhost:8003}
LEDGER=${LEDGER:-http://localhost:8085}
ZEEBE=${ZEEBE:-http://localhost:8080}
ZEEBE_AUTH=${ZEEBE_AUTH:-demo:demo}

now()  { date +%H:%M:%S; }
info() { echo "[$(now)] $*"; }
die()  { echo "[$(now)] ERROR: $*" >&2; exit 1; }

# Decode a single top-level field out of a JSON blob via python3.
jget() {
  python3 -c "import sys,json;print(json.loads(sys.argv[1]).get('$2',''))" "$1"
}

# POST JSON and assert a JSON response. Echoes response on stdout, dies on error.
post_json() {
  local url="$1" body="$2" resp
  resp=$(curl -sS -X POST "$url" -H 'Content-Type: application/json' -d "$body") \
    || die "POST $url failed"
  python3 -c "import sys,json;json.loads(sys.argv[1])" "$resp" 2>/dev/null \
    || die "non-JSON response from $url: ${resp:-<empty>}"
  printf '%s' "$resp"
}

get_json() {
  local url="$1" resp
  resp=$(curl -sS "$url") || die "GET $url failed"
  python3 -c "import sys,json;json.loads(sys.argv[1])" "$resp" 2>/dev/null \
    || die "non-JSON response from $url: ${resp:-<empty>}"
  printf '%s' "$resp"
}

# Track which user tasks we already completed so we don't hammer the API.
# Space-padded for safe substring matching under macOS bash 3.2.
COMPLETED_TASKS=" "

claim_and_complete_task() {
  local task_key="$1" elem="$2" vars
  case "$elem" in
    Activity_1qokx0x) vars='{"checkPassed": true}' ;;         # RegisterUser  : Check User Background
    Activity_020aoj1) vars='{"report_valid": true}' ;;        # ReportContent : Check if report is valid
    Activity_09g0wne) vars='{"objection_rejected": false}' ;; # ReportContent : Review Objection (approve objection)
    *)                vars='{}' ;;
  esac
  info "   auto-complete user-task $task_key ($elem) vars=$vars"
  curl -sS -u "$ZEEBE_AUTH" -X PATCH \
    "$ZEEBE/v2/user-tasks/$task_key/assignment" \
    -H 'Content-Type: application/json' \
    -d '{"assignee":"demo","action":"claim","allowOverride":true}' \
    >/dev/null 2>&1 || true
  curl -sS -u "$ZEEBE_AUTH" -X POST \
    "$ZEEBE/v2/user-tasks/$task_key/completion" \
    -H 'Content-Type: application/json' \
    -d "{\"variables\": $vars}" \
    >/dev/null 2>&1 || true
  COMPLETED_TASKS+="${task_key} "
}

# Get state of a process instance ("ACTIVE", "COMPLETED", "CANCELED", "TERMINATED" or "").
process_state() {
  local pid="$1"
  local resp
  resp=$(curl -sS -u "$ZEEBE_AUTH" -X POST "$ZEEBE/v2/process-instances/search" \
    -H 'Content-Type: application/json' \
    -d "{\"filter\":{\"processInstanceKey\":\"$pid\"},\"page\":{\"limit\":1}}") || return 0
  python3 -c "
import sys, json
try:
    d = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
items = d.get('items', [])
print(items[0].get('state', '') if items else '')
" "$resp"
}

# Auto-complete user tasks for $pid and wait until the process is no longer
# ACTIVE (or until $timeout seconds elapse). Handles cascading user tasks
# (e.g. Review Objection after report_valid=true) within the same call.
wait_for_process() {
  local pid="$1" timeout="${2:-45}"
  local end=$(( $(date +%s) + timeout ))
  while [ "$(date +%s)" -lt "$end" ]; do
    local resp tasks
    resp=$(curl -sS -u "$ZEEBE_AUTH" -X POST "$ZEEBE/v2/user-tasks/search" \
      -H 'Content-Type: application/json' \
      -d "{\"filter\":{\"state\":\"CREATED\",\"processInstanceKey\":\"$pid\"},\"page\":{\"limit\":20}}") \
      || { sleep 1; continue; }
    tasks=$(python3 -c "
import sys, json
try:
    d = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
for t in d.get('items', []):
    print(t['userTaskKey'], t['elementId'])
" "$resp") || tasks=""
    if [ -n "$tasks" ]; then
      while IFS=' ' read -r task_key elem; do
        [ -z "$task_key" ] && continue
        case "$COMPLETED_TASKS" in *" $task_key "*) continue ;; esac
        claim_and_complete_task "$task_key" "$elem"
      done <<< "$tasks"
    fi

    local state
    state=$(process_state "$pid")
    if [ -n "$state" ] && [ "$state" != "ACTIVE" ]; then
      info "   process pid=$pid finished (state=$state)"
      return 0
    fi
    sleep 1
  done
  info "   WARNING: process pid=$pid still ACTIVE after ${timeout}s"
}

CONTENT_URL="https://example.com/post-demo-$(date +%s)"

info "0) Camunda 8 Run reachable?"
curl -sS -o /dev/null --fail -u "$ZEEBE_AUTH" "$ZEEBE/v2/topology" \
  || die "Camunda 8 Run not reachable at $ZEEBE (start it with: cd <c8run-dir> && ./start.sh)"

info "1) Register a user"
USER_RESP=$(post_json "$USER_SVC/users" '{"username":"alice","password":"secret"}')
# POST /users returns both a UUID userId (the actual user identifier stored
# in user-service) and a processInstanceKey (the Camunda process instance).
# We need the UUID for cross-service correlation and the pid for waiting.
USER_ID=$(jget "$USER_RESP" userId)
USER_PID=$(jget "$USER_RESP" processInstanceKey)
[ -n "$USER_ID" ]  || die "no userId in user response: $USER_RESP"
[ -n "$USER_PID" ] || die "no processInstanceKey in user response: $USER_RESP"
info "   userId=$USER_ID  processInstanceKey=$USER_PID"

info "1a) Auto-complete RegisterUser background-check task and wait for completion (pid=$USER_PID)"
wait_for_process "$USER_PID" 30

info "2) Submit a verification for contentUrl=$CONTENT_URL (auto-approve)"
VERIF_RESP=$(post_json "$VERIF_SVC/verifications" \
  "{\"userId\":\"$USER_ID\",\"contentUrl\":\"$CONTENT_URL\",\"contentTitle\":\"Demo article\",\"peerMode\":\"auto-approve\"}")
VERIF_ID=$(jget "$VERIF_RESP" verificationId)
[ -n "$VERIF_ID" ] || die "no verificationId returned: $VERIF_RESP"
info "   verificationId=$VERIF_ID"
sleep 4

info "3) Read back the derived contentId (sha256-hashed by verification-service)"
VERIF_DETAIL=$(get_json "$VERIF_SVC/verifications/$VERIF_ID")
CONTENT_ID=$(jget "$VERIF_DETAIL" contentId)
[ -n "$CONTENT_ID" ] || die "no contentId derived from verification detail: $VERIF_DETAIL"
info "   contentId=$CONTENT_ID"

info "4) File a report against postId=$CONTENT_ID (matches the hashed contentId)"
REPORT_RESP=$(post_json "$REPORT_SVC/reports" \
  "{\"reporterId\":\"$USER_ID\",\"postId\":\"$CONTENT_ID\",\"postOwnerId\":\"$USER_ID\",\"reason\":\"spam\",\"objectionMode\":\"manual\"}")
REPORT_PID=$(jget "$REPORT_RESP" processInstanceKey)
REPORT_ID=$(jget "$REPORT_RESP" reportId)
info "   reportId=$REPORT_ID processInstanceKey=$REPORT_PID"
echo "$REPORT_RESP" | python3 -m json.tool || true

info "4a) Auto-complete ReportContent user tasks (validity + objection review) and wait for completion"
wait_for_process "$REPORT_PID" 45

info "5) Wait for events to settle, then query the ledger"
sleep 4
echo "--- state ---"
curl -sS "$LEDGER/api/content/$CONTENT_ID/state" | python3 -m json.tool || true
echo "--- decision-trace ---"
curl -sS "$LEDGER/api/content/$CONTENT_ID/decision-trace" | python3 -m json.tool || true

info "Open the UI at $LEDGER to explore interactively."
