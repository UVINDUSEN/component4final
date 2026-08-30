"""
Adds the three missing routes to an EXISTING main.py that has already diverged
from the repo.  Safe to re-run — each insert is guarded by a check.

Usage:
  ./.venv/bin/python3 apply_routes.py          # dry-run (prints what it would do)
  ./.venv/bin/python3 apply_routes.py --apply  # actually writes
"""
import sys, re, os

DRY = "--apply" not in sys.argv
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
src = open(path).read()

changes = 0

# ─────────────────────────────────────────────────────────────────────────────
# 1.  POST /v1/subjects/self  +  POST /v1/subjects/attach
#     Insert BEFORE the existing resolve_subject route.
# ─────────────────────────────────────────────────────────────────────────────

SELF_ATTACH_BLOCK = r'''

# ═══════════════════════════════════════════════════════════════════════════════
# PATIENT-FIRST ENROLMENT  (added by apply_routes.py)
# ═══════════════════════════════════════════════════════════════════════════════

class SelfEnrolRequest(BaseModel):
    app_user_id: str = Field(..., min_length=4, max_length=128,
                             description="the participant id AURA mints at registration")


class SelfEnrolResponse(BaseModel):
    subject_id: str
    created: bool
    clinician_linked: bool


@app.post("/v1/subjects/self", response_model=SelfEnrolResponse, tags=["enrolment"])
def self_enrol(req: SelfEnrolRequest, db: Session = Depends(get_session),
               authorization: Optional[str] = Header(None)):
    """AURA claims a subject for itself at registration.

    Idempotent: re-calling for a known app_user_id returns the existing subject.
    """
    _auth(authorization)
    app_user_id = req.app_user_id.strip()
    if not app_user_id:
        raise HTTPException(422, "app_user_id must not be blank")

    existing = db.scalar(select(SubjectAlias).where(
        SubjectAlias.alias_type == "app_user_id",
        SubjectAlias.alias_value == app_user_id))

    if existing:
        subject = db.get(Subject, existing.subject_id)
        if subject is None or subject.status != "active":
            raise HTTPException(409, "that participant id belongs to an inactive subject")
        linked = db.scalar(select(SubjectAlias).where(
            SubjectAlias.subject_id == existing.subject_id,
            SubjectAlias.alias_type == "mrn_hash")) is not None
        _audit(db, existing.subject_id, "enrol.self.repeat", {"app_user_id": app_user_id})
        db.commit()
        return SelfEnrolResponse(subject_id=existing.subject_id, created=False,
                                 clinician_linked=linked)

    subject_id = identity.new_subject_id()
    db.add(Subject(subject_id=subject_id, enrolled_by="aura-self"))
    db.add(SubjectAlias(subject_id=subject_id, alias_type="app_user_id",
                        alias_value=app_user_id))
    _audit(db, subject_id, "enrol.self.created", {"app_user_id": app_user_id})
    db.commit()
    return SelfEnrolResponse(subject_id=subject_id, created=True, clinician_linked=False)


class AttachRequest(BaseModel):
    app_user_id: str = Field(..., description="the id decoded from the AURA QR")
    mrn: Optional[str] = Field(None, description="ward MRN, if the clinician has one")
    enrolled_by: Optional[str] = None


@app.post("/v1/subjects/attach", tags=["enrolment"])
def attach_subject(req: AttachRequest, db: Session = Depends(get_session),
                   authorization: Optional[str] = Header(None)):
    """Clinician scans the AURA QR — binds their record to the subject the
    PATIENT already created, instead of minting a second one."""
    _auth(authorization)
    app_user_id = req.app_user_id.strip()
    alias = db.scalar(select(SubjectAlias).where(
        SubjectAlias.alias_type == "app_user_id",
        SubjectAlias.alias_value == app_user_id))
    if not alias:
        raise HTTPException(404, "no AURA registration for that participant id — "
                                 "ask the patient to open Aura and complete sign-up")

    subject = _require_subject(db, alias.subject_id)

    mrn_linked = False
    if req.mrn:
        try:
            mrn_hash = identity.hash_mrn(req.mrn)
        except identity.PepperNotConfigured as exc:
            raise HTTPException(500, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

        clash = db.scalar(select(SubjectAlias).where(
            SubjectAlias.alias_type == "mrn_hash", SubjectAlias.alias_value == mrn_hash))
        if clash and clash.subject_id != subject.subject_id:
            raise HTTPException(409, "that MRN already belongs to a different subject")
        if not clash:
            db.add(SubjectAlias(subject_id=subject.subject_id,
                                alias_type="mrn_hash", alias_value=mrn_hash))
        mrn_linked = True

    if req.enrolled_by and subject.enrolled_by in (None, "", "aura-self"):
        subject.enrolled_by = req.enrolled_by

    _audit(db, subject.subject_id, "enrol.attached",
           {"app_user_id": app_user_id, "mrn_linked": mrn_linked}, req.enrolled_by)
    db.commit()
    return {"subject_id": subject.subject_id, "app_user_id": app_user_id,
            "mrn_linked": mrn_linked, "clinician_linked": True}


'''

