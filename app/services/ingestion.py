"""Réception d'une mesure : filtrage, calcul, enregistrement, mise à jour de l'état du point et alerte."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Alert, Measurement, MeasurementPoint
from app.schemas import MeasurementReading
from app.services import analysis
from app.services.alerting import update_alert_state
from app.utils import utcnow

MAX_FUTURE_SKEW = timedelta(minutes=5)
MAX_PAST_AGE = timedelta(days=7)


@dataclass
class IngestResult:
    measurement: Measurement
    evaluation: analysis.Evaluation
    alert: Alert | None
    state_updated: bool


def resolve_timestamp(provided: datetime | None, now: datetime) -> datetime:
    """Les ESP32 n'ont pas toujours d'horloge fiable : on retombe sur l'heure du serveur si incohérent."""
    if provided is None:
        return now
    if provided.tzinfo is None:
        provided = provided.replace(tzinfo=now.tzinfo)
    if provided > now + MAX_FUTURE_SKEW or provided < now - MAX_PAST_AGE:
        return now
    return provided


def ingest_measurement(
    db: Session, point: MeasurementPoint, data: MeasurementReading, *, source: str = "http"
) -> IngestResult:
    settings = get_settings()
    now = utcnow()
    measured_at = resolve_timestamp(data.measured_at, now)
    cfg = analysis.PointConfig.from_point(point)

    if data.readings_cm:
        distance, kept = analysis.filter_readings(data.readings_cm)
    else:
        distance, kept = data.distance_cm, 1  # type: ignore[assignment]

    height = analysis.water_height_cm(cfg, distance)
    window = timedelta(minutes=settings.rise_window_minutes)
    rows = db.execute(
        select(Measurement.measured_at, Measurement.water_height_cm)
        .where(
            Measurement.point_id == point.id,
            Measurement.measured_at >= measured_at - window,
            Measurement.measured_at < measured_at,
        )
        .order_by(Measurement.measured_at)
    ).all()
    rise = analysis.compute_rise_rate(
        measured_at, height, [(r[0], r[1]) for r in rows], settings.rise_window_minutes
    )

    ev = analysis.evaluate(
        cfg,
        distance_cm=distance,
        distance_aval_cm=data.distance_aval_cm,
        obstruction_distance_cm=data.obstruction_distance_cm,
        rise_rate_cm_min=rise,
    )

    m = Measurement(
        point_id=point.id,
        measured_at=measured_at,
        received_at=now,
        source=source,
        simulated=data.simulated or point.simulated,
        distance_cm=distance,
        readings_count=kept,
        distance_aval_cm=data.distance_aval_cm,
        obstruction_distance_cm=data.obstruction_distance_cm,
        water_height_cm=ev.water_height_cm,
        water_level_pct=ev.water_level_pct,
        rise_rate_cm_min=ev.rise_rate_cm_min,
        obstruction_pct=ev.obstruction_pct,
        obstruction_class=ev.obstruction_class,
        blockage_detected=ev.blockage_detected,
        alert_level=ev.level,
    )
    db.add(m)
    db.flush()

    # Une mesure reçue en différé (rattrapage après coupure réseau) n'écrase pas l'état courant.
    is_latest = point.last_seen_at is None or measured_at >= point.last_seen_at
    alert = None
    if is_latest:
        point.last_seen_at = measured_at
        point.water_height_cm = ev.water_height_cm
        point.water_level_pct = ev.water_level_pct
        point.rise_rate_cm_min = ev.rise_rate_cm_min
        point.obstruction_pct = ev.obstruction_pct
        point.obstruction_class = ev.obstruction_class
        point.blockage_detected = ev.blockage_detected
        alert = update_alert_state(db, point, m, ev)
        point.current_level = ev.level
    db.flush()
    return IngestResult(measurement=m, evaluation=ev, alert=alert, state_updated=is_latest)
