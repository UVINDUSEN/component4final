# R26-DS-012 · End-to-End Integration Report

**Scope:** the scenario as you described it — AURA registers a patient and mints an ID,
AURA computes the psychological and digital-phenotyping scores, the doctor scans the AURA QR,
the doctor's clinical note produces a third score, fusion runs, and the result appears on the
AURA home page, the doctor dashboard, and the explainability pages.

**Method:** cloned all three repos at HEAD, read the backend, both Flutter apps and the fusion
service, ran the existing 145-test backend suite, then wrote a new test that walks your scenario
literally, in your stated order.

---

## Headline

**The scenario did not work end to end.** The first run of the scenario test scored
**5 passed, 14 failed**. After the backend fixes in this bundle it scores **24 passed, 1 failed**,
and the one remaining failure is a two-line Flutter change I've written for you.

The existing 145-test suite still passes unchanged, so none of this was bought by loosening
an existing guarantee.

There is also one issue the fixes deliberately do **not** touch, because it needs data rather
than code, and it is currently the biggest threat to the demo. It's P0-3 below.

---

## The root cause

Everything except three independent bugs traced back to a single thing:
**your backend implements clinician-first enrolment; your product is patient-first.**

The backend's enrolment sequence is: clinician POSTs an MRN to `/v1/subjects` → backend mints a
pairing code → patient redeems it at `/v1/subjects/pair`, which creates the `app_user_id` alias.
That alias is what `/v1/ingest/contextual` and `/v1/ingest/physiological` resolve a patient by.

In your scenario nobody ever redeems a pairing code. AURA mints its own ID and the doctor arrives
later. So the `app_user_id` alias never exists, and:

```
POST /v1/ingest/contextual     -> 404 no subject for that app_user_id
POST /v1/ingest/physiological  -> 404 no subject for that app_user_id
```

Both patient-side scores were unreachable. Only the clinician's C3 note landed. The gate needs two
usable modalities and found one, so it blocked:

```
band=GREY  reason="insufficient evidence: 1 usable modality, need 2"
```

and every display surface in both apps correctly showed nothing. The cascade — no composite on the
AURA home page, no composite on the dashboard, no contributions on the XAI page, no conformal set —
was one identity bug wearing nine costumes.

---

## Findings

### P0-1 · Enrolment direction inverted — **fixed in backend, needs app wiring**

Two new routes, both idempotent, both refusing rather than merging when an identifier already
belongs to someone else:

- `POST /v1/subjects/self` — AURA claims its own `subject_id` at registration.
- `POST /v1/subjects/attach` — the clinician binds a scanned AURA ID to a clinical record,
  optionally adding a ward MRN, on the **same** subject the patient already created.

The clinician-first flow is untouched, so both orders now converge on one `subject_id`.

The subtle failure this prevents is worth stating at viva: the doctor app currently takes the
scanned participant ID and passes it as the **MRN** to `/v1/subjects`. The backend hashes it and
mints a *second* subject. The patient's C1/C4 readings and the clinician's C3 note then land on
two different rows, and each side displays a perfectly healthy-looking composite computed over
half the evidence. `/v1/subjects/attach` exists specifically to make that impossible.

### P0-2 · The doctor app cannot scan the QR AURA actually renders — **patch supplied**

AURA encodes `clinanx://patient/P_8A0840A798B81072`.
`ScanPatientIdScreen._onDetect` uppercases the raw value and tests it against `^P_[A-F0-9]{16}$`,
so it sees `CLINANX://PATIENT/P_8A0840A798B81072`, fails, and tells the clinician
*"That code is not an Aura Participant ID."*

**Every genuine AURA code is rejected.** Two teams built to two different ideas of the payload and
neither is wrong alone.

`doctor_app/aura_qr.dart` fixes the decoder rather than the encoder: the scheme prefix is what makes
the QR a deep link, and changing AURA would break any ward device already running the current build.
Ward barcodes are still rejected — the shape check is unchanged, it just runs on the right substring.

### P0-3 · Reference distributions saturate the composite — **NOT FIXED, needs real data**

All three files under `fusion_service/reference/` are still self-declared placeholders. Measured
from the actual files:

| modality | placeholder max | any live score above this harmonises to **1.0** |
|---|---|---|
| `c1_physiological` | 0.529 | raw 0.7 → percentile **1.000** |
| `c4_demographic` (your DCAR) | 0.452 | raw 0.5 → percentile **1.000** |
| `c3_clinical_nlp` | 0.981 | behaves sensibly across the range |

In the end-to-end run, C1 raw 0.72 harmonised to **1.0** and C4 raw 0.55 harmonised to **1.0**.
Two of three channels pinned at the ceiling, producing composite **0.984 → RED**.

This is not a fusion arithmetic error — the arithmetic is correct. It is that the percentile
harmonisation is calibrated against invented distributions whose range is far narrower than the
live scores. **The system will read RED for most plausible patients**, and a panel that demos two
different patients will see it. Your live composites of 0.82–0.86 are the same effect.

Getting real held-out score vectors from Dewdu (C1) and Dulhara (C3), and generating your own from
the DCAR held-out fold, is the single highest-value remaining task. If they don't arrive in time,
say so explicitly in the viva rather than presenting the band as clinical — the honest framing is
that the fusion mechanism is validated and the clinical scale is pending calibration data.

### P0-4 · AURA never sends its scores to the backend — **service supplied**

`ApiService` posts physiological features to Dewdu's HF Space (`/ingest`) and reads `/predict`, but
the only central-backend route it calls is `/v1/subjects/pair`. Nothing ever posts to
`/v1/ingest/contextual` or `/v1/ingest/physiological`.