if "/v1/subjects/self" in src:
    print("SKIP  /v1/subjects/self already present")
else:
    # Anchor: the resolve_subject route definition
    anchor = '@app.get("/v1/subjects/resolve"'
    if anchor not in src:
        print("ERROR  cannot find resolve_subject anchor — aborting")
        sys.exit(1)
    src = src.replace(anchor, SELF_ATTACH_BLOCK + anchor)
    changes += 1
    print("ADD   /v1/subjects/self + /v1/subjects/attach  (before resolve_subject)")


# ─────────────────────────────────────────────────────────────────────────────
# 2.  doctor_timeline missing fields: fusion_result_id, modalities_used, renormalised
# ─────────────────────────────────────────────────────────────────────────────

if '"fusion_result_id": latest.id' in src:
    print("SKIP  timeline already has fusion_result_id")
else:
    old_timeline = '''"subject_id": subject_id,
        "composite": latest.composite if latest else None,
        "tier": latest.tier if latest else None,
        "band": latest.band if latest else "GREY",
        "confidence": round(latest.confidence, 4) if latest else 0.0,
        "reason": latest.reason if latest else "no assessment yet",'''

    new_timeline = '''"subject_id": subject_id,
        "fusion_result_id": latest.id if latest else None,
        "composite": latest.composite if latest else None,
        "tier": latest.tier if latest else None,
        "band": latest.band if latest else "GREY",
        "confidence": round(latest.confidence, 4) if latest else 0.0,
        "modalities_used": latest.modalities_used if latest else 0,
        "renormalised": bool(latest.renormalised) if latest else False,
        "reason": latest.reason if latest else "no assessment yet",'''

    if old_timeline in src:
        src = src.replace(old_timeline, new_timeline)
        changes += 1
        print("ADD   fusion_result_id + modalities_used + renormalised to doctor_timeline")
    else:
        print("WARN  could not find timeline block — may already be patched differently")


# ─────────────────────────────────────────────────────────────────────────────
# 3.  POST /v1/evidence/ask   (global, non-patient-scoped RAG)
#     Insert BEFORE the health() endpoint.
# ─────────────────────────────────────────────────────────────────────────────

EVIDENCE_ASK_BLOCK = r'''

# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL RAG — Ask CARE tab  (added by apply_routes.py)
# ═══════════════════════════════════════════════════════════════════════════════

class GlobalEvidenceRequest(BaseModel):
    question: str = Field(..., min_length=1)


@app.post("/v1/evidence/ask", tags=["egress"])
def evidence_ask(req: GlobalEvidenceRequest, db: Session = Depends(get_session),
                 authorization: Optional[str] = Header(None)):
    """Knowledge-base question with no patient selected — backs the Ask CARE tab."""
    _auth(authorization)
    result = rag_client.call_rag(req.question)
    _audit(db, None, "rag.evidence.global",
           {"available": result.available, "abstained": result.abstained,
            "safety_level": result.safety_level,
            "local_crisis_bypass": result.local_crisis_bypass,
            "error": result.error})
    db.commit()
    return result.to_wire()


'''

if "/v1/evidence/ask" in src:
    print("SKIP  /v1/evidence/ask already present")
else:
    anchor = '@app.get("/health"'
    if anchor not in src:
        print("ERROR  cannot find health() anchor — aborting")
        sys.exit(1)
    src = src.replace(anchor, EVIDENCE_ASK_BLOCK + anchor)
    changes += 1
    print("ADD   /v1/evidence/ask  (before health)")


# ─────────────────────────────────────────────────────────────────────────────
# Write
# ─────────────────────────────────────────────────────────────────────────────
if changes == 0:
    print("\nNothing to do — all routes already present.")
elif DRY:
    print(f"\nDRY RUN — {changes} change(s) ready.  Re-run with --apply to write.")
else:
    with open(path, "w") as f:
        f.write(src)
    print(f"\nWROTE {changes} change(s) to {path}")
