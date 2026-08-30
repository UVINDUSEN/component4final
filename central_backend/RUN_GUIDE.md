# C1 Contract Migration — Run Guide

Everything here has been applied to a clone of your repo and tested.
**210 tests pass, 0 fail** (146 backend + 27 C1 backend + 23 C1 fusion + 14 fusion invariants).

---

## Before you start

Put all 5 files in `~/Downloads/component4final/`. Then:

```
cd ~/Downloads/component4final/
ls apply_c1_patch.sh apply_fusion_patch.sh test_c1_contract.py test_c1_fusion_contract.py make_c1_reference.py
```

All five should list. If any are missing, re-download before continuing.

---

## Step 1 — Stop everything

Both patches edit files that are loaded into memory at startup. A running
backend will keep serving the old code.

```
lsof -ti:8000 | xargs kill -9 2>/dev/null; echo "port 8000 cleared"
```

Leave ngrok running — it just forwards, it holds no code.

---

## Step 2 — Patch the central backend

```
cd ~/Downloads/component4final/central_backend/
cp ../apply_c1_patch.sh . && cp ../test_c1_contract.py . && cp ../make_c1_reference.py .
chmod +x apply_c1_patch.sh
./apply_c1_patch.sh
```

**Expected output — all five steps:**

```
[1/5] backups written (.bak-YYYYMMDD-HHMMSS)
      _call_c1_legacy -> live two-horizon adapter
[2/5] modality_clients.py patched
      pairing registers c1_device_id; physio ingest returns both scales
[3/5] main.py patched
      test_backend.py: C1 id-mapping assertion updated
[4/5] old C1 reference RETIRED ...
[5/5] syntax OK on both files
```

If it stops with `ERROR: run this from central_backend/`, you are in the wrong
directory. If an `assert` fails, your local file differs from the repo — stop
and tell me which assert; do not force it.

---

## Step 3 — Patch the fusion service

```
cd ~/Downloads/component4final/fusion_service/
cp ../apply_fusion_patch.sh . && cp ../test_c1_fusion_contract.py .
chmod +x apply_fusion_patch.sh
./apply_fusion_patch.sh
```

**Expected:**

```
[1/4] backups written (.bak-...)
      PhysioTick.score + ManualComponent.score bounded to 0-1
[2/4] app.py patched
      to_reading: C1 read from risk_forecast; all scores bounded 0-1
[3/4] clients.py patched
[4/4] syntax OK on both files
```

---

## Step 4 — Run all four test suites

```
cd ~/Downloads/component4final/central_backend/
BACKEND_API_TOKEN="" MRN_PEPPER=t ./.venv/bin/python3 test_backend.py | tail -3
./.venv/bin/python3 test_c1_contract.py | tail -3
```

```
cd ../fusion_service/
python3 test_c1_fusion_contract.py | tail -3
python3 validate_fusion.py | grep "CODE-LEVEL"
```

**Expected:**

| Suite | Expected |
|---|---|
| `test_backend.py` | 146 passed, 0 failed |
| `test_c1_contract.py` | 27 passed, 0 failed |
| `test_c1_fusion_contract.py` | 23 passed, 0 failed |
| `validate_fusion.py` | CODE-LEVEL VALIDATION: 14 passed, 0 failed |

Backend goes 145 -> 146 because one old test asserted C1 falls back to your
internal UUID. Dewdu asked for pairing to register the C1 device id, so that
assumption is now wrong. It was replaced with two tests: C1 resolves to the
paired `app_user_id`, and a genuinely unmapped modality still falls back.

**If anything fails, roll back — do not debug live:**

```
cd ~/Downloads/component4final/central_backend/
cp modality_clients.py.bak-* modality_clients.py
cp main.py.bak-* main.py
```

---

## Step 5 — Restart and smoke-test

```
cd ~/Downloads/component4final/central_backend/
lsof -ti:8000 | xargs kill -9 2>/dev/null
./.venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

In a second tab:

```
cd ~/Downloads/component4final/central_backend/
curl -sS http://127.0.0.1:8000/health -w "\nHTTP %{http_code}\n"
```

Then a live physiological ingest against Dewdu's Space:

```
curl -sS -X POST http://127.0.0.1:8000/v1/ingest/physiological \
  -H "Authorization: Bearer $(grep '^BACKEND_API_TOKEN=' .env | cut -d= -f2- | tr -d '"')" \
  -H "Content-Type: application/json" \
  -d '{"app_user_id":"P_8A0840A798B81072","device_user_id":"P_8A0840A798B81072"}' \
  -w "\n--- HTTP %{http_code} ---\n"
