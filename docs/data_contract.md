# Data contract: POST /claims

Machine-readable source of truth: `schemas/fnol.schema.json`. Cross-field rules are enforced in
`services/api/triage_api/validation.py`.

## Fields

| Field | Type | Required | Rule |
|-------|------|----------|------|
| policy_number | string | yes | 5-40 chars, letters, digits, `/` and `-` |
| date_of_loss | date (YYYY-MM-DD) | yes | cannot be in the future |
| vehicle_registration | string | yes | Indian format, e.g. KA01AB1234 |
| description | string | yes | 10-2000 chars, free text, treated as untrusted data |
| time_of_loss | HH:MM | no | 24-hour clock |
| location | string | no | up to 300 chars |
| vehicle_status | enum | no | parked, moving |
| driven_by | enum | no | owner, other |
| third_party_injury | boolean | no | |
| fir_lodged | boolean | no | |
| fir_number | string | no | required when fir_lodged is true; must be empty when false |
| estimated_damage_inr | integer | no | 0 to 10,000,000 |
| claim_type_hint | enum | no | own_damage, third_party, theft, total_loss |

Unknown fields are rejected.

## Responses

- 202 `{run_id, status: "queued"}`
- 422 `{status: "rejected", quarantine_id, errors: [{field, rule, message}]}`: stored in the quarantine table; no run is created
- 429 rate limit exceeded
