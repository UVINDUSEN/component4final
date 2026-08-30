#!/bin/bash
# ============================================================================
# C1 CONTRACT MIGRATION — PART 2: fusion_service
# Run from: fusion_service/
# ============================================================================
set -e
echo "=== C1 fusion_service migration ==="

if [ ! -f app.py ] || [ ! -f clients.py ]; then
  echo "ERROR: run this from fusion_service/ (app.py + clients.py not found)"; exit 1
fi

STAMP=$(date +%Y%m%d-%H%M%S)
cp app.py "app.py.bak-$STAMP"
cp clients.py "clients.py.bak-$STAMP"
echo "[1/4] backups written (.bak-$STAMP)"

# ---------------------------------------------------------------------------
# 1. app.py — enforce 0..1 on every score that enters fusion
# ---------------------------------------------------------------------------
python3 - <<'PYEOF'
src = open("app.py").read()

old_tick = '''class PhysioTick(BaseModel):
    mrn: str
    score: float
    confidence: float = 0.7
    coverage: float = 1.0
    captured_at: Optional[datetime] = None'''
new_tick = '''class PhysioTick(BaseModel):
    """C1 minute tick.

    `score` MUST already be the C1 fusion score on the 0-1 scale:
        max(risk_forecast[+5min], risk_forecast[+10min]) / 100
    It is NOT current_risk_index and NOT the 0-100 display scale. The bound is
    enforced here rather than clamped, so a 0-100 value posted by mistake fails
    loudly with a 422 instead of silently saturating the harmoniser at 1.0.
    """
    mrn: str
    score: float = Field(..., ge=0.0, le=1.0,
                         description="C1 fusion score, 0-1: max(+5,+10 forecast)/100")
    confidence: float = Field(0.7, ge=0.0, le=1.0)
    coverage: float = Field(1.0, ge=0.0, le=1.0)
    captured_at: Optional[datetime] = None'''
assert old_tick in src, "PhysioTick not found"
src = src.replace(old_tick, new_tick, 1)

old_mc = '''class ManualComponent(BaseModel):
    score: Optional[float] = None
    available: bool = True
    confidence: float = 0.5
    coverage: float = 1.0
    captured_at: Optional[datetime] = None'''
new_mc = '''class ManualComponent(BaseModel):
    # Every modality score entering fusion is a 0-1 quantity. Bounded, not
    # clamped: an out-of-range value is a contract violation, not a big number.
    score: Optional[float] = Field(None, ge=0.0, le=1.0)
    available: bool = True
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    coverage: float = Field(1.0, ge=0.0, le=1.0)
    captured_at: Optional[datetime] = None'''
assert old_mc in src, "ManualComponent not found"
src = src.replace(old_mc, new_mc, 1)

open("app.py", "w").write(src)
print("      PhysioTick.score + ManualComponent.score bounded to 0-1")
PYEOF
echo "[2/4] app.py patched"

# ---------------------------------------------------------------------------
# 2. clients.py — C1 must be read from risk_forecast, never from "score"
# ---------------------------------------------------------------------------
python3 - <<'PYEOF'
src = open("clients.py").read()

old = '''    score = None
    for key in ("score", "risk_score", "value", "probability", "risk"):
        if isinstance(body.get(key), (int, float)):
            score = float(body[key])
            break
    if score is None:
        return Reading(available=False, note=f"no numeric score field in {list(body)[:6]}")'''

new = '''    # ── C1 SPECIAL CASE ──────────────────────────────────────────────────────
    # The live C1 contract publishes BOTH `score` (current anomaly, 0-1) and
    # `risk_forecast` (two future values, 0-100). Only the forecast peak enters
    # fusion:  max(risk_forecast) / 100.  Reading `score` here would silently
    # fuse the CURRENT anomaly instead of the PREDICTED peak — a different
    # quantity that happens to be the same shape, which is why the generic
    # score-hunting loop below must never see a C1 payload first.
    if modality == "c1_physiological" and "risk_forecast" in body:
        fc = body.get("risk_forecast")
        horizons = body.get("forecast_horizons_minutes")
        if not isinstance(fc, (list, tuple)) or len(fc) != 2:
            return Reading(available=False,
                           note=f"C1 risk_forecast must have exactly 2 values, got {fc!r}")
        try:
            vals = [float(v) for v in fc]
        except (TypeError, ValueError):
            return Reading(available=False, note=f"C1 risk_forecast not numeric: {fc!r}")
        if not all(math.isfinite(v) and 0.0 <= v <= 100.0 for v in vals):
            return Reading(available=False,
                           note=f"C1 risk_forecast values outside 0-100: {vals}")
        if horizons is not None and [int(h) for h in horizons] != [5, 10]:
            return Reading(available=False,
                           note=f"C1 horizons must be [5, 10], got {horizons}")
        if str(body.get("status", "success")).lower() != "success":
            return Reading(available=False,
                           note=f"C1 status={body.get('status')} — no fusable score")
        return Reading(
            score=max(vals) / 100.0,
            available=True,
            confidence=float(body.get("confidence") or 0.5),
            coverage=float(body.get("coverage", 1.0)),
            captured_at=_parse_time(body.get("captured_at")) or datetime.now(timezone.utc),
            note=f"max({vals[0]},{vals[1]})/100 | {body.get('forecast_model_version')}",
        )

    score = None
    for key in ("score", "risk_score", "value", "probability", "risk"):
        if isinstance(body.get(key), (int, float)):
            score = float(body[key])
            break
    if score is None:
        return Reading(available=False, note=f"no numeric score field in {list(body)[:6]}")

    # Any modality score entering fusion is a 0-1 quantity. Reject, do not clamp.
    if not math.isfinite(score) or not (0.0 <= score <= 1.0):
        return Reading(available=False,
                       note=f"{modality} score {score} outside 0-1 — rejected, not clamped")'''

assert old in src, "score-hunting loop not found"
src = src.replace(old, new, 1)

if "import math" not in src:
    src = src.replace("import os", "import math\nimport os", 1)

open("clients.py", "w").write(src)
print("      to_reading: C1 read from risk_forecast; all scores bounded 0-1")
PYEOF
echo "[3/4] clients.py patched"

python3 -c "import ast; ast.parse(open('app.py').read()); ast.parse(open('clients.py').read()); print('[4/4] syntax OK on both files')"
echo ""
echo "=== fusion_service done ==="
