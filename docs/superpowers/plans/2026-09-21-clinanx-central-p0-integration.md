# ClinAnx ↔ Central Backend P0 Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the frozen clinician authentication, assignment-scoped assessment/dashboard, physiological forecast, and persistent AttentionEvent contracts without changing fusion mathematics or breaking `POST /v1/clinical-notes -> C3 -> fusion`.

**Architecture:** The Central Backend remains the only authority for clinician identity, assignments, current FusionResult, ForecastResult, and AttentionEvent lifecycle. ClinAnx becomes a typed presentation client of those persisted server records. The patient app keeps its working current-risk flow during this slice and receives only the backward-compatible authoritative `fusion_result_id` addition; patient-scoped event authentication is not invented here.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy 2, SQLite/PostgreSQL, PyJWT, Flutter/Dart, `flutter_test`.

**Spec:** `R26-DS-012_System_Integration_Implementation_Handbook.pdf`, `R26-DS-012_14-Day_Implementation_Sprint_Plan_and_Team_Checklists.pdf`, and `dulhara79/tcwpn_mobile_app/docs/integration/PHASE6_ATTENTION_EVENT_API_CONTRACT.md`.

## Global Constraints

- Preserve the current fusion gate, harmonisation, weights, thresholds, C2 exclusion, C3 adapter, C4 adapter, CARE-X, and CARE-AnxRAG behavior.
- Preserve `POST /v1/clinical-notes` and its immediate C3-to-fusion behavior.
- Current assessment and forecast are separate persisted objects.
- Forecast scope is exactly `physiological` for the current C1-led forecast.
- Missing, stale, failed, excluded, or insufficient data never becomes score `0`, tier `Low`, or band `GREEN`.
- `fusion_result_id` is the authoritative assessment identity in clinician and patient projections.
- Clinician patient/event access is enforced by active server assignment.
- Event lifecycle is exactly `OPEN -> ACKNOWLEDGED -> RESOLVED`.
- ACK and RESOLVE requests accept `{}`; actor and timestamp come from the JWT.
- A conflicting lifecycle mutation returns `409` and never overwrites canonical state.
- One escalation episode creates at most one AttentionEvent.
- No privileged shared backend token is added to either Flutter release build.
- No `/v1/subjects/attach` route is added; identity resolution remains `/v1/subjects/resolve`.

## Verified Baseline

- `UVINDUSEN/component4final@a1ceef8` has shared-token auth, subjects, readings, FusionResult, clinical-note fusion, patient `/risk`, and clinician `/timeline`; it has no clinician, assignment, ForecastResult, AttentionEvent, login, `/v1/me`, dashboard, roster, latest-assessment, history, or data-quality contract.
- `dulhara79/tcwpn_mobile_app@65a9183` already contains typed assessment/event models and the complete Phase 6 AttentionEvent client. Event calls are live-wired; dashboard/latest-assessment/auth repositories are intentionally blocked.
- Draft ClinAnx PR #36 contains useful tested live-adapter work, but its statement that the backend routes already exist is false against backend `main`. Reuse its code only after the backend contract tests pass, and replace its composed dashboard with the frozen aggregate route.
- `DewduSendanayake/anxiety_mobile_app@2d7cb59` consumes `/v1/patients/{subject_id}/risk` and independently polls C1. It must remain working while the clinician P0 backend is introduced.
- `feat/demographic-c4-inputs` changes only one patient test and belongs to the separate C4 demographic effort; do not mix it into this P0 branch.

## Review Focus

1. Valid JWT with no assignment: every patient/event route returns `403`; no existence detail leaks through a `404` first.
2. Stale C1 with a high cached forecast: latest assessment exposes stale/unavailable state and creates no event.
3. Two clinicians mutate one event: one transaction succeeds; the second receives `409` and the stored actor/time remains unchanged.
4. Backend restart during an elevated episode: persisted episode state prevents a duplicate event.
5. C3 failure after note submission: the note result is stored as unavailable/error, no zero is substituted, and prior working assessment endpoints remain readable.

---

# Subplan A — Central Backend Authority

Work on a new branch from `UVINDUSEN/component4final/main`, suggested name `feat/clinanx-central-p0-contracts`.

### Task 1: Add persistent authority tables

