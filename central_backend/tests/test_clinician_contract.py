import datetime as dt
import os

os.environ.setdefault("CLINICIAN_JWT_SECRET", "test-secret-that-is-long-enough-for-tests")

from fastapi.testclient import TestClient
from sqlalchemy import delete

from clinician_api import hash_password
from db_models import (AttentionEvent, Clinician, ClinicianSubjectAssignment,
                       ForecastResult, FusionResult, SessionLocal, Subject, init_db, utcnow)
from main import app


def setup_function():
    init_db()
    with SessionLocal() as db:
        for model in (AttentionEvent, ForecastResult, ClinicianSubjectAssignment,
                      FusionResult, Clinician, Subject):
            db.execute(delete(model))
        db.commit()


def seed():
    with SessionLocal() as db:
        db.add(Clinician(clinician_id="DR001", display_name="Dr X", role="clinician",
                         password_hash=hash_password("secret")))
        db.add_all([Subject(subject_id="patient-a"), Subject(subject_id="patient-b")])
        db.flush()
        db.add(ClinicianSubjectAssignment(clinician_id="DR001", subject_id="patient-a"))
        fusion = FusionResult(subject_id="patient-a", composite=.58, tier="Medium", band="AMBER",
            confidence=.71, modalities_used=3, model_version="ragf-v0.4",
            harmonisation={"assessment": {"status": "complete"}})
        db.add(fusion); db.flush()
        forecast = ForecastResult(forecast_result_id="fcst_test", subject_id="patient-a",
            scope="physiological", horizon_minutes=10, score=.84, tier="High",
            escalation_predicted=True, generated_at=utcnow(), valid_until=utcnow()+dt.timedelta(minutes=10))
        db.add(forecast); db.flush()
        db.add(AttentionEvent(id="evt_test", subject_id="patient-a", fusion_result_id=fusion.id,
            forecast_result_id=forecast.forecast_result_id, severity="high", reason="policy",
            forecast_horizon=10))
        db.commit()


def auth(client):
    response = client.post("/auth/login", json={"clinician_id": "DR001", "password": "secret"})
    assert response.status_code == 200
    assert response.json()["clinician"]["clinician_id"] == "DR001"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_auth_dashboard_assessment_and_assignment_scope():
    seed(); client = TestClient(app); headers = auth(client)
    assert client.get("/v1/me", headers=headers).status_code == 200
    dashboard = client.get("/v1/clinicians/me/dashboard", headers=headers).json()
    assert dashboard["assigned_count"] == 1
    assert dashboard["patients"][0]["fusion_result_id"] == dashboard["open_attention_events"][0]["fusion_result_id"]
    assessment = client.get("/v1/patients/patient-a/assessment/latest", headers=headers).json()
    assert assessment["current_assessment"] == {"score": .58, "tier": "Medium", "band": "AMBER"}
    assert assessment["forecast"]["scope"] == "physiological"
    assert client.get("/v1/patients/patient-b/assessment/latest", headers=headers).status_code == 403


def test_attention_lifecycle_is_atomic_and_server_attributed():
    seed(); client = TestClient(app); headers = auth(client)
    assert client.get("/v1/attention-events/evt_test", headers=headers).status_code == 200
    ack = client.post("/v1/attention-events/evt_test/acknowledge", headers=headers, json={})
    assert ack.status_code == 200 and ack.json()["event"]["acknowledged_by"] == "DR001"
    assert client.post("/v1/attention-events/evt_test/acknowledge", headers=headers, json={}).status_code == 409
    resolved = client.post("/v1/attention-events/evt_test/resolve", headers=headers, json={})
    assert resolved.status_code == 200 and resolved.json()["event"]["resolved_by"] == "DR001"
    assert client.post("/v1/attention-events/evt_test/resolve", headers=headers, json={}).status_code == 409


def test_openapi_contains_frozen_paths_and_strict_empty_body():
    schema = TestClient(app).get("/openapi.json").json()
    for path in ("/auth/login", "/v1/me", "/v1/clinicians/me/dashboard",
                 "/v1/clinicians/me/patients", "/v1/patients/{subject_id}/assessment/latest",
                 "/v1/patients/{subject_id}/assessments", "/v1/patients/{subject_id}/data-quality",
                 "/v1/attention-events", "/v1/attention-events/{event_id}",
                 "/v1/attention-events/{event_id}/acknowledge", "/v1/attention-events/{event_id}/resolve"):
        assert path in schema["paths"]
    empty = schema["components"]["schemas"]["EmptyBody"]
    assert empty["additionalProperties"] is False


def test_non_clinician_role_cannot_use_clinician_api():
    with SessionLocal() as db:
        db.add(Clinician(clinician_id="ADMIN1", display_name="Admin", role="admin",
                         password_hash=hash_password("secret")))
        db.commit()
    client = TestClient(app)
    login = client.post("/auth/login", json={"clinician_id": "ADMIN1", "password": "secret"})
    assert login.status_code == 403


def test_patient_and_clinician_views_share_authoritative_fusion_identity():
    seed()
    client = TestClient(app)
    headers = auth(client)

    patient = client.get("/v1/patients/patient-a/risk")
    clinician = client.get(
        "/v1/patients/patient-a/assessment/latest",
        headers=headers,
    )

    assert patient.status_code == 200
    assert clinician.status_code == 200
    patient_body = patient.json()
    clinician_body = clinician.json()

    assert patient_body["fusion_result_id"] == clinician_body["fusion_result_id"]
    assert patient_body["composite"] == clinician_body["current_assessment"]["score"]
    assert patient_body["fusion_result_id"] is not None


def test_patient_and_clinician_views_share_unavailable_state():
    seed()
    with SessionLocal() as db:
        db.add(
            ClinicianSubjectAssignment(
                clinician_id="DR001",
                subject_id="patient-b",
            )
        )
        db.commit()

    client = TestClient(app)
    headers = auth(client)

    patient = client.get("/v1/patients/patient-b/risk")
    clinician = client.get(
        "/v1/patients/patient-b/assessment/latest",
        headers=headers,
    )

    assert patient.status_code == 200
    assert clinician.status_code == 200
    patient_body = patient.json()
    clinician_body = clinician.json()

    assert patient_body["fusion_result_id"] is None
    assert clinician_body["fusion_result_id"] is None
    assert patient_body["composite"] is None
    assert clinician_body["current_assessment"] is None
    assert patient_body["band"] == "GREY"
    assert clinician_body["assessment_status"] == "unavailable"
