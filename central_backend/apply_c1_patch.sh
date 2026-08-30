#!/bin/bash
# ============================================================================
# C1 CONTRACT MIGRATION — Dewdu's live two-horizon forecast contract (2026-08)
# Run from: central_backend/
# ============================================================================
set -e
echo "=== C1 contract migration ==="

if [ ! -f modality_clients.py ]; then
  echo "ERROR: run this from central_backend/ (modality_clients.py not found)"; exit 1
fi

STAMP=$(date +%Y%m%d-%H%M%S)
cp modality_clients.py "modality_clients.py.bak-$STAMP"
cp main.py "main.py.bak-$STAMP"
echo "[1/5] backups written (.bak-$STAMP)"

# ---------------------------------------------------------------------------
# 1. Replace _call_c1_legacy with the new live adapter
# ---------------------------------------------------------------------------
python3 - <<'PYEOF'
import re, io

src = open("modality_clients.py").read()

NEW = '''
# ── C1 live contract (Dewdu, 2026-08) ────────────────────────────────────────
# GET /predict/{user_id} now returns a TWO-horizon forecast at +5 and +10 min.
#
#   c1_fusion_score = max(risk_forecast[0], risk_forecast[1]) / 100   -> [0,1]
#
# current_risk_index is a 0-100 DISPLAY scale and must NEVER be the fusion raw
# score. The previous implementation did exactly that, pushing values up to
# 100.0 into a harmoniser whose reference distribution maxes at 0.53 — every
# reading saturated at percentile 1.0.
C1_EXPECTED_HORIZONS = [5, 10]

# C1 publishes confidence: null / confidence_status: "not_calibrated". We do not
# invent a C1 confidence. This is OUR fusion policy default for a component that
# publishes none, recorded as such in the note and detail blob.
C1_POLICY_DEFAULT_CONFIDENCE = 0.5


def _finite(x):
    """float(x) if it is a real finite number, else None. No silent coercion."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _c1_map_status(c1_status: str) -> str:
    s = (c1_status or "").strip().lower()
    if s == "success":
        return "ok"
    if s in ("buffering", "not_calibrated", "warming_up", "calibrating"):
        return "warming_up"
    if s in ("stale", "off_body", "off-body", "offbody", "unusable_signal",
             "unusable-signal", "poor_signal", "poor-signal"):
        return "poor_signal"
    return "error"


def c1_fusion_score(risk_forecast, horizons):
    """(score_0_to_1, error_or_None). Rejects malformed values, never clamps."""
    if not isinstance(risk_forecast, (list, tuple)):
        return None, "risk_forecast missing or not a list"
    if len(risk_forecast) != 2:
        return None, f"risk_forecast must have exactly 2 values, got {len(risk_forecast)}"
    vals = [_finite(v) for v in risk_forecast]
    if any(v is None for v in vals):
        return None, f"risk_forecast has non-finite values: {risk_forecast!r}"
    for v in vals:
        if not (0.0 <= v <= 100.0):
            return None, f"risk_forecast value {v} outside 0-100"
    if horizons is None:
        return None, "forecast_horizons_minutes missing"
    try:
        h = [int(x) for x in horizons]
    except (TypeError, ValueError):
        return None, f"forecast_horizons_minutes not integers: {horizons!r}"
    if h != C1_EXPECTED_HORIZONS:
        return None, f"forecast_horizons_minutes must be {C1_EXPECTED_HORIZONS}, got {h}"
    score = max(vals) / 100.0
    if not (0.0 <= score <= 1.0):
        return None, f"derived fusion score {score} outside 0-1"
    return float(score), None


def _call_c1_legacy(client: httpx.Client, user_id: str) -> ComponentResult:
    """GET /predict/{user_id} — live C1 contract, two horizons at +5 and +10 min."""
    r = client.get(f"{C1_BASE}/predict/{user_id}",
                   headers=_headers(C1_TOKEN), timeout=TIMEOUT_S)
    if r.status_code != 200:
        return ComponentResult(status="error", note=f"C1 HTTP {r.status_code}")
    body = r.json()

    c1_status = body.get("status", "")
    our_status = _c1_map_status(c1_status)

    fc = body.get("risk_forecast")
    horizons = body.get("forecast_horizons_minutes")
    raw_score, err = None, None

    if our_status == "ok":
        raw_score, err = c1_fusion_score(fc, horizons)
        if err:
            # A "success" we cannot trust is an error, not a low score.
            our_status, raw_score = "error", None

    # Non-success -> score MUST be null. Zero means genuine low physiological risk.
    if our_status != "ok":
        raw_score = None

    coverage = _finite(body.get("coverage"))
    if coverage is None or not (0.0 <= coverage <= 1.0):
        coverage = 0.0

    notes = [f"C1 status='{c1_status}'"]
    if err:
        notes.append(f"INVALID C1 response: {err}")
    if our_status == "ok":
        notes.append(f"fusion_score=max({fc[0]},{fc[1]})/100={raw_score:.4f} "
                     f"@ horizons {horizons}min")
    else:
        msg = str(body.get("message", ""))[:100]
        if msg:
            notes.append(msg)
        notes.append("score stored as null (never zero)")
    if body.get("confidence") is None:
        notes.append("confidence: fusion policy default (C1 publishes none)")
    if body.get("confidence_status"):
        notes.append(f"confidence_status={body['confidence_status']}")

    detail = dict(body)
    detail["_c1_fusion_score"] = raw_score
    detail["_c1_fusion_formula"] = "max(risk_forecast)/100"
    detail["_c1_forecast_model_version"] = body.get("forecast_model_version")
    detail["_c1_validation_error"] = err
    detail["_c1_confidence_is_policy_default"] = body.get("confidence") is None

    published_conf = _finite(body.get("confidence"))
    return ComponentResult(
        raw_score=raw_score,
        status=our_status,
        confidence=(published_conf if published_conf is not None
                    else C1_POLICY_DEFAULT_CONFIDENCE),
        coverage=coverage,
        model_version=body.get("model_version"),
        detail=detail,
        note="; ".join(n for n in notes if n)[:300],
        captured_at=_parse_captured_at(
            body.get("captured_at") or body.get("latest_reading_at"),
            dt.datetime.now(dt.timezone.utc)))
'''

