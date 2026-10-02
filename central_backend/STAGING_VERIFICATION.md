# R26-DS-012 staging verification

## Purpose

This runbook closes the gap between deterministic CI verification and real deployment evidence.

The Integration Handbook requires a backend-authoritative flow: one subject identity, one FusionResult, separate forecast, server-owned AttentionEvent lifecycle, and consistent patient/clinician views.

## Evidence required

Record:

- backend revision
- C1/C3/C4/RAG revisions
- model and escalation policy versions
- `/ready` response
- schema revision
- patient app SHA
- ClinAnx SHA

## Synthetic staging flow

1. Create a fresh synthetic participant.
2. Register patient session.
3. Create clinician assignment invite.
4. Redeem invite once.
5. Verify second redemption fails.
6. Produce C1/C3/C4 inputs.
7. Confirm C2 remains excluded.
8. Confirm patient and clinician receive the same `fusion_result_id`.
9. Confirm one AttentionEvent episode.
10. ACK and RESOLVE from ClinAnx.
11. Restart services and verify persistence.

## Failure evidence

Capture:

- stale C1
- unavailable C3/C4
- invalid JWT
- unassigned access
- network recovery
- duplicate event attempts
- concurrent event transitions

Do not store patient identifiers or tokens in evidence files.

## Acceptance boundary

CI proves the code contract. This run proves the deployed environment. Release acceptance requires both.
