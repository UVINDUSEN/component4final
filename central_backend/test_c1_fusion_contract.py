"""C1 contract tests for the fusion_service side."""
import sys, math
sys.path.insert(0, ".")
from clients import to_reading
P=F=0
def ck(n,c):
    global P,F
    if c: P+=1; print(f"  PASS  {n}")
    else: F+=1; print(f"  FAIL  {n}")

LIVE={"status":"success","score":0.42,"current_risk_index":42.0,
 "risk_forecast":[55.0,64.0],"forecast_horizons_minutes":[5,10],"coverage":1.0,
 "captured_at":"2026-08-29T11:59:00Z","model_version":"c1-unmasked-lstm-ae-wesad-v2",
 "forecast_model_version":"c1-direct-ridge-score-forecast-wesad-v5",
 "confidence":None,"confidence_status":"not_calibrated"}

print("\n=== C1 read from forecast, not from 'score' ===")
r=to_reading("c1_physiological", LIVE)
ck(f"score 0.64 (got {r.score})", abs(r.score-0.64)<1e-9)
ck("NOT 0.42 (the 'score' field)", abs(r.score-0.42)>1e-6)
ck("NOT 42.0 (current_risk_index)", r.score!=42.0)
ck("available", r.available is True)
ck("coverage real", r.coverage==1.0)
ck("forecast model version in note", "ridge" in (r.note or ""))

print("\n=== validation rejects, never clamps ===")
for bad,lbl in (([55.0]*10,"legacy 10-step"),([120.0,64.0],"out of 0-100"),
                ([float('nan'),64.0],"NaN"),("x","not a list"),([55.0],"one value")):
    b=dict(LIVE); b["risk_forecast"]=bad
    r=to_reading("c1_physiological", b)
    ck(f"{lbl} -> unavailable", r.available is False and r.score is None)
b=dict(LIVE); b["forecast_horizons_minutes"]=[5,15]
ck("horizons [5,15] rejected", to_reading("c1_physiological",b).available is False)
for st in ("buffering","not_calibrated","stale"):
    b=dict(LIVE); b["status"]=st
    ck(f"status {st} -> no fusable score", to_reading("c1_physiological",b).available is False)

print("\n=== other modalities: 0-1 bound enforced ===")
ck("c3 valid 0.73 accepted", to_reading("c3_clinical_nlp",{"score":0.73}).score==0.73)
r=to_reading("c3_clinical_nlp",{"score":73.0})
ck("c3 out-of-range 73.0 REJECTED not clamped", r.available is False and r.score is None)
ck("c4 valid accepted", to_reading("c4_demographic",{"score":0.31}).score==0.31)

print("\n=== PhysioTick schema bound ===")
from app import PhysioTick
from pydantic import ValidationError
ck("0.64 accepted", PhysioTick(mrn="m",score=0.64).score==0.64)
try:
    PhysioTick(mrn="m",score=64.0); ck("64.0 rejected",False)
except ValidationError: ck("64.0 (0-100 by mistake) rejected with 422",True)
try:
    PhysioTick(mrn="m",score=-0.1); ck("negative rejected",False)
except ValidationError: ck("negative rejected",True)
ck("0.0 boundary ok", PhysioTick(mrn="m",score=0.0).score==0.0)
ck("1.0 boundary ok", PhysioTick(mrn="m",score=1.0).score==1.0)

print(f"\n{'='*52}\n  {P} passed, {F} failed\n{'='*52}")
sys.exit(1 if F else 0)
