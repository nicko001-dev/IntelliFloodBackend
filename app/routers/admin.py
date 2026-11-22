"""Administration (clé X-Admin-Key) : zones, points, calibration, clés capteurs, outils de test."""

import statistics

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import alert_out, get_point_or_404, get_zone_or_404, unique_slug
from app.enums import AlertStatus
from app.models import Alert, CitizenReport, Measurement, MeasurementPoint, NotificationLog, Subscriber, Zone
from app.schemas import (
    AiPreviewIn,
    AiPreviewOut,
    AlertOut,
    ApiKeyOut,
    CalibrateIn,
    LatencyStats,
    NotificationLogOut,
    PointCreate,
    PointCreated,
    PointOut,
    PointUpdate,
    ReportOut,
    ReportStatusUpdate,
    TestSmsIn,
    ZoneCreate,
    ZoneOut,
    ZoneUpdate,
)
from app.security import generate_api_key, hash_key, require_admin
from app.services.ai import generate_messages
from app.services.dispatch import get_sms_provider
from app.services.messages import SMS_LIMIT, AlertContext, to_sms_text
from app.utils import utcnow

router = APIRouter(prefix="/admin", tags=["Administration"], dependencies=[Depends(require_admin)])


# ------------------------------------------------------------------ zones
@router.post("/zones", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
def create_zone(payload: ZoneCreate, db: Session = Depends(get_db)):
    slug = payload.slug or unique_slug(db, f"{payload.commune}-{payload.name}")
    if db.scalar(select(Zone.id).where(Zone.slug == slug)):
        raise HTTPException(409, "Ce slug existe déjà")
    zone = Zone(slug=slug, **payload.model_dump(exclude={"slug"}))
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


@router.patch("/zones/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: int, payload: ZoneUpdate, db: Session = Depends(get_db)):
    zone = get_zone_or_404(db, zone_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(zone, k, v)
    db.commit()
    db.refresh(zone)
    return zone


# ------------------------------------------------------------------ points
def _load_point(db: Session, point_id: int) -> MeasurementPoint:
    return db.scalar(
        select(MeasurementPoint).options(joinedload(MeasurementPoint.zone)).where(MeasurementPoint.id == point_id)
    )


@router.post("/points", response_model=PointCreated, status_code=status.HTTP_201_CREATED, summary="Créer un point de mesure")
def create_point(payload: PointCreate, db: Session = Depends(get_db)):
    get_zone_or_404(db, payload.zone_id)
    if db.scalar(select(MeasurementPoint.id).where(MeasurementPoint.code == payload.code)):
        raise HTTPException(409, "Ce code de point existe déjà")
    api_key = generate_api_key()
    point = MeasurementPoint(**payload.model_dump(), api_key_hash=hash_key(api_key))
    db.add(point)
    db.commit()
    base = PointOut.model_validate(_load_point(db, point.id))
    return PointCreated(**base.model_dump(), api_key=api_key)


@router.patch("/points/{point_id}", response_model=PointOut, summary="Modifier géométrie / seuils / état")
def update_point(point_id: int, payload: PointUpdate, db: Session = Depends(get_db)):
    point = get_point_or_404(db, point_id)
    changes = payload.model_dump(exclude_unset=True)
    if "zone_id" in changes:
        get_zone_or_404(db, changes["zone_id"])
    for k, v in changes.items():
        if v is None and k not in ("d0_cm", "description"):
            continue
        setattr(point, k, v)
    if not point.orange_pct < point.red_pct or not (
        point.obstruction_partial_pct < point.obstruction_strong_pct < point.obstruction_critical_pct
    ):
        db.rollback()
        raise HTTPException(422, "Seuils incohérents (orange < rouge ; obstruction partielle < forte < critique)")
    db.commit()
    return _load_point(db, point_id)


@router.post("/points/{point_id}/calibrate", response_model=PointOut, summary="Enregistrer D0 (canalisation propre)")
def calibrate(point_id: int, payload: CalibrateIn, db: Session = Depends(get_db)):
    point = get_point_or_404(db, point_id)
    if payload.d0_cm is not None:
        point.d0_cm = payload.d0_cm
    else:
        recent = db.scalars(
            select(Measurement.distance_cm)
            .where(Measurement.point_id == point_id)
            .order_by(Measurement.measured_at.desc())
            .limit(10)
        ).all()
        if not recent:
            raise HTTPException(409, "Aucune mesure disponible pour calibrer ce point")
        point.d0_cm = round(statistics.median(recent), 1)
    db.commit()
    return _load_point(db, point_id)


@router.post("/points/{point_id}/rotate-key", response_model=ApiKeyOut, summary="Régénérer la clé d'API du capteur")
def rotate_key(point_id: int, db: Session = Depends(get_db)):
    point = get_point_or_404(db, point_id)
    key = generate_api_key()
    point.api_key_hash = hash_key(key)
    db.commit()
    return ApiKeyOut(api_key=key)


# ------------------------------------------------------------------ alertes / signalements
@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut, summary="Clôturer manuellement une alerte")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.scalar(
        select(Alert).options(joinedload(Alert.point).joinedload(MeasurementPoint.zone)).where(Alert.id == alert_id)
    )
    if alert is None:
        raise HTTPException(404, "Alerte introuvable")
    alert.status, alert.resolved_at = AlertStatus.RESOLVED.value, utcnow()
    db.commit()
    return alert_out(alert)


@router.get("/reports", response_model=list[ReportOut], summary="Tous les signalements citoyens")
def all_reports(
    status_: str | None = Query(default=None, alias="status", pattern="^(new|verified|rejected)$"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = select(CitizenReport)
    if status_:
        q = q.where(CitizenReport.status == status_)
    return db.scalars(q.order_by(CitizenReport.created_at.desc()).limit(limit)).all()


@router.patch("/reports/{report_id}", response_model=ReportOut, summary="Vérifier / rejeter un signalement")
def update_report(report_id: int, payload: ReportStatusUpdate, db: Session = Depends(get_db)):
    report = db.get(CitizenReport, report_id)
    if report is None:
        raise HTTPException(404, "Signalement introuvable")
    report.status = payload.status.value
    db.commit()
    return report


# ------------------------------------------------------------------ outils de test (modules IA / SMS) et statistiques
@router.post("/ai/preview", response_model=AiPreviewOut, summary="Tester les messages IA sur un scénario")
def ai_preview(payload: AiPreviewIn, settings: Settings = Depends(get_settings)):
    ctx = AlertContext(
        zone_name=payload.zone_name,
        commune=payload.commune,
        point_name=payload.point_name,
        level=payload.level,
        water_level_pct=payload.water_level_pct,
        water_height_cm=payload.water_height_cm,
        rise_rate_cm_min=payload.rise_rate_cm_min,
        obstruction_pct=payload.obstruction_pct,
        obstruction_class=payload.obstruction_class,
        blockage_detected=payload.blockage_detected,
    )
    gen = generate_messages(ctx, settings)
    return AiPreviewOut(
        source=gen.source,
        error=gen.error,
        habitants=gen.messages.habitants,
        habitants_length=len(gen.messages.habitants),
        sms_text=to_sms_text(gen.messages.habitants, SMS_LIMIT),
        onpc=gen.messages.onpc,
    )


@router.post("/notifications/test-sms", summary="Envoyer un SMS de test (vérifier fournisseur et numéros)")
def test_sms(payload: TestSmsIn):
    provider = get_sms_provider()
    res = provider.send(payload.phone, to_sms_text(payload.message, SMS_LIMIT))
    return {"provider": provider.name, "ok": res.ok, "simulated": res.simulated, "error": res.error}


@router.get("/notifications", response_model=list[NotificationLogOut], summary="Journal des notifications")
def notification_log(
    alert_id: int | None = None, limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)
):
    q = select(NotificationLog)
    if alert_id:
        q = q.where(NotificationLog.alert_id == alert_id)
    return db.scalars(q.order_by(NotificationLog.created_at.desc()).limit(limit)).all()


@router.get("/stats/latency", response_model=LatencyStats, summary="Délai mesure reçue -> première notification")
def latency(limit: int = Query(default=100, ge=1, le=1000), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Alert.id, Alert.level, Alert.latency_ms, Alert.created_at)
        .where(Alert.latency_ms.is_not(None))
        .order_by(Alert.created_at.desc())
        .limit(limit)
    ).all()
    values = [r.latency_ms for r in rows]
    return LatencyStats(
        count=len(values),
        avg_ms=round(statistics.fmean(values), 1) if values else None,
        min_ms=min(values) if values else None,
        max_ms=max(values) if values else None,
        p95_ms=round(statistics.quantiles(values, n=20)[-1], 1) if len(values) >= 2 else (values[0] if values else None),
        recent=[
            {"alert_id": r.id, "level": r.level, "latency_ms": r.latency_ms, "created_at": r.created_at.isoformat()}
            for r in rows[:20]
        ],
    )


@router.get("/stats/overview", summary="Compteurs d'administration")
def overview(db: Session = Depends(get_db)):
    count = lambda model: db.scalar(select(func.count()).select_from(model)) or 0  # noqa: E731
    return {
        "zones": count(Zone),
        "points": count(MeasurementPoint),
        "measurements": count(Measurement),
        "alerts": count(Alert),
        "subscribers": count(Subscriber),
        "reports": count(CitizenReport),
    }
