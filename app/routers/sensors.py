"""Réception des mesures des capteurs (ESP32) — protégée par clé d'API (F3.5)."""

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import MeasurementPoint
from app.schemas import BatchIngestOut, IngestOut, MeasurementBatchIn, MeasurementIn, MeasurementOut
from app.security import global_sensor_key_valid, sensor_key_valid
from app.services.dispatch import dispatch_alert
from app.services.ingestion import IngestResult, ingest_measurement
from app.services.realtime import alert_event, measurement_event, publish

router = APIRouter(prefix="/measurements", tags=["Capteurs"])


def _authenticated_point(db: Session, code: str, api_key: str | None, settings: Settings) -> MeasurementPoint:
    point = db.scalar(select(MeasurementPoint).where(MeasurementPoint.code == code).with_for_update())
    if point is None:
        if global_sensor_key_valid(api_key, settings):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Point de mesure inconnu")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé d'API invalide ou absente (X-API-Key)")
    if not sensor_key_valid(point, api_key, settings):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé d'API invalide ou absente (X-API-Key)")
    if not point.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Point de mesure désactivé")
    return point


def _to_out(result: IngestResult) -> IngestOut:
    return IngestOut(
        measurement=MeasurementOut.model_validate(result.measurement),
        alert_level=result.evaluation.level,
        alert_id=result.alert.id if result.alert else None,
        reasons=result.evaluation.reasons,
        state_updated=result.state_updated,
    )


def _publish(point: MeasurementPoint, result: IngestResult, background: BackgroundTasks) -> None:
    if result.state_updated:
        publish(measurement_event(point, result.measurement))
    if result.alert:
        publish(alert_event(result.alert, "alert_created"))
        background.add_task(dispatch_alert, result.alert.id)


@router.post("", response_model=IngestOut, status_code=status.HTTP_201_CREATED, summary="Envoyer une mesure")
def post_measurement(
    payload: MeasurementIn,
    background: BackgroundTasks,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    point = _authenticated_point(db, payload.point_code, x_api_key, settings)
    try:
        result = ingest_measurement(db, point, payload, source="http")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    _publish(point, result, background)
    return _to_out(result)


@router.post(
    "/batch",
    response_model=BatchIngestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Envoyer un lot de mesures (rattrapage après coupure réseau)",
)
def post_batch(
    payload: MeasurementBatchIn,
    background: BackgroundTasks,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    point = _authenticated_point(db, payload.point_code, x_api_key, settings)
    ordered = sorted(payload.measurements, key=lambda m: m.measured_at)  # type: ignore[arg-type,return-value]
    results: list[IngestResult] = []
    try:
        for reading in ordered:
            results.append(ingest_measurement(db, point, reading, source="http"))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    for r in results:
        _publish(point, r, background)
    return BatchIngestOut(accepted=len(results), results=[_to_out(r) for r in results])
