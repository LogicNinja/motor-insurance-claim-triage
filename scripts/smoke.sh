#!/usr/bin/env bash
set -euo pipefail

API="${API_URL:-http://localhost:8001}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
windows_docker="/mnt/c/Users/${USER:-}/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe"
if [[ -x "$windows_docker" ]] && grep -qi microsoft /proc/version; then
  DOCKER_BIN="$windows_docker"
elif ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
  echo "Docker CLI not found. Set DOCKER_BIN to the Docker CLI path." >&2
  exit 1
fi
CLIENT_ID="stage3-smoke-$(date +%s)-$$-$RANDOM"

split_response() {
  RESPONSE_CODE="${1##*$'\n'}"
  RESPONSE_BODY="${1%$'\n'*}"
}

echo "== valid FNOL =="
valid_response=$(curl -sS -w $'\n%{http_code}' -X POST "$API/claims" \
  -H 'content-type: application/json' -H "x-client-id: $CLIENT_ID" -d '{
    "policy_number": "POL-TEST-0001",
    "date_of_loss": "2026-10-05",
    "vehicle_registration": "KA01AB1234",
    "description": "I was making a food delivery run in my own car when the accident happened."
  }')
split_response "$valid_response"
if [[ "$RESPONSE_CODE" != "202" ]]; then
  echo "Expected 202 for valid FNOL; received $RESPONSE_CODE: $RESPONSE_BODY" >&2
  exit 1
fi
run_id=$(printf '%s' "$RESPONSE_BODY" | "$DOCKER_BIN" compose exec -T api python -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
echo "Created run: $run_id"
worker_result=""
for attempt in $(seq 1 30); do
  run_status=$(curl -fsS "$API/runs/$run_id")
  if worker_result=$(printf '%s' "$run_status" | "$DOCKER_BIN" compose exec -T api python -c '
import json,sys
body=json.load(sys.stdin)
result=body.get("result")
if not isinstance(result,dict):
    raise SystemExit(3)
assert result.get("run_id") == body.get("run_id"), result
assert result.get("status") in {"analyzed", "escalated"}, result
coverage=result.get("coverage") or {}
routing=result.get("routing") or {}
assert coverage.get("position") in {"potentially_not_covered", "escalate"}, result
assert routing.get("route") in {"investigation", "human_review"}, result
required={"Section I(A) clause 1 item VI", "General Exceptions III(a)", "CHAPTER 4 - Limitations as to Use"}
cited={item.get("clause_id") for item in coverage.get("citations", [])}
assert required <= cited, (required-cited, result)
if coverage.get("degraded"):
    assert coverage.get("position") == "escalate", result
    assert routing.get("route") == "human_review", result
print("status=" + body["status"] + "; result=" + result["status"] + "; route=" + result["routing"]["route"])
'); then
    break
  fi
  sleep 1
done
if [[ -z "$worker_result" ]]; then
  echo "Timed out waiting for the worker to persist a result for $run_id" >&2
  exit 1
fi
echo "Real queue result: $worker_result"

echo
echo "== malformed FNOL: field errors, quarantine, no run =="
correlation_id="stage3-invalid-$(date +%s)-$$-$RANDOM"
invalid_response=$(curl -sS -w $'\n%{http_code}' -X POST "$API/claims" \
  -H 'content-type: application/json' \
  -H "x-client-id: $CLIENT_ID" \
  -H "x-correlation-id: $correlation_id" -d '{
    "policy_number": "x",
    "date_of_loss": "2999-01-01",
    "vehicle_registration": "not-a-plate",
    "description": "short",
    "estimated_damage_inr": -5
  }')
split_response "$invalid_response"
if [[ "$RESPONSE_CODE" != "422" ]]; then
  echo "Expected 422 for malformed FNOL; received $RESPONSE_CODE: $RESPONSE_BODY" >&2
  exit 1
fi
printf '%s' "$RESPONSE_BODY" | "$DOCKER_BIN" compose exec -T api python -c '
import json,sys
body=json.load(sys.stdin)
assert body.get("status") == "rejected", body
assert body.get("quarantine_id"), body
fields={error.get("field") for error in body.get("errors", [])}
expected={"policy_number","date_of_loss","vehicle_registration","description","estimated_damage_inr"}
assert expected <= fields, (expected-fields, body)
print("Quarantine:", body["quarantine_id"])
print("Field errors:", ", ".join(sorted(fields)))
'

# Correlate against Postgres so this proves a quarantine row exists and no run
# row was created for the malformed request.
counts=$("$DOCKER_BIN" compose exec -T postgres psql -v ON_ERROR_STOP=1 -U triage -d triage -Atqc \
  "SELECT count(*) FROM quarantine WHERE correlation_id='$correlation_id'; SELECT count(*) FROM runs WHERE correlation_id='$correlation_id';")
quarantine_count=$(printf '%s\n' "$counts" | sed -n '1p')
run_count=$(printf '%s\n' "$counts" | sed -n '2p')
if [[ "$quarantine_count" != "1" || "$run_count" != "0" ]]; then
  echo "Expected quarantine=1 and runs=0 for $correlation_id; got quarantine=$quarantine_count runs=$run_count" >&2
  exit 1
fi
echo "Database proof: quarantine=$quarantine_count, runs=$run_count for correlation $correlation_id"
