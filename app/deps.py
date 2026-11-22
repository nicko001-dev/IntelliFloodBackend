from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Alert, MeasurementPoint, Zone
from app.schemas import AlertOut


def get_point_or_404(db: Session, point_id: int) -> MeasurementPoint:
    point = db.get(MeasurementPoint, point_id)
    if point is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Point de mesure introuvable")
    return point


def get_zone_or_404(db: Session, zone_id: int) -> Zone:
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone introuvable")
    return zone


def alert_out(alert: Alert) -> AlertOut:
    p = alert.point
    return AlertOut(
        id=alert.id,
        point_id=p.id,
        point_code=p.code,
        point_name=p.name,
        zone_id=p.zone_id,
        zone_name=p.zone.name,
        commune=p.zone.commune,
        latitude=p.latitude,
        longitude=p.longitude,
        level=alert.level,
        status=alert.status,
        reason=alert.reason,
        created_at=alert.created_at,
        resolved_at=alert.resolved_at,
        water_height_cm=alert.water_height_cm,
        water_level_pct=alert.water_level_pct,
        rise_rate_cm_min=alert.rise_rate_cm_min,
        obstruction_pct=alert.obstruction_pct,
        obstruction_class=alert.obstruction_class,
        blockage_detected=alert.blockage_detected,
        message_residents=alert.message_residents,
        message_onpc=alert.message_onpc,
        message_source=alert.message_source,
        notified_at=alert.notified_at,
        latency_ms=alert.latency_ms,
    )


def unique_slug(db: Session, base: str) -> str:
    import re
    import unicodedata

    s = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:70] or "zone"
    candidate, n = s, 2
    while db.scalar(select(Zone.id).where(Zone.slug == candidate)):
        candidate, n = f"{s}-{n}", n + 1
    return candidate
