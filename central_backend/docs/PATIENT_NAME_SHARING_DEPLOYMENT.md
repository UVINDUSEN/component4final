# Patient-consented display name sharing

This change deliberately adds a separately encrypted personally identifying field.
The **canonical** subject_id, aliases, research signals, fusion and attention
events remain pseudonymous; the existing "Patient XXXXXXXX" fallback stays.

## One-time deployment setup

1. Obtain clinical/research approval for identifiable data processing, storage
   duration, incident access, retention, revocation, and assigned-clinician visibility.
2. Generate **one persistent new key** offline:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   Provision it as `PATIENT_DISPLAY_NAME_FERNET_KEY` via the host secret manager.
   **Do not commit, print in logs, put in a Flutter build, or rotate without re-encrypting stored names.**
3. Back up the existing production database using the team's approved procedure.
4. Deploy backend migrations **before** starting the updated backend:
   `python central_backend/migrate_p0.py` (from repo root, with production env).
   Migration `0004_encrypted_patient_display_names` creates a separate
   `patient_display_names` table. Existing subjects/assessments are preserved.
5. Deploy updated backend. `GET /ready` now requires schema revision 0004 and
   a valid Fernet key. If either is missing the service deliberately returns 503.
6. Deploy the Aura and ClinAnx clients pointing to this same backend instance.

## API and boundaries

- `PUT /v1/patients/me/display-name` with patient JWT,
  `{"display_name":"Chosen name"}`. This overwrites the encrypted record
  for the JWT subject only. No subject_id is accepted from the caller.
- `DELETE /v1/patients/me/display-name` with patient JWT revokes
  the shared name. The pseudonymous fallback stays.
- Assigned clinicians see nullable `patient_name` in their authenticated
  `GET /v1/clinicians/me/patients` and dashboard payloads. Unassigned clinicians
  cannot fetch names; there is no unauthenticated name endpoint.
- Logs and audit events contain metadata only, never plaintext names; names
  must not enter QR payloads, push notifications, or research/model logs.
- A name is a user-entered **display name**, not verified legal identity.
  Do not use as an immutable identity key.

## Privacy caveats

Fernet encrypts the database field at application level. This does not replace
transport TLS, access control, database backups protection, host security, logging
reviews, or operational access policies. In-memory decrypted values and TLS
responses are visible to the authorized clinician device; avoid screenshot or
notification exposure. A patient can revoke future API disclosure; revocation
does not erase copies already read by clinicians.

## Verification

`pytest central_backend/tests/test_patient_display_names.py`

Also rerun the complete backend tests and Flutter tests, verify privacy
restrictions and cross-device name updates/revocation on an approved deployment.
Do not point test fixtures at an actual research database.
