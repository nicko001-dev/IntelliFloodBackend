"""Diffusion d'une alerte : rédaction (IA ou secours) puis SMS, push, ONPC, temps réel.

Règles (cahier des charges §6.1) : l'alerte ROUGE déclenche SMS aux inscrits du quartier + notification
+ message à l'ONPC ; l'ORANGE déclenche une pré-alerte (notification push aux inscrits). Les SMS en orange
et les messages ONPC en orange sont activables par configuration (SMS_ON_ORANGE, ONPC_NOTIFY_ON_ORANGE).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.enums import Channel, NotificationStatus
from app.models import Alert, DeviceToken, MeasurementPoint, NotificationLog, Subscriber
from app.services.ai import generate_messages
from app.services.emailer import send_email
from app.services.messages import ONPC_LIMIT, SMS_LIMIT, AlertContext, to_sms_text
from app.services.push import build_push_provider
from app.services.realtime import alert_event, publish
from app.services.sms import build_sms_provider
from app.utils import mask_phone, utcnow

log = logging.getLogger(__name__)
MAX_WORKERS = 8


def get_sms_provider():
    return build_sms_provider(get_settings())


@lru_cache
def get_push_provider():
    return build_push_provider(get_settings())


def _status(ok: bool, simulated: bool) -> str:
    if not ok:
        return NotificationStatus.FAILED.value
    return NotificationStatus.SIMULATED.value if simulated else NotificationStatus.SENT.value


def _notify(db: Session, settings: Settings, alert: Alert, ctx: AlertContext) -> list[NotificationLog]:
    logs: list[NotificationLog] = []
    is_red = alert.level == "rouge"
    zone_id = alert.point.zone_id
    sms = get_sms_provider()

    def log_entry(channel: Channel, recipient: str, provider: str, ok: bool, simulated: bool, error: str | None):
        logs.append(
            NotificationLog(
                alert_id=alert.id,
                channel=channel.value,
                recipient=recipient,
                provider=provider,
                status=_status(ok, simulated),
                error=error,
            )
        )

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        # 1) SMS aux habitants inscrits du quartier
        if is_red or settings.sms_on_orange:
            subs = list(db.scalars(select(Subscriber).where(Subscriber.zone_id == zone_id, Subscriber.active.is_(True))))
            text = to_sms_text(alert.message_residents, SMS_LIMIT)
            for sub, res in zip(subs, pool.map(lambda s: sms.send(s.phone, text), subs), strict=True):
                log_entry(Channel.SMS, mask_phone(sub.phone), sms.name, res.ok, res.simulated, res.error)

        # 2) Notification push dans l'application
        tokens = list(
            db.scalars(
                select(DeviceToken).where(
                    DeviceToken.active.is_(True), (DeviceToken.zone_id == zone_id) | (DeviceToken.zone_id.is_(None))
                )
            )
        )
        if tokens:
            push = get_push_provider()
            title = (f"Alerte rouge — {ctx.zone_name}" if is_red else f"Pré-alerte — {ctx.zone_name}")
            data = {
                "alert_id": str(alert.id),
                "point_id": str(alert.point_id),
                "zone_id": str(zone_id),
                "level": alert.level,
            }
            results = pool.map(lambda t: push.send(t.token, title, alert.message_residents, data), tokens)
            for tok, res in zip(tokens, results, strict=True):
                if res.invalid_token:
                    tok.active = False
                log_entry(Channel.PUSH, f"{tok.platform}:{tok.token[:8]}…", getattr(push, "name", "push"), res.ok, res.simulated, res.error)

        # 3) ONPC : SMS et/ou e-mail (démonstration)
        if is_red or settings.onpc_notify_on_orange:
            onpc_text = to_sms_text(alert.message_onpc, ONPC_LIMIT)
            for phone in settings.onpc_phones:
                res = sms.send(phone, onpc_text)
                log_entry(Channel.SMS, "ONPC " + mask_phone(phone), sms.name, res.ok, res.simulated, res.error)
            subject = f"[IntelliFlood] Alerte {alert.level.upper()} — {ctx.zone_name}"
            for addr in settings.onpc_email_list:
                ok, simulated, err = send_email(settings, addr, subject, alert.message_onpc)
                log_entry(Channel.EMAIL, "ONPC " + addr[:2] + "***" + addr[addr.find("@"):], "smtp", ok, simulated, err)
    return logs


def dispatch_alert(alert_id: int) -> None:
    """Tâche de fond : à appeler après le commit de l'alerte. Ne lève jamais d'exception."""
    try:
        settings = get_settings()
        with SessionLocal() as db:
            alert = db.scalar(
                select(Alert)
                .options(joinedload(Alert.point).joinedload(MeasurementPoint.zone))
                .where(Alert.id == alert_id)
            )
            if alert is None:
                return
            ctx = AlertContext.from_alert(alert)

            generated = generate_messages(ctx, settings)
            alert.message_residents = generated.messages.habitants
            alert.message_onpc = generated.messages.onpc
            alert.message_source = generated.source
            alert.ai_error = generated.error
            db.commit()

            logs = _notify(db, settings, alert, ctx)
            db.add_all(logs)
            if alert.notified_at is None and any(
                entry.status in (NotificationStatus.SENT.value, NotificationStatus.SIMULATED.value) for entry in logs
            ):
                alert.notified_at = utcnow()
                if alert.measurement_received_at:
                    alert.latency_ms = int((alert.notified_at - alert.measurement_received_at).total_seconds() * 1000)
            db.commit()
            event = alert_event(alert, "alert_updated")
        publish(event)
    except Exception:  # pragma: no cover - dernier filet : une alerte ne doit jamais faire tomber le serveur
        log.exception("Échec de la diffusion de l'alerte %s", alert_id)
