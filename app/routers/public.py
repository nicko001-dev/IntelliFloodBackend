"""Lecture publique pour l'application mobile : zones, points, carte, historique, alertes."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.deps import alert_out, get_point_or_404, get_zone_or_404
from app.models import Alert, Measurement, MeasurementPoint, Zone
from app.schemas import AlertOut, HistoryBucket, MeasurementOut, PointOut, ZoneOut
from app.utils import utcnow

router = APIRouter(tags=["Application mobile"])


@router.get("/zones", response_model=list[ZoneOut], summary="Quartiers disponibles (écran Inscription)")
def list_zones(db: Session = Depends(get_db)):
    return db.scalars(select(Zone).order_by(Zone.commune, Zone.name)).all()


@router.get("/zones/{zone_id}", response_model=ZoneOut)
def get_zone(zone_id: int, db: Session = Depends(get_db)):
    return get_zone_or_404(db, zone_id)


def _points_query(zone_id: int | None, level: str | None, include_inactive: bool = False):
    q = select(MeasurementPoint).options(joinedload(MeasurementPoint.zone))
    if not include_inactive:
        q = q.where(MeasurementPoint.active.is_(True))
    if zone_id is not None:
        q = q.where(MeasurementPoint.zone_id == zone_id)
    if level:
        q = q.where(MeasurementPoint.current_level == level)
    return q


@router.get("/points", response_model=list[PointOut], summary="Points de mesure et état courant (écran Accueil)")
def list_points(
    zone_id: int | None = None,
    level: str | None = Query(default=None, pattern="^(vert|orange|rouge)$"),
    db: Session = Depends(get_db),
):
    return db.scalars(_points_query(zone_id, level).order_by(MeasurementPoint.name)).all()


@router.get("/points/geojson", summary="Points au format GeoJSON (écran Carte : Leaflet / flutter_map)")
def points_geojson(zone_id: int | None = None, db: Session = Depends(get_db)):
    points = db.scalars(_points_query(zone_id, None).order_by(MeasurementPoint.name)).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p.longitude, p.latitude]},  # GeoJSON : [lon, lat]
                "properties": {
                    "id": p.id,
                    "code": p.code,
                    "name": p.name,
                    "zone": p.zone.name,
                    "commune": p.zone.commune,
                    "level": p.current_level,
                    "color": p.level_color,
                    "water_level_pct": p.water_level_pct,
                    "obstruction_class": p.obstruction_class,
                    "connectivity": p.connectivity,
                    "last_seen_at": p.last_seen_at.isoformat() if p.last_seen_at else None,
                },
            }
            for p in points
        ],
    }


@router.get("/points/{point_id}", response_model=PointOut, summary="Détail d'un point")
def get_point(point_id: int, db: Session = Depends(get_db)):
    get_point_or_404(db, point_id)
    return db.scalar(
        select(MeasurementPoint).options(joinedload(MeasurementPoint.zone)).where(MeasurementPoint.id == point_id)
    )


@router.get(
    "/points/{point_id}/measurements",
    response_model=list[MeasurementOut],
    summary="Dernières mesures d'un point",
)
def point_measurements(
    point_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    get_point_or_404(db, point_id)
    q = select(Measurement).where(Measurement.point_id == point_id)
    if since:
        q = q.where(Measurement.measured_at >= since)
    if until:
        q = q.where(Measurement.measured_at <= until)
    return db.scalars(q.order_by(Measurement.measured_at.desc()).limit(limit)).all()


@router.get(
    "/points/{point_id}/history",
    response_model=list[HistoryBucket],
    summary="Historique agrégé pour le graphique (écran Détail d'un point)",
)
def point_history(
    point_id: int,
    hours: int = Query(default=24, ge=1, le=168),
    bucket_minutes: int = Query(default=10, ge=1, le=360),
    db: Session = Depends(get_db),
):
    get_point_or_404(db, point_id)
    start = utcnow() - timedelta(hours=hours)
    rows = db.execute(
        select(
            Measurement.measured_at,
            Measurement.water_height_cm,
            Measurement.water_level_pct,
            Measurement.obstruction_pct,
            Measurement.rise_rate_cm_min,
        )
        .where(Measurement.point_id == point_id, Measurement.measured_at >= start)
        .order_by(Measurement.measured_at)
    ).all()
    size = bucket_minutes * 60
    buckets: dict[int, list] = {}
    for r in rows:
        buckets.setdefault(int(r.measured_at.timestamp() // size), []).append(r)
    out = []
    for key in sorted(buckets):
        items = buckets[key]
        obs = [i.obstruction_pct for i in items if i.obstruction_pct is not None]
        rises = [i.rise_rate_cm_min for i in items if i.rise_rate_cm_min is not None]
        out.append(
            HistoryBucket(
                bucket_start=datetime.fromtimestamp(key * size, tz=start.tzinfo),
                avg_height_cm=round(sum(i.water_height_cm for i in items) / len(items), 2),
                max_height_cm=max(i.water_height_cm for i in items),
                avg_level_pct=round(sum(i.water_level_pct for i in items) / len(items), 1),
                max_level_pct=max(i.water_level_pct for i in items),
                max_obstruction_pct=max(obs) if obs else None,
                max_rise_rate_cm_min=max(rises) if rises else None,
                samples=len(items),
            )
        )
    return out


def _alerts_query():
    return select(Alert).options(joinedload(Alert.point).joinedload(MeasurementPoint.zone))


@router.get("/alerts", response_model=list[AlertOut], summary="Liste des alertes (écran Alertes)")
def list_alerts(
    status: str | None = Query(default=None, pattern="^(open|resolved)$"),
    level: str | None = Query(default=None, pattern="^(orange|rouge)$"),
    zone_id: int | None = None,
    point_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    q = _alerts_query()
    if status:
        q = q.where(Alert.status == status)
    if level:
        q = q.where(Alert.level == level)
    if point_id:
        q = q.where(Alert.point_id == point_id)
    if zone_id:
        q = q.join(MeasurementPoint, Alert.point_id == MeasurementPoint.id).where(MeasurementPoint.zone_id == zone_id)
    alerts = db.scalars(q.order_by(Alert.created_at.desc()).limit(limit).offset(offset)).unique().all()
    return [alert_out(a) for a in alerts]


@router.get("/alerts/{alert_id}", response_model=AlertOut)
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException

    alert = db.scalar(_alerts_query().where(Alert.id == alert_id))
    if alert is None:
        raise HTTPException(404, "Alerte introuvable")
    return alert_out(alert)


@router.get("/stats/summary", summary="Compteurs globaux pour l'écran Accueil")
def summary(db: Session = Depends(get_db)):
    counts = dict(
        db.execute(
            select(MeasurementPoint.current_level, func.count()).where(MeasurementPoint.active.is_(True)).group_by(
                MeasurementPoint.current_level
            )
        ).all()
    )
    open_alerts = db.scalar(select(func.count()).select_from(Alert).where(Alert.status == "open")) or 0
    return {
        "rouge": counts.get("rouge", 0),
        "orange": counts.get("orange", 0),
        "vert": counts.get("vert", 0),
        "open_alerts": open_alerts,
    }