**Files:**
- Modify: `central_backend/db_models.py`
- Create: `central_backend/migrate_p0.py`
- Create: `central_backend/tests/conftest.py`
- Create: `central_backend/tests/test_p0_schema.py`
- Modify: `central_backend/requirements.txt`

**Interfaces:**
- Produces ORM classes `Clinician`, `ClinicianSubjectAssignment`, `ForecastResult`, `AttentionEvent`, and `EscalationEpisode`.
- `AttentionEvent.id` and `ForecastResult.id` are opaque strings; `FusionResult.id` stays the existing integer.

- [ ] Write `test_p0_schema.py` first. It must create a temporary SQLite database, call `Base.metadata.create_all`, and assert all five tables, the unique `(clinician_id, subject_id)` assignment constraint, the unique event `episode_key`, and foreign keys to `subjects`/`fusion_results`.
- [ ] Run `pytest central_backend/tests/test_p0_schema.py -q`; expect failure because the models do not exist.
- [ ] Add the five models. Use UTC timestamps; statuses are strings constrained in service code; assignments carry `active`, `assigned_at`, `ended_at`; forecasts carry scope/horizon/score/tier/predicted/generated/valid/model/source detail; events carry the frozen fields plus internal `episode_key`; episode state carries confirmation count, last qualifying time, active event id, and recovered time.
- [ ] Add `migrate_p0.py` that imports `Base` and `engine`, runs `Base.metadata.create_all(engine)`, and prints the created/verified P0 table names. This repository only adds tables in this slice, so the same operation is safe for existing SQLite and PostgreSQL databases.
- [ ] Pin `PyJWT==2.9.0` and `pytest==8.3.4` in `central_backend/requirements.txt`.
- [ ] Re-run the schema test and `python central_backend/test_backend.py`; both must pass.
- [ ] Commit as `feat: add clinician forecast and attention persistence`.

### Task 2: Implement clinician login and principal verification

**Files:**
- Create: `central_backend/auth.py`
- Create: `central_backend/seed_clinician.py`
- Modify: `central_backend/main.py`
- Modify: `central_backend/env.example.txt`
- Create: `central_backend/tests/test_clinician_auth.py`

**Interfaces:**
- Produces `POST /auth/login`, `GET /v1/me`, `require_clinician()`, and `require_service_or_clinician()`.
- JWT claims: `sub`, `clinician_id`, `role`, `iss`, `aud`, `iat`, `exp`, and `jti`.

- [ ] Write tests for successful login, wrong password `401`, inactive clinician `403`, expired/wrong issuer/wrong audience tokens `401`, and `/v1/me` returning `principal_type`, `clinician_id`, `display_name`, `role`, `status`, and `expires_at`.
- [ ] Run `pytest central_backend/tests/test_clinician_auth.py -q`; expect route-not-found failures.
- [ ] Implement PBKDF2-SHA256 password hashes using `hashlib.pbkdf2_hmac` with per-user salt and constant-time comparison. `seed_clinician.py` reads a password with `getpass`, creates/updates a clinician, and can activate/deactivate it without printing the password.
- [ ] Implement HS256 JWT issuance with required `CLINICIAN_JWT_SECRET`, `CLINICIAN_JWT_ISSUER`, `CLINICIAN_JWT_AUDIENCE`, and configurable eight-hour expiry. Tests inject a secret; non-test startup must not use a hard-coded secret.
- [ ] Preserve the existing `BACKEND_API_TOKEN` only for service/ingestion compatibility. New clinician routes accept only clinician JWTs.
- [ ] Re-run auth tests and the legacy backend suite.
- [ ] Commit as `feat: add clinician JWT authentication`.

### Task 3: Implement assignments and clinician roster

**Files:**
- Create: `central_backend/assignments.py`
- Modify: `central_backend/seed_clinician.py`
- Modify: `central_backend/main.py`
- Create: `central_backend/tests/test_assignments.py`

**Interfaces:**
- Produces `require_assignment(db, clinician_id, subject_id)` and `GET /v1/clinicians/me/patients`.
- Roster response is `{"clinician_id":"DR001","patients":[{"subject_id":"...","display_id":"Patient A","assigned_at":"..."}]}`.