start = src.index("def _call_c1_legacy(")
end = src.index("def call_c1(", start)
src = src[:start] + NEW.strip() + "\n\n\n" + src[end:]

if "import math" not in src:
    src = src.replace("import datetime as dt", "import datetime as dt\nimport math", 1)

open("modality_clients.py", "w").write(src)
print("      _call_c1_legacy -> live two-horizon adapter")
PYEOF
echo "[2/5] modality_clients.py patched"

# ---------------------------------------------------------------------------
# 2. main.py — register C1 device id on pairing + expose both scales
# ---------------------------------------------------------------------------
python3 - <<'PYEOF'
src = open("main.py").read()

# (a) On pairing, also register app_user_id as the C1 device id, so the backend
#     queries HF with the id C1 actually knows, not our internal subject_id.
old_pair = '''    if not clash:
        db.add(SubjectAlias(subject_id=code.subject_id, alias_type="app_user_id",
                            alias_value=req.app_user_id))'''
new_pair = '''    if not clash:
        db.add(SubjectAlias(subject_id=code.subject_id, alias_type="app_user_id",
                            alias_value=req.app_user_id))

    # The patient app and the C1 HF Space use the SAME participant id, so the
    # pairing value is also the C1 device id. Registering it here means the
    # backend never queries C1 with an internal subject_id it has never seen.
    c1_alias = db.scalar(select(SubjectAlias).where(
        SubjectAlias.subject_id == code.subject_id,
        SubjectAlias.alias_type == "c1_device_id"))
    if not c1_alias:
        db.add(SubjectAlias(subject_id=code.subject_id,
                            alias_type="c1_device_id",
                            alias_value=req.app_user_id))'''
assert old_pair in src, "pair block not found"
src = src.replace(old_pair, new_pair, 1)

# (b) Physiological ingest returns BOTH scales, explicitly documented.
old_ret = '''    fusion_info = _auto_fuse(db, subject_id, "physio-ingest", debounce=True)
    return {"subject_id": subject_id, "reading_id": row.id,
            "status": result.status, "score": result.raw_score, "note": result.note,
            **fusion_info}'''
new_ret = '''    fusion_info = _auto_fuse(db, subject_id, "physio-ingest", debounce=True)

    # Two scales, named so neither can be mistaken for the other:
    #   c1_fusion_score / composite_score -> 0-1 (canonical, what fusion uses)
    #   final_risk_score                  -> 0-100 (display only, for the app)
    fused = fusion_info.get("fusion") or {}
    composite = fused.get("composite")
    return {"subject_id": subject_id, "reading_id": row.id,
            "status": result.status,
            "score": result.raw_score,
            "c1_fusion_score": result.raw_score,
            "composite_score": composite,
            "final_risk_score": (round(composite * 100, 2)
                                 if composite is not None else None),
            "tier": fused.get("tier"),
            "band": fused.get("band"),
            "scale_note": "c1_fusion_score and composite_score are 0-1; "
                          "final_risk_score is composite_score*100 for display",
            "note": result.note,
            **fusion_info}'''
assert old_ret in src, "physio return block not found"
src = src.replace(old_ret, new_ret, 1)

open("main.py", "w").write(src)
print("      pairing registers c1_device_id; physio ingest returns both scales")
PYEOF
echo "[3/5] main.py patched"

# ---------------------------------------------------------------------------
# 3. Quarantine the stale C1 reference distribution
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 3b. Update the one test that asserted the OLD C1 id-mapping behaviour
# ---------------------------------------------------------------------------
python3 - <<'PYEOF2'
src = open("test_backend.py").read()
old = """check("unmapped modality falls back to our subject_id",
      main._external_id(db, P1, "c1_physiological") == P1)"""
new = """# C1 is auto-registered at pairing time (patient app and C1 Space share the
# participant id), so it must resolve to the paired app_user_id, NOT our UUID.
check("c1 resolves to the paired app_user_id, not our internal UUID",
      main._external_id(db, P1, "c1_physiological") == "phone-aaa")
check("genuinely unmapped modality still falls back to our subject_id",
      main._external_id(db, P1, "c3_clinical_nlp") == P1)"""
if old in src:
    open("test_backend.py","w").write(src.replace(old, new, 1))
    print("      test_backend.py: C1 id-mapping assertion updated")
else:
    print("      test_backend.py: already updated, skipping")
PYEOF2

REF="../fusion_service/reference/c1_physiological.json"
if [ -f "$REF" ]; then
  mv "$REF" "../fusion_service/reference/RETIRED-c1_physiological-old-model.json.txt"
  echo "[4/5] old C1 reference RETIRED (was built on the old model + old score"
  echo "      definition; max 0.53 would saturate every new score at 1.0)."
  echo "      Harmoniser now takes its no-reference path: raw pass-through with"
  echo "      an explicit 'NOT harmonised' note. C1 is provisional until Dewdu"
  echo "      supplies real held-out scores."
else
  echo "[4/5] old C1 reference already absent — nothing to retire"
fi

python3 -c "import ast,sys; ast.parse(open('modality_clients.py').read()); ast.parse(open('main.py').read()); print('[5/5] syntax OK on both files')"
echo ""
echo "=== done. restart:  lsof -ti:8000 | xargs kill -9; ./.venv/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000"
