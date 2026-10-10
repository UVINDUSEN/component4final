"""Patient-controlled name sharing: do not mix PHI with research identity."""
import os

from cryptography.fernet import Fernet

os.environ.setdefault("MRN_PEPPER", "patient-name-tests-mrn-pepper")
os.environ.setdefault("CLINICIAN_JWT_SECRET", "patient-name-tests-clinician-secret")
os.environ.setdefault("PATIENT_JWT_SECRET", "patient-name-tests-patient-secret")
os.environ.setdefault("PATIENT_DISPLAY_NAME_FERNET_KEY", Fernet.generate_key().decode())

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from clinician_api import hash_password
from db_models import (
    AuditLog, AttentionEvent, Clinician, ClinicianAssignmentInvite,
    ClinicianSubjectAssignment, EscalationEpisode, ForecastResult, FusionResult,
    ModalityReading, PairingCode, PatientCredential, PatientDisplayName,
    SessionLocal, Subject, SubjectAlias, Verdict, init_db,
)
from main import app
from patient_auth import issue_patient_token


def setup_function():
    init_db()
    with SessionLocal() as db:
        for model in (AuditLog, AttentionEvent, EscalationEpisode, ForecastResult,
                      Verdict, ClinicianSubjectAssignment, ClinicianAssignmentInvite,
                      PatientDisplayName, FusionResult, ModalityReading, PairingCode,
                      PatientCredential, SubjectAlias, Clinician, Subject):
            db.execute(delete(model))
        db.add_all([
            Subject(subject_id="subject-a"), Subject(subject_id="subject-b"),
            Clinician(clinician_id="DR001", display_name="Clinician One",
                      password_hash=hash_password("testpassword")),
        ])
        db.commit()


def clinician_auth(c):
    res = c.post("/auth/login", json={
        "clinician_id": "DR001", "password": "testpassword"})
    assert res.status_code == 200
    return {"Authorization": "Bearer " + res.json()["access_token"]}


def patient_auth(subject):
    token, _ = issue_patient_token(subject)
    return {"Authorization": "Bearer " + token}


def assign(c, patient, clinician):
    issued = c.post("/v1/patients/me/assignment-invites", headers=patient, json={})
    assert issued.status_code == 200
    redeemed = c.post("/v1/clinicians/me/assignments", headers=clinician,
                      json={"invite_code": issued.json()["invite_code"]})
    assert redeemed.status_code == 200


def test_patient_controls_encrypted_name_and_assigned_clinician_sees_it():
    c = TestClient(app)
    clinician = clinician_auth(c)
    patient = patient_auth("subject-a")

    # Identity remains pseudonymous without patient name-sharing consent.
    assign(c, patient, clinician)
    before = c.get("/v1/clinicians/me/patients", headers=clinician).json()
    assert before["patients"][0]["patient_name"] is None

    response = c.put("/v1/patients/me/display-name", headers=patient,
                     json={"display_name": "  Test Patient  "})
    assert response.status_code == 200
    assert response.json() == {"shared": True}
    with SessionLocal() as db:
        row = db.get(PatientDisplayName, "subject-a")
        assert row is not None
        assert "Test Patient" not in row.encrypted_name
        assert db.get(Subject, "subject-a").subject_id == "subject-a"
        assert not any("Test Patient" in str(e.detail)
                       for e in db.scalars(select(AuditLog)))

    roster = c.get("/v1/clinicians/me/patients", headers=clinician).json()
    assert roster["patients"][0]["patient_name"] == "Test Patient"
    dashboard = c.get("/v1/clinicians/me/dashboard", headers=clinician).json()
    assert dashboard["patients"][0]["patient_name"] == "Test Patient"

    # Updates apply to the same subject; a second enrollment is never needed.
    assert c.put("/v1/patients/me/display-name", headers=patient,
                 json={"display_name": "New Display Name"}).status_code == 200
    roster = c.get("/v1/clinicians/me/patients", headers=clinician).json()
    assert roster["patients"][0]["patient_name"] == "New Display Name"

    # Revocation removes access from future responses.
    assert c.delete("/v1/patients/me/display-name", headers=patient).json() == {"shared": False}
    after = c.get("/v1/clinicians/me/patients", headers=clinician).json()
    assert after["patients"][0]["patient_name"] is None
    with SessionLocal() as db:
        assert db.get(PatientDisplayName, "subject-a") is None


def test_patient_auth_and_assignment_boundaries_and_input_validation():
    c = TestClient(app)
    clinician = clinician_auth(c)
    a = patient_auth("subject-a")
    b = patient_auth("subject-b")

    assert c.put("/v1/patients/me/display-name",
                 json={"display_name": "No authentication"}).status_code == 401
    assert c.put("/v1/patients/me/display-name", headers=clinician,
                 json={"display_name": "Role confusion"}).status_code == 401
    assert c.put("/v1/patients/me/display-name", headers=a,
                 json={"subject_id": "subject-b", "display_name": "No"}).status_code == 422
    assert c.put("/v1/patients/me/display-name", headers=a,
                 json={"display_name": "  "}).status_code == 422
    assert c.put("/v1/patients/me/display-name", headers=a,
                 json={"display_name": "bad\\nname"}).status_code == 422
    assert c.put("/v1/patients/me/display-name", headers=a,
                 json={"display_name": "X" * 81}).status_code == 422

    assert c.put("/v1/patients/me/display-name", headers=b,
                 json={"display_name": "Unassigned Patient"}).status_code == 200
    # Unassigned subjects are not included in clinician roster or dashboard.
    assert c.get("/v1/clinicians/me/patients", headers=clinician).json()["patients"] == []
    assert c.get("/v1/patients/subject-b/assessment/latest",
                 headers=clinician).status_code == 403
    assert c.delete("/v1/patients/me/display-name", headers=clinician).status_code == 401


def test_key_is_required_and_no_plaintext_fallback(monkeypatch):
    from patient_display_names import display_name_key_configured
    c = TestClient(app)
    patient = patient_auth("subject-a")
    monkeypatch.delenv("PATIENT_DISPLAY_NAME_FERNET_KEY", raising=False)
    assert not display_name_key_configured()
    assert c.put("/v1/patients/me/display-name", headers=patient,
                 json={"display_name": "Name"}).status_code == 503
    with SessionLocal() as db:
        assert db.get(PatientDisplayName, "subject-a") is None
