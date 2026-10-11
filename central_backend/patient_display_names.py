"""Patient-consented display names, encrypted separately from research records.

The encryption key must be provided by the deployment secret manager as
PATIENT_DISPLAY_NAME_FERNET_KEY, distinct from JWT/DB/identity secrets.
Never log names or return them in patient-independent service responses.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy.orm import Session

from db_models import PatientDisplayName


def _cipher() -> Fernet:
    key = os.getenv("PATIENT_DISPLAY_NAME_FERNET_KEY", "")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError):
        raise HTTPException(503, "patient display-name encryption is not configured")


def display_name_key_configured() -> bool:
    try:
        _cipher()
        return True
    except HTTPException:
        return False


def encrypt_display_name(name: str) -> str:
    return _cipher().encrypt(name.encode("utf-8")).decode("ascii")


def shared_display_name(db: Session, subject_id: str) -> str | None:
    """Only call from already assignment-scoped clinician roster/dashboard."""
    row = db.get(PatientDisplayName, subject_id)
    if row is None:
        return None
    try:
        return _cipher().decrypt(row.encrypted_name.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Fail closed; never substitute a misleading name for a patient.
        raise HTTPException(503, "patient display-name ciphertext cannot be verified")