`ApiService.sendToFusionModel` points at `https://PLACEHOLDER_FUSION_ENDPOINT.hf.space/fuse`, which
exists in no service in any repo, and short-circuits before making a request. Delete it — a
placeholder that always returns `{'success': false}` looks like a network fault forever.

`patient_app/fusion_risk_service.dart` provides `ensureEnrolled`, `submitIntake` (GAD-7 +
demographics → C4), `submitPhysioWindow` (→ C1) and `latestRisk` for the home page.

### P0-5 · The AURA home card shows a local number that looks like the composite

`home_page.dart::_overallRisk` reads `AnxietyFeedbackService().latestFusionRisk` and, when that is
null — which is always, because it is fed by the dead placeholder above — falls back to
`_lastReading!.riskScore`, the chest-strap-only score.

So the card labelled *"combined physiological + phenotyping risk"* has never once displayed a
fusion composite. It displays C1 alone. That is a research-integrity problem, not just a wiring gap.

**And there is a scale bug waiting behind it.** `_labelForScore` thresholds on 20 / 45 / 70 — a
0–100 scale. The backend composite is 0–1. Wire the two together naively and the composite of
**0.984 renders as "Low" in green.** Multiply by 100 at the boundary, or better, use the backend's
`band` directly and stop deriving severity client-side.

### P1-6 · Doctor timeline omitted three fields the app's model expects — **fixed**

`doctor_timeline()` did not return `fusion_result_id`, `modalities_used` or `renormalised`.
`fusion_result_id` is the one that matters: `POST /v1/verdict` keys the clinician's HITL judgement
to it, so without it the app can render a composite it can never record a verdict against — which
silently disables the entire conformal calibration loop.

### P1-7 · The Ask CARE tab called a route that does not exist — **fixed**

`CentralBackendGateway.askEvidence` POSTs to `/v1/evidence/ask`. The backend served no such route,
so the whole tab returned 404 and reported it as *"CARE-AnxRAG unavailable"* — indistinguishable
from the service genuinely being down. Added as a global, non-patient-scoped RAG query.

### P2-8 · "Three risk scores" needs a precise mapping before viva

Your scenario says AURA produces two scores and the doctor one. In the code the mapping is:

| scenario term | component | in fusion? |
|---|---|---|
| psychological (GAD-7 + demographics) | C4 · DCAR | yes |
| digital phenotyping | C1 · physiological | yes |
| clinical notes | C3 · TC-WPN | yes |
| behavioural graph | C2 | **no — excluded by pre-registered rule** |

If anyone on the team means C2 by "digital phenotyping", the scenario and the implementation
disagree and you need to settle it now. The three locks holding C2 out are intact and verified —
its fusion weight came back **exactly 0.0** in the live run.

Also note the gate fires at **two** usable modalities, not three. That is deliberate and correct:
a patient with C1+C4 and no note yet would otherwise sit at GREY indefinitely. Be ready to say
"the composite is defined from two streams onward, and the timeline reports `modalities_used` so
the clinician always knows which they're looking at" — don't let a panel discover it first.

---

## Evidence

```
existing suite      145 passed, 0 failed   (unchanged after patches)
scenario test       BEFORE:  5 passed, 14 failed
                    AFTER :  24 passed,  1 failed
remaining failure   the QR regex, in Flutter — patch supplied
```

Live run after fixes:

```
AURA self-enrols                  -> subject 0a810975-62a3-4b9c-9231-e2ceee49b53a
AURA intake (GAD-7 total 14)      -> C4 0.55
AURA physio window                -> C1 0.72
doctor attaches scanned QR + MRN  -> SAME subject, both aliases resolve
doctor note                       -> C3 0.83, auto-triggered fusion
composite 0.984  tier High  band RED  modalities_used=3
weights  c1 0.2583 | c2 0.0 | c3 0.5048 | c4 0.2368
```

---

## What to run

```bash
cd /Users/uvindusenevirathne/Downloads/component4final/central_backend/
cp main.py main.py.backup
# copy the new main.py from this bundle over yours, then:
BACKEND_API_TOKEN="" MRN_PEPPER=t ./.venv/bin/python3 test_backend.py
BACKEND_API_TOKEN="" MRN_PEPPER=t ./.venv/bin/python3 e2e_scenario.py
```

Expect 145/145 and 24/25. The 25th passes once `aura_qr.dart` is wired into
`ScanPatientIdScreen._onDetect`.

**Important:** the backend in `dulhara79/R26-DS-012` under
`Multimodal Risk Fusion.../c4_final/central_backend/` is what I patched. If your local copy has
diverged, diff before overwriting — `main.py.patch` in this bundle applies the changes surgically.

---

## Remaining, by owner

**You**

1. Get real held-out scores for C1, C3, C4 and regenerate the reference files (P0-3). Highest value.
2. Wire `aura_qr.dart` into the doctor app's scanner and switch enrolment to `/v1/subjects/attach`.
3. Wire `fusion_risk_service.dart` into AURA: `ensureEnrolled()` at registration, `submitIntake()`
   after GAD-7, `submitPhysioWindow()` from the sensor loop, `latestRisk()` on the home page.
4. Fix the 0–100 vs 0–1 scale on the AURA home card; render `band` rather than re-deriving it.
5. Delete `ApiService.sendToFusionModel`.
6. Verify the CARE-AnxRAG retrieval architecture before claiming a vector database.

**Dulhara** — the `DR001` credentials on the TC-WPN Space; C3 held-out scores.

**Dewdu** — C1 held-out scores; confirm AURA can carry the backend token.

**Team** — settle whether "digital phenotyping" means C1 or C2, and resolve the component
numbering discrepancy between the README and how you've all been working.
