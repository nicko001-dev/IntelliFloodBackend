"""Machine à états des alertes : une alerte est créée à l'escalade, résolue au retour au calme.

Anti-spam : si une alerte du même niveau existe déjà dans la fenêtre de « cooldown »
(alert_cooldown_seconds), elle est rouverte au lieu d'en créer une nouvelle (donc pas de SMS en double).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.enums import AlertLevel, AlertStatus
from app.models import Alert, Measurement, MeasurementPoint
from app.services.analysis import Evaluation
from app.services.messages import AlertContext, fallback_messages
from app.utils import utcnow


def update_alert_state(db: Session, point: MeasurementPoint, m: Measurement, ev: Evaluation) -> Alert | None:
    """Retourne l'Alert nouvellement créée (à notifier) ou None."""
    now = utcnow()
    new_level = AlertLevel(ev.level)
    open_alerts = list(
        db.scalars(select(Alert).where(Alert.point_id == point.id, Alert.status == AlertStatus.OPEN.value))
    )

    if new_level == AlertLevel.VERT:
        for a in open_alerts:
            a.status, a.resolved_at = AlertStatus.RESOLVED.value, now
        return None

    # Résout les alertes ouvertes de niveau inférieur (escalade) ou supérieur (désescalade)
    for a in open_alerts:
        if AlertLevel(a.level) == new_level:
            return None  # déjà couvert, rien de nouveau
        a.status, a.resolved_at = AlertStatus.RESOLVED.value, now
    if any(AlertLevel(a.level).rank > new_level.rank for a in open_alerts):
        return None  # désescalade (rouge -> orange) : on résout sans nouvelle alerte

    # Cooldown : rouvrir une alerte récente du même niveau plutôt que d'en créer une
    cooldown = timedelta(seconds=get_settings().alert_cooldown_seconds)
    recent = db.scalar(
        select(Alert)
        .where(Alert.point_id == point.id, Alert.level == new_level.value, Alert.created_at >= now - cooldown)
        .order_by(Alert.created_at.desc())
        .limit(1)
    )
    if recent is not None:
        recent.status, recent.resolved_at = AlertStatus.OPEN.value, None
        return None

    alert = Alert(
        point_id=point.id,
        measurement_id=m.id,
        level=new_level.value,
        status=AlertStatus.OPEN.value,
        reason="; ".join(ev.reasons) or f"niveau {new_level.value}",
        created_at=now,
        water_height_cm=ev.water_height_cm,
        water_level_pct=ev.water_level_pct,
        rise_rate_cm_min=ev.rise_rate_cm_min,
        obstruction_pct=ev.obstruction_pct,
        obstruction_class=ev.obstruction_class,
        blockage_detected=ev.blockage_detected,
        measurement_received_at=m.received_at,
    )
    # Messages prédéfinis immédiatement : la liste d'alertes n'est jamais vide, même si l'IA tarde.
    msgs = fallback_messages(AlertContext.from_parts(point, point.zone, alert))
    alert.message_residents, alert.message_onpc, alert.message_source = msgs.habitants, msgs.onpc, "fallback"
    db.add(alert)
    db.flush()
    return alert
