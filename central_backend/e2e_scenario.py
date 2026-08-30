"""
E2E SCENARIO TEST — replicates the described clinical flow literally.

  1. Patient installs AURA, registers -> AURA mints participant id P_xxxx
  2. AURA collects psychological (GAD-7 + demographics) -> C4 score
  3. AURA collects digital phenotyping -> C1 / C2 score
  4. Doctor scans AURA QR -> doctor app attaches to that patient id
  5. Doctor writes clinical note -> C3 score
  6. Fusion produces composite
  7. Composite renders on AURA home + doctor dashboard + XAI page

Run:  MRN_PEPPER=t BACKEND_API_TOKEN="" python3 e2e_scenario.py
"""
import datetime as dt
import os
import sys

os.environ.setdefault("MRN_PEPPER", "t")
os.environ.setdefault("BACKEND_API_TOKEN", "")
os.environ.setdefault("DATABASE_URL", "sqlite:///./e2e_scenario.db")

for stale in ("e2e_scenario.db",):
    if os.path.exists(stale):
        os.remove(stale)

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import modality_clients as mc  # noqa: E402

client = TestClient(main.app)

NOW = dt.datetime.now(dt.timezone.utc)


def stub_c1(subject_id, window=None, client=None):
    return mc.ComponentResult(raw_score=0.72, status="ok", confidence=0.81,
                              coverage=1.0, captured_at=NOW,
                              model_version="c1-lstm-ae-v1.3", note="stub")


def stub_c2(subject_external_id, payload=None, client=None):
    return mc.ComponentResult(raw_score=None, status="not_validated",
                              confidence=0.0, coverage=1.0, captured_at=NOW,
                              model_version="c2-gatv2", note="excluded",
                              detail={"behavioral_vulnerability_score": 0.31})


def stub_c3(note_text, note_type="progress", anxiety_support=None,
            control_support=None, client=None, **kw):
    return mc.ComponentResult(raw_score=0.83, status="ok", confidence=0.77,
                              coverage=1.0, captured_at=NOW,
                              model_version="tcwpn-v2", note="stub")


def stub_c4(subject_id, demographics, client=None):
    return mc.ComponentResult(raw_score=0.55, status="ok", confidence=0.62,
                              coverage=1.0, captured_at=NOW,
                              model_version="dcar-v1", note="stub")


mc.call_c1, mc.call_c2, mc.call_c3, mc.call_c4 = stub_c1, stub_c2, stub_c3, stub_c4
main.mc.call_c1, main.mc.call_c2, main.mc.call_c3, main.mc.call_c4 = (
    stub_c1, stub_c2, stub_c3, stub_c4)

PASS, FAIL = [], []


