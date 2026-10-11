"""Operational readiness must not be inferred from a running HTTP process."""

import os

os.environ.setdefault("MRN_PEPPER", "readiness-test-pepper")

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from db_models import engine
from main import app
from migrate_p0 import run_migrations


def test_readiness_checks_schema_database_and_patient_and_clinician_secrets(monkeypatch):
    run_migrations(engine)
    monkeypatch.setenv("CLINICIAN_JWT_SECRET", "test-clinician-secret")
    monkeypatch.setenv("PATIENT_JWT_SECRET", "test-patient-secret")
    monkeypatch.setenv("BACKEND_API_TOKEN", "test-internal-service-token")
    monkeypatch.setenv("PATIENT_DISPLAY_NAME_FERNET_KEY", Fernet.generate_key().decode())
    client = TestClient(app)
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["database"] == "ready"
    assert ready.json()["schema_revision"] == "0004_encrypted_patient_display_names"
    assert ready.json()["auth"] == {"clinician": True, "patient": True}
    monkeypatch.delenv("PATIENT_JWT_SECRET")
    assert client.get("/ready").status_code == 503