- [ ] Write tests proving active assignment succeeds, inactive/missing assignment returns `403`, guessed subject IDs do not bypass assignment, and each clinician sees only their own roster.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement assignment lookup before any patient data query. Use a deterministic privacy-safe display label stored on the assignment; never expose MRN or alias values.
- [ ] Extend `seed_clinician.py` with `assign` and `unassign` commands taking explicit clinician and subject IDs.
- [ ] Run assignment tests and the legacy suite.
- [ ] Commit as `feat: enforce clinician patient assignments`.

### Task 4: Build the physiological ForecastResult and escalation episode engine

**Files:**
- Create: `central_backend/attention_policy.py`
- Create: `central_backend/forecast.py`
- Create: `central_backend/tests/test_attention_policy.py`
- Create: `central_backend/tests/test_forecast.py`

**Interfaces:**
- `build_forecast(subject_id, fusion_result_id, c1_response, generated_at) -> ForecastResult | None`.
- `evaluate_episode(db, forecast, current_c1_score) -> AttentionEvent | None`.

- [ ] Write pure policy tests for exact `[5,10]` horizons, malformed/non-numeric/out-of-range forecasts, 0-100 to 0-1 normalization, two confirmations 20 seconds to 2 minutes apart, thresholds 45/70, required increases 20/10, recovery below 40, restart persistence, and one event per episode.
- [ ] Run both files and confirm failure.
- [ ] Port the existing patient `PredictiveEscalationGate` semantics without changing thresholds. Persist every valid forecast with `scope="physiological"`, `horizon_minutes` equal to the selected peak/crossing horizon, and a 10-minute validity window.
- [ ] Set event severity to `elevated` for the 45-rule and `high` for the 70/current-high rule; use `policy_version="escalation-v1"`.
- [ ] Do not create a forecast or event from stale, warming, poor-signal, error, or malformed C1 data.
- [ ] Run policy/forecast tests.
- [ ] Commit as `feat: persist physiological forecasts and escalation episodes`.

### Task 5: Attach forecast/event evaluation to physiological ingestion

**Files:**
- Modify: `central_backend/main.py`
- Modify: `central_backend/modality_clients.py` only if a small typed forecast extractor is required
- Create: `central_backend/tests/test_physiological_attention_flow.py`

**Interfaces:**
- A successful `/v1/ingest/physiological` persists the ModalityReading, retains current fusion debounce behavior, persists a separate ForecastResult, then evaluates the event episode.

- [ ] Write an end-to-end TestClient test with two qualifying C1 responses followed by recovery and a second episode. Assert two confirmations create one event, repeat elevated inputs create no duplicate, recovery rearms, and the next episode creates a new event.
- [ ] Run the test and confirm failure.
- [ ] Add the post-ingest forecast/event call after the reading commit. Event evaluation must run on each valid C1 update even when full fusion is debounced.
- [ ] Return only non-sensitive IDs in the ingest response: `forecast_result_id` and `attention_event_id` when present.
- [ ] Run focused and legacy tests.
- [ ] Commit as `feat: evaluate attention policy on C1 updates`.

### Task 6: Implement canonical assessment, history, data-quality, and dashboard projections

**Files:**
- Create: `central_backend/assessment_views.py`
- Modify: `central_backend/main.py`
- Create: `central_backend/tests/test_assessment_contract.py`
- Create: `central_backend/tests/test_dashboard_contract.py`

**Interfaces:**
- Produces the frozen routes `/v1/patients/{subject_id}/assessment/latest`, `/v1/patients/{subject_id}/assessments`, `/v1/patients/{subject_id}/data-quality`, and `/v1/clinicians/me/dashboard`.

- [ ] Copy the ClinAnx complete/partial/unavailable JSON fixtures into backend contract tests and assert exact key/type compatibility.
- [ ] Add tests that latest/history/data-quality/dashboard all return `401` without JWT and `403` without assignment.
- [ ] Confirm tests fail.
- [ ] Build `AssessmentSummary` exclusively from the stored FusionResult, latest stored readings, and latest valid ForecastResult. Map backend `provisional` to canonical `partial` and `insufficient` to canonical `unavailable`.
- [ ] Populate modality `available`, `included_in_fusion`, `status`, `confidence`, `coverage`, `captured_at`, and contribution from persisted gate/result data; stale data remains visible but excluded.
- [ ] Dashboard returns frozen top-level keys `clinician`, `assigned_count`, `open_attention_events`, and `patients`; patient summaries contain the frozen minimum fields.
- [ ] History returns `{"assessments":[...]}` in newest-first order. Data-quality returns `{"subject_id":"...","modalities":[...]}` using the same modality projection as latest assessment.
- [ ] Add `fusion_result_id` to the existing patient `/risk` response without removing or renaming any current key.
- [ ] Run contract tests and legacy tests.
- [ ] Commit as `feat: expose canonical clinician assessment contracts`.