def step(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS ' if ok else 'FAIL '} {name}" + (f"  — {detail}" if detail else ""))


AURA_PARTICIPANT_ID = "P_8A0840A798B81072"   # what AURA mints at registration
AURA_QR_PAYLOAD = f"clinanx://patient/{AURA_PARTICIPANT_ID}"

print("\n" + "=" * 74)
print("  STEP 1-3 · PATIENT SIDE FIRST (AURA registers, then sends scores)")
print("=" * 74)

r = client.post("/v1/subjects/self", json={"app_user_id": AURA_PARTICIPANT_ID})
step("AURA self-enrols at registration", r.status_code == 200,
     f"HTTP {r.status_code}: {r.text[:110]}")
aura_subject = r.json().get("subject_id") if r.status_code == 200 else None
print(f"        AURA subject_id = {aura_subject}")

r2 = client.post("/v1/subjects/self", json={"app_user_id": AURA_PARTICIPANT_ID})
step("re-registering is idempotent (no forked patient)",
     r2.status_code == 200 and r2.json().get("subject_id") == aura_subject
     and r2.json().get("created") is False)

# The scenario says AURA sends its scores BEFORE the doctor is involved.
r = client.post("/v1/ingest/contextual", json={
    "app_user_id": AURA_PARTICIPANT_ID,
    "gender": "female", "age": 29, "edu": "tertiary",
    "gad7_items": [2, 2, 3, 2, 1, 2, 2],
})
step("AURA can submit psychological intake before doctor enrols",
     r.status_code == 200, f"HTTP {r.status_code}: {r.text[:110]}")

r = client.post("/v1/ingest/physiological", json={
    "app_user_id": AURA_PARTICIPANT_ID,
    "window_start": NOW.isoformat(), "window_end": NOW.isoformat(),
    "sampling_hz": 1,
    "features": {"mean_hr": 88.0, "sdnn": 31.0, "rmssd": 24.0},
})
step("AURA can submit digital-phenotyping window before doctor enrols",
     r.status_code == 200, f"HTTP {r.status_code}: {r.text[:110]}")

print("\n" + "=" * 74)
print("  STEP 4 · DOCTOR SCANS THE AURA QR")
print("=" * 74)

import re  # noqa: E402
scanner_regex = re.compile(r"^P_[A-F0-9]{16}$")
scanned_raw = AURA_QR_PAYLOAD.strip().upper()          # exactly what the app does
step("CURRENT scanner regex accepts the QR payload AURA renders",
     bool(scanner_regex.match(scanned_raw)),
     f"AURA emits {AURA_QR_PAYLOAD!r}, scanner uppercases to {scanned_raw!r}")

# The proposed decoder: strip the clinanx:// scheme, then validate.
def decode_aura_qr(raw):
    v = raw.trim() if hasattr(raw, "trim") else raw.strip()
    m = re.match(r"^clinanx://patient/(.+)$", v, re.IGNORECASE)
    if m:
        v = m.group(1)
    v = v.strip().upper()
    return v if scanner_regex.match(v) else None

decoded = decode_aura_qr(AURA_QR_PAYLOAD)
step("PATCHED decoder recovers the participant id from the QR",
     decoded == AURA_PARTICIPANT_ID, f"decoded={decoded!r}")
step("PATCHED decoder still rejects a random ward barcode",
     decode_aura_qr("0123456789012") is None)

r = client.post("/v1/subjects/attach", json={
    "app_user_id": decoded, "mrn": "NHSL-4417", "enrolled_by": "DR001"})
step("doctor attaches the scanned id to a clinical record",
     r.status_code == 200, f"HTTP {r.status_code}: {r.text[:110]}")
subject_id = r.json().get("subject_id") if r.status_code == 200 else None
step("attach joined the SAME subject the patient created — not a second one",
     subject_id is not None and subject_id == aura_subject,
     f"aura={aura_subject} doctor={subject_id}")
print(f"        subject_id = {subject_id}")

if subject_id:
    r = client.get(f"/v1/subjects/resolve?app_user_id={AURA_PARTICIPANT_ID}")
    step("backend resolves the AURA participant id to that subject",
         r.status_code == 200 and r.json().get("subject_id") == subject_id,
         f"HTTP {r.status_code}: {r.text[:110]}")
    r = client.get("/v1/subjects/resolve?mrn=NHSL-4417")
    step("backend resolves the ward MRN to the SAME subject",
         r.status_code == 200 and r.json().get("subject_id") == subject_id)

print("\n" + "=" * 74)
print("  STEP 5 · DOCTOR SUBMITS THE CLINICAL NOTE")
print("=" * 74)

if subject_id:
    r = client.post("/v1/clinical-notes", json={
        "subject_id": subject_id,
        "note_text": "Patient reports persistent worry, poor sleep, "
                     "avoidance of social contact over three weeks.",
        "note_type": "progress",
        "note_date": NOW.isoformat(),
        "visit_count": 3,
    })
    step("clinical note accepted and scored", r.status_code == 200,
         f"HTTP {r.status_code}: {r.text[:110]}")
    body = r.json() if r.status_code == 200 else {}
    step("note ingest auto-triggered fusion", body.get("fusion_triggered") is True,
         str(body.get("fusion") or body.get("fusion_error") or
             body.get("fusion_skipped_reason"))[:120])

print("\n" + "=" * 74)
print("  STEP 6 · FUSION OVER THE THREE SCORES")
print("=" * 74)

if subject_id:
    r = client.post("/v1/fusion/run", json={"subject_id": subject_id,
                                            "trigger": "e2e"})
    f = r.json() if r.status_code == 200 else {}
    step("fusion runs", r.status_code == 200, f"HTTP {r.status_code}")
    step("fusion produced a composite (not gate-blocked)",
         f.get("composite") is not None,
         f"band={f.get('band')} reason={str(f.get('reason'))[:90]}")
    step("all three intended modalities contributed",
         f.get("modalities_used") == 3,
         f"modalities_used={f.get('modalities_used')} "
         f"weights={list((f.get('weights') or {}).keys())}")
    print(f"        composite={f.get('composite')} tier={f.get('tier')} "
          f"band={f.get('band')}")

print("\n" + "=" * 74)
print("  STEP 7 · DISPLAY SURFACES")
print("=" * 74)

if subject_id:
    r = client.get(f"/v1/patients/{subject_id}/risk")
    step("AURA home-page risk endpoint returns a composite",
         r.status_code == 200 and r.json().get("composite") is not None,
         f"HTTP {r.status_code}: {r.text[:110]}")

    r = client.get(f"/v1/doctor/patients/{subject_id}/timeline")
    t = r.json() if r.status_code == 200 else {}
    step("doctor dashboard timeline returns a composite",
         r.status_code == 200 and t.get("composite") is not None)
    step("XAI page has per-modality contributions",
         bool(t.get("contributions")), f"keys={list((t.get('contributions') or {}))}")
    step("XAI page has gate decision", t.get("gate") is not None)
    step("XAI page has conformal set", t.get("conformal") is not None)
    for field in ("fusion_result_id", "modalities_used", "renormalised"):
        step(f"timeline exposes '{field}' (doctor app model expects it)",
             field in t)

    r = client.post("/v1/evidence/ask", json={"question": "stepped care?"})
    step("Ask CARE tab route /v1/evidence/ask exists",
         r.status_code != 404, f"HTTP {r.status_code}")

print("\n" + "=" * 74)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("\n  FAILING:")
    for f in FAIL:
        print(f"    · {f}")
print("=" * 74 + "\n")
sys.exit(0)
