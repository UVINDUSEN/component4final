"""Retryable FCM HTTP v1 outbox worker.

Run as a separate scheduled job after setting ENABLE_PUSH_NOTIFICATIONS=1,
FCM_PROJECT_ID, DEVICE_TOKEN_ENCRYPTION_KEY and Google ADC credentials.
No push content includes participant identifiers or clinical notes.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import uuid

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import or_, select

from db_models import (
    AttentionEvent, AuditLog, DeviceToken, NotificationDelivery,
    SessionLocal, utcnow,
)


def _send_fcm(token: str, event: AttentionEvent):
    import google.auth
    from google.auth.transport.requests import Request
    project = os.getenv("FCM_PROJECT_ID", "").strip()
    if not project:
        raise RuntimeError("FCM_PROJECT_ID missing")
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    credentials.refresh(Request())
    payload = {"message": {
        "token": token,
        "data": {"type": "attention_event", "event_id": event.id,
                 "severity": str(event.severity), "environment": "research"},
        "android": {"priority": "high"},
    }}
    with httpx.Client(timeout=15.0) as client:
        response = client.post(
            f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
            json=payload,
            headers={"Authorization": f"Bearer {credentials.token}"})
    return response.status_code


def dispatch_once(limit: int = 25, sender=_send_fcm) -> dict:
    if os.getenv("ENABLE_PUSH_NOTIFICATIONS") != "1":
        return {"status": "disabled", "claimed": 0}
    key = os.getenv("DEVICE_TOKEN_ENCRYPTION_KEY", "").encode()
    try:
        box = Fernet(key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("invalid DEVICE_TOKEN_ENCRYPTION_KEY") from exc
    if not os.getenv("FCM_PROJECT_ID"):
        raise RuntimeError("FCM_PROJECT_ID is required")

    now = utcnow()
    jobs = []
    with SessionLocal() as db:
        stmt = select(NotificationDelivery).where(
            or_(
                (NotificationDelivery.status == "pending") &
                (NotificationDelivery.next_attempt_at <= now),
                (NotificationDelivery.status == "sending") &
                (NotificationDelivery.claimed_until < now),
            )).order_by(NotificationDelivery.created_at).limit(limit)
        if db.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        for item in db.scalars(stmt).all():
            item.status = "sending"
            item.attempts += 1
            item.claim_id = uuid.uuid4().hex
            item.claimed_until = now + dt.timedelta(minutes=2)
            jobs.append((item.id, item.claim_id))
        db.commit()

    counts = {"claimed": len(jobs), "sent": 0, "retried": 0, "skipped": 0}
    for delivery_id, claim_id in jobs:
        with SessionLocal() as db:
            delivery = db.get(NotificationDelivery, delivery_id)
            if delivery is None or delivery.claim_id != claim_id:
                continue
            device = db.get(DeviceToken, delivery.device_id)
            event = db.get(AttentionEvent, delivery.event_id)
            event_created = event.created_at if event else None
            if event_created is not None and event_created.tzinfo is None:
                event_created = event_created.replace(tzinfo=dt.timezone.utc)
            # Never send notifications about old, resolved or cancelled events.
            if (not device or not device.active or not event or event.status != "OPEN"
                    or not event_created or now - event_created > dt.timedelta(minutes=10)):
                delivery.status, delivery.claimed_until = "skipped", None
                db.commit()
                counts["skipped"] += 1
                continue
            try:
                push_token = box.decrypt(device.token_ciphertext.encode()).decode()
                status = sender(push_token, event)
            except Exception:
                status = 503
            if 200 <= status < 300:
                delivery.status, delivery.sent_at = "sent", utcnow()
                counts["sent"] += 1
            elif status in {400, 404, 410} or delivery.attempts >= 5:
                delivery.status = "failed"
                delivery.failure_code = str(status)
            else:
                delivery.status = "pending"
                delay = min(300, 15 * 2 ** min(delivery.attempts, 5))
                delivery.next_attempt_at = utcnow() + dt.timedelta(seconds=delay)
                counts["retried"] += 1
            delivery.claimed_until = None
            db.add(AuditLog(subject_id=event.subject_id,
                            event="notification.delivery",
                            actor="backend", detail={
                                "event_id": event.id, "delivery_id": delivery.id,
                                "status": delivery.status, "http_status": status}))
            db.commit()
    return counts


if __name__ == "__main__":
    print(dispatch_once())