### Task 7: Implement persistent AttentionEvent APIs and atomic lifecycle

**Files:**
- Create: `central_backend/attention_api.py`
- Modify: `central_backend/main.py`
- Create: `central_backend/tests/test_attention_event_api.py`

**Interfaces:**
- Produces all six frozen event operations with `{"events":[...]}` and `{"event":{...}}` envelopes.

- [ ] Write tests for status/subject filters, detail, empty-body ACK, empty-body RESOLVE, actor/time from JWT, assignment scoping on guessed IDs, invalid status `422`, missing event `404`, and concurrent state conflicts `409`.
- [ ] Confirm route failures.
- [ ] Implement list/detail with assignment joins. ACK uses one conditional `UPDATE ... WHERE status='OPEN'`; RESOLVE uses one conditional `UPDATE ... WHERE status='ACKNOWLEDGED'`. If row count is zero and the event still exists, return `409`.
- [ ] Audit `attention.created`, `attention.acknowledged`, and `attention.resolved` with actor and event ID, never note text or credentials.
- [ ] Run focused, contract, and legacy tests.
- [ ] Commit as `feat: add persistent attention event lifecycle API`.

### Task 8: Migrate clinician routes without breaking clinical-note fusion

**Files:**
- Modify: `central_backend/main.py`
- Create: `central_backend/tests/test_clinical_note_authorization.py`

**Interfaces:**
- Clinician JWTs may submit assigned clinical notes and use clinician timeline/evidence/explanation/verdict routes.
- Service token compatibility remains for ingestion/fusion automation.

- [ ] Test that an assigned clinician note still calls C3, stores the reading, runs fusion, and returns the prior response keys; unassigned clinician gets `403`; request-body `author` cannot override JWT actor.
- [ ] Test the existing service-token clinical-note path still works during migration.
- [ ] Replace clinician-facing `_auth` usage with principal plus assignment checks. Keep service-token fallback only where explicitly required by automated ingestion.
- [ ] Run all backend tests.
- [ ] Commit as `fix: preserve clinical note fusion under clinician auth`.

### Task 9: Verify OpenAPI and deployment configuration

**Files:**
- Modify: `central_backend/env.example.txt`
- Modify: `central_backend/Dockerfile` only to ensure all new Python modules are copied by the existing wildcard
- Create: `central_backend/tests/test_openapi_contract.py`
- Create: `central_backend/P0_DEPLOYMENT.md`

- [ ] Assert every frozen route and response schema is present in `/openapi.json`.
- [ ] Run `pytest central_backend/tests -q`, `python central_backend/test_backend.py`, and `python -m central_backend.migrate_p0` against a clean temporary DB.
- [ ] Document required JWT variables, clinician seed commands, assignment commands, migration command, start command, and rollback as restoring the DB backup plus prior image.
- [ ] Commit as `docs: document ClinAnx P0 backend deployment`.

---

# Subplan B — ClinAnx Live Wiring

Continue from draft PR #36 only after Subplan A OpenAPI tests pass. Rebase its branch on current `dulhara79/tcwpn_mobile_app/main`; do not merge its unverified-contract wording unchanged.

### Task 10: Wire auth, dashboard, roster, and latest assessment to verified routes

**Files:**
- Modify: `lib/data/api/auth_service.dart`
- Modify: `lib/data/api/session.dart`
- Modify: `lib/data/local/stores.dart`
- Modify: `lib/data/repositories/central_backend_repositories.dart`
- Modify: `lib/domain/contracts/dashboard_snapshot.dart`
- Modify: `test/repositories/central_backend_repositories_test.dart`
- Modify: `test/gateway_contract_test.dart`

