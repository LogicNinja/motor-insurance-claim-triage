#!/usr/bin/env bash
set -euo pipefail
API="${API_URL:-http://localhost:8000}"

echo "== valid FNOL =="
curl -sS -i -X POST "$API/claims" -H 'content-type: application/json' -d '{
  "policy_number": "POL-TEST-0001",
  "date_of_loss": "2026-10-05",
  "vehicle_registration": "KA01AB1234",
  "description": "I was making a food delivery run in my own car when the accident happened."
}' | sed -n '1p;$p'

echo
echo "== malformed FNOL (expect 422, quarantined, no run) =="
curl -sS -i -X POST "$API/claims" -H 'content-type: application/json' -d '{
  "policy_number": "x",
  "date_of_loss": "2999-01-01",
  "vehicle_registration": "not-a-plate",
  "description": "short",
  "estimated_damage_inr": -5
}' | sed -n '1p;$p'