```

**Read the `score` field carefully.** It must be between 0 and 1.
If you ever see a value above 1 (e.g. `42.0`), the patch did not take —
you are still on the old process.

`status: "warming_up"` with `score: null` is **correct**, not a failure: C1
needs 10 consecutive one-minute windows before it will predict.

---

## Step 6 — Set C1_URL if it is not already

```
grep C1_URL .env || echo 'C1_URL=https://dewdu-physiological-anxiety-escalation.hf.space' >> .env
```

No trailing slash. Restart after any `.env` edit.

---

## Step 7 — When Dewdu sends the held-out scores

Ask him for exactly this shape:

```json
{
  "model_version": "c1-unmasked-lstm-ae-wesad-v2",
  "forecast_model_version": "c1-direct-ridge-score-forecast-wesad-v5",
  "forecasts": [[55.0, 64.0], [12.0, 18.0], "... one pair per held-out window"]
}
```

Raw 0-100 pairs, minimum 30, ideally 200+. The script applies `max(+5,+10)/100`
itself so the formula cannot drift between your side and his. He can instead
send `{"scores": [...]}` already on 0-1 and it will accept that too.

```
cd ~/Downloads/component4final/central_backend/
./.venv/bin/python3 make_c1_reference.py c1_heldout.json
lsof -ti:8000 | xargs kill -9 2>/dev/null
./.venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Until then C1 harmonisation is **provisional** and every response says so.

---

## What changed, in one table

| File | Change |
|---|---|
| `central_backend/modality_clients.py` | New C1 adapter. `max(risk_forecast)/100`. Validates 2 finite values in 0-100, horizons exactly `[5,10]`. Rejects malformed input instead of clamping. Non-success -> `null`, never `0.0`. Real coverage + real `captured_at`. Both model versions and the full forecast kept in audit detail. |
| `central_backend/main.py` | Pairing also registers `c1_device_id` = `app_user_id`. Physiological ingest returns `c1_fusion_score`, `composite_score` (0-1) and `final_risk_score` (0-100) with a `scale_note`. |
| `central_backend/test_backend.py` | Old C1 id-mapping assertion replaced with two correct ones. 145 -> 146. |
| `fusion_service/app.py` | `PhysioTick.score` and `ManualComponent.score` bounded `ge=0.0, le=1.0`. A 0-100 value now fails with 422 instead of saturating the harmoniser. |
| `fusion_service/clients.py` | `to_reading` reads C1 from `risk_forecast`, never from `score`. All modality scores bounded 0-1, rejected not clamped. |
| `fusion_service/reference/c1_physiological.json` | Retired to `RETIRED-...json.txt`. |

---

## Two things to tell Dewdu

**1. Confidence — I took your second option.** Making it nullable would crash
`doctor_timeline` at `round(r["confidence"], 3)`. Instead there is a named
`C1_POLICY_DEFAULT_CONFIDENCE = 0.5`, with `_c1_confidence_is_policy_default: true`
in the detail blob and "fusion policy default (C1 publishes none)" in the note.
Nothing is labelled as C1-generated confidence.

**2. A second bug, in `fusion_service/clients.py`.** It picked `body["score"]`,
which in your new contract is `0.42` (the *current* anomaly) rather than the
fusion value `0.64`. Two different quantities that happen to be the same shape.
Only reachable under `FUSION_MODE=http`; the backend runs `inprocess`, so it was
never live. Fixed anyway.

---

## The saturation bug, measured

The old reference distribution has `max = 0.5288`. Every new-contract score sits
above it. Measured through your real `Harmoniser`:

| C1 fusion score | old reference percentile | after retiring it |
|---|---|---|
| 0.55 | 1.0 | 0.55 |
| 0.60 | 1.0 | 0.60 |
| 0.64 | 1.0 | 0.64 |
| 0.70 | 1.0 | 0.70 |
| 0.80 | 1.0 | 0.80 |

Five distinct physiological states collapsing to one identical value. Dewdu
predicted saturation; it was total.

---

## Not done, and why

- **Live call to the real Space.** My sandbox cannot reach `hf.space`. Every test
  runs against a mock built from Dewdu's documented contract. Step 5 is your
  first real call — watch the `score` field.
- **`FUSION_MODE=http` end-to-end.** The fusion service POSTs to `C1_URL` as a
  full endpoint, but live C1 is `GET /predict/{user_id}`. That mismatch predates
  this change and only matters if you switch off `inprocess`.