- [ ] Add failing tests for login expiry parsing, secure expiry persistence, `/v1/me`, frozen dashboard aggregate parsing, roster parsing, latest assessment parsing, and explicit `401/403/409/422/5xx` mapping.
- [ ] Run focused Flutter tests and confirm failures.
- [ ] Extend `AuthSession`/`Session`/`SecureStore` with `expiresAt`; expired sessions sign out before protected requests.
- [ ] Make `CentralBackendDashboardRepository` call exactly `/v1/clinicians/me/dashboard`; do not compose risk or urgency locally.
- [ ] Wire `CentralBackendAuthRepository`, `CentralBackendPatientRepository`, and `CentralBackendAssessmentRepository` to the frozen routes. Retain the already-live event repository unchanged except for verified severity parsing.
- [ ] Parse `elevated` and `high` event severity as typed values; unknown values remain unknown.
- [ ] Run focused tests, `flutter test`, and `flutter analyze`.
- [ ] Commit as `feat: connect ClinAnx to frozen central contracts`.

### Task 11: Preserve clinician workflows and complete integration verification

**Files:**
- Modify only where tests prove necessary: `lib/features/shell.dart`, `lib/features/patients/patients_screen.dart`, `lib/features/patients/patient_overview_screen.dart`
- Create: `test/integration/central_contract_flow_test.dart`
- Update: PR #36 description and contract evidence

- [ ] Add a fake-server integration test: login -> `/v1/me` -> dashboard -> assessment -> event detail -> ACK -> refresh -> RESOLVE -> refresh. Assert the same `fusion_result_id` appears in dashboard and assessment.
- [ ] Add a note-flow regression test proving `/v1/clinical-notes` remains the only authoritative C3 submission and the app refreshes latest assessment afterward.
- [ ] Run the full Flutter suite, analyzer, and debug web/APK build used by repository CI.
- [ ] Remove claims that backend routes were verified before Subplan A; replace them with the actual backend commit and OpenAPI evidence.
- [ ] Mark PR #36 ready only when both repositories' contract tests are green.

---

# Subplan C — Patient Compatibility Checkpoint

Create a separate patient branch from `DewduSendanayake/anxiety_mobile_app/main`; do not use the C4 demographic branch for this work.

### Task 12: Preserve patient behavior while exposing authoritative assessment identity

**Files:**
- Modify: `lib/services/fusion_risk_service.dart`
- Create: `test/fusion_risk_contract_test.dart`
- Verify without changing behavior: `lib/services/anxiety_feedback_service.dart`, `lib/pages/dashboard_page.dart`

- [ ] Add a failing parser test for `fusion_result_id`, existing composite/band/message fields, and missing/GREY behavior.
- [ ] Add `fusionResultId` to `FusionRisk` and parse it from the backward-compatible `/risk` response.
- [ ] Confirm the app still performs no multimodal fusion and never treats missing/GREY as low.
- [ ] Do not remove the current patient physiological check-in gate in this slice. It remains a local patient-safety/check-in mechanism, not the server AttentionEvent authority used by ClinAnx.
- [ ] Run `flutter test` and `flutter analyze`; create a separate PR with no C4 demographic changes.

## Final Cross-Repository Acceptance

- [ ] Same subject and assessment show the same `fusion_result_id` in backend, ClinAnx dashboard, ClinAnx patient overview, and patient `/risk` projection.
- [ ] Current assessment and C1-led forecast remain separate.
- [ ] Missing/stale/error data stays unavailable/partial and never becomes low.
- [ ] Unassigned clinician receives `403` for roster-external patient and event IDs.
- [ ] One episode produces one persisted event across backend restart.
- [ ] ACK/RESOLVE survive app restart and return canonical actors/timestamps.
- [ ] Existing clinical-note -> C3 -> fusion regression remains green.
- [ ] Existing patient pairing, C1 ingestion, C4 ingestion, and risk polling remain green.
- [ ] Backend OpenAPI and both app contract tests agree on exact paths and keys.

## Execution Order

1. Complete and review Subplan A Tasks 1-9 in the backend PR.
2. Deploy the backend to a staging URL and capture `/openapi.json`.
3. Complete Subplan B Tasks 10-11 and update draft ClinAnx PR #36.
4. Run the live ClinAnx contract flow against staging.
5. Complete Subplan C Task 12 and run patient regression tests.
6. Merge backend first, ClinAnx second, patient compatibility third; deploy matching versions together.
