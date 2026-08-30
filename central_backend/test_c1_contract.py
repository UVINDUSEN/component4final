import sys, os, datetime as dt
sys.path.insert(0,".")
os.environ["C1_URL"]="https://dewdu-physiological-anxiety-escalation.hf.space"
os.environ["MRN_PEPPER"]="t"; os.environ["BACKEND_API_TOKEN"]=""
import httpx, modality_clients as mc
P=F=0
def ck(n,c):
    global P,F
    if c: P+=1; print(f"  PASS  {n}")
    else: F+=1; print(f"  FAIL  {n}")

def mock(body, code=200):
    def h(req): return httpx.Response(code, json=body)
    return httpx.Client(transport=httpx.MockTransport(h))

GOOD={"status":"success","score":0.42,"current_risk_index":42.0,
 "risk_forecast":[55.0,64.0],"forecast_horizons_minutes":[5,10],"coverage":1.0,
 "captured_at":"2026-08-29T11:59:00Z","model_version":"c1-unmasked-lstm-ae-wesad-v2",
 "forecast_model_version":"c1-direct-ridge-score-forecast-wesad-v5",
 "confidence":None,"confidence_status":"not_calibrated"}

print("\n=== Live call through call_c1 (mocked Space) ===")
r=mc.call_c1("P_8A0840A798B81072", client=mock(GOOD))
ck(f"status ok (got {r.status})", r.status=="ok")
ck(f"raw_score 0.64 (got {r.raw_score})", abs(r.raw_score-0.64)<1e-9)
ck("NOT 42.0 (current_risk_index)", r.raw_score!=42.0)
ck("NOT 0.42 (score field)", abs(r.raw_score-0.42)>1e-6)
ck(f"coverage 1.0 (got {r.coverage})", r.coverage==1.0)
ck("model_version kept", r.model_version=="c1-unmasked-lstm-ae-wesad-v2")
ck("forecast_model_version kept", r.detail["_c1_forecast_model_version"]=="c1-direct-ridge-score-forecast-wesad-v5")
ck(f"captured_at = C1 time not now (got {r.captured_at})", r.captured_at.hour==11 and r.captured_at.minute==59)
ck("forecast preserved in audit detail", r.detail["risk_forecast"]==[55.0,64.0])
ck("horizons preserved in audit detail", r.detail["forecast_horizons_minutes"]==[5,10])
ck("confidence flagged as policy default", r.detail["_c1_confidence_is_policy_default"] is True)
ck("note says policy default", "policy default" in r.note)

print("\n=== buffering / stale / errors ===")
for st in ("buffering","not_calibrated","stale","off_body","unusable_signal"):
    b=dict(GOOD); b["status"]=st
    r=mc.call_c1("u",client=mock(b))
    ck(f"{st}: score None", r.raw_score is None)
b=dict(GOOD); b["status"]="buffering"
ck("buffering -> warming_up", mc.call_c1("u",client=mock(b)).status=="warming_up")
b=dict(GOOD); b["status"]="stale"
ck("stale -> poor_signal", mc.call_c1("u",client=mock(b)).status=="poor_signal")

print("\n=== malformed rejected ===")
for bad,lbl in (([55.0]*10,"legacy 10-step"),([120.0,64.0],"out of range"),(None,"missing")):
    b=dict(GOOD); b["risk_forecast"]=bad
    r=mc.call_c1("u",client=mock(b))
    ck(f"{lbl}: error + None", r.status=="error" and r.raw_score is None)
b=dict(GOOD); b["forecast_horizons_minutes"]=[5,15]
ck("horizons [5,15] rejected", mc.call_c1("u",client=mock(b)).status=="error")
ck("HTTP 500 -> error", mc.call_c1("u",client=mock({},500)).status=="error")

print("\n=== harmoniser no longer saturates ===")
sys.path.insert(0,"../fusion_service")
from harmonise import Harmoniser
from pathlib import Path
h=Harmoniser(Path("../fusion_service/reference"))
out=h.harmonise("c1_physiological",0.64)
ck(f"0.64 passes through as 0.64 not 1.0 (got {out.value})", abs(out.value-0.64)<1e-9)
ck("honest 'not harmonised' note present", "NOT valid" in (out.note or ""))
vals=[h.harmonise("c1_physiological",v).value for v in (0.55,0.60,0.64,0.70,0.80)]
ck(f"5 distinct values stay distinct (got {vals})", len(set(vals))==5)

print(f"\n{'='*52}\n  {P} passed, {F} failed\n{'='*52}")
sys.exit(1 if F else 0)
