"""Écritures publiques : inscription aux alertes SMS, enregistrement push, signalements citoyens."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_zone_or_404
from app.models import CitizenReport, DeviceToken, Subscriber
from app.ratelimit import RateLimiter
from app.schemas import (
    DeviceIn,
    DeviceUnregister,
    ReportIn,
    ReportOut,
    SubscriptionIn,
    SubscriptionOut,
    SubscriptionUpdate,
    ZoneOut,
)
from app.utils import mask_phone, utcnow

router = APIRouter(tags=["Habitants"])
_limit_subscribe = RateLimiter(10, 3600, "subscribe")
_limit_device = RateLimiter(60, 3600, "device")
_limit_report = RateLimiter(20, 3600, "report")


def _sub_out(sub: Subscriber, *, token: bool, already: bool = False) -> SubscriptionOut:
    return SubscriptionOut(
        id=sub.id,
        phone_masked=mask_phone(sub.phone),
        zone=ZoneOut.model_validate(sub.zone),
        token=sub.token if token else None,
        already_subscribed=already,
    )


@router.post(
    "/subscriptions",
    response_model=SubscriptionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_limit_subscribe)],
    summary="S'inscrire aux alertes SMS (quartier + numéro)",
)
def subscribe(payload: SubscriptionIn, response: Response, db: Session = Depends(get_db)):
    zone = get_zone_or_404(db, payload.zone_id)
    existing = db.scalar(select(Subscriber).where(Subscriber.phone == payload.phone))
    if existing is not None:
        # On ne révèle jamais le jeton ni ne modifie une inscription existante (numéro non vérifié).
        response.status_code = status.HTTP_200_OK
        return _sub_out(existing, token=False, already=True)
    sub = Subscriber(phone=payload.phone, zone_id=zone.id, token=secrets.token_urlsafe(32))
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return _sub_out(sub, token=True)


@router.patch("/subscriptions/{token}", response_model=SubscriptionOut, summary="Changer de quartier")
def change_zone(token: str, payload: SubscriptionUpdate, db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscriber).where(Subscriber.token == token))
    if sub is None:
        raise HTTPException(404, "Inscription introuvable")
    sub.zone_id = get_zone_or_404(db, payload.zone_id).id
    db.commit()
    db.refresh(sub)
    return _sub_out(sub, token=False)


@router.delete("/subscriptions/{token}", status_code=status.HTTP_204_NO_CONTENT, summary="Se désinscrire")
def unsubscribe(token: str, db: Session = Depends(get_db)):
    sub = db.scalar(select(Subscriber).where(Subscriber.token == token))
    if sub is None:
        raise HTTPException(404, "Inscription introuvable")
    db.delete(sub)  # suppression réelle : collecte minimale des données personnelles
    db.commit()


@router.post(
    "/devices",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_limit_device)],
    summary="Enregistrer le jeton push (FCM) de l'appareil",
)
def register_device(payload: DeviceIn, db: Session = Depends(get_db)):
    if payload.zone_id is not None:
        get_zone_or_404(db, payload.zone_id)
    dev = db.scalar(select(DeviceToken).where(DeviceToken.token == payload.token))
    if dev is None:
        db.add(DeviceToken(token=payload.token, platform=payload.platform, zone_id=payload.zone_id))
    else:
        dev.platform, dev.zone_id, dev.active, dev.last_seen_at = payload.platform, payload.zone_id, True, utcnow()
    db.commit()


@router.post("/devices/unregister", status_code=status.HTTP_204_NO_CONTENT, summary="Retirer le jeton push")
def unregister_device(payload: DeviceUnregister, db: Session = Depends(get_db)):
    dev = db.scalar(select(DeviceToken).where(DeviceToken.token == payload.token))
    if dev is not None:
        db.delete(dev)
        db.commit()


@router.post(
    "/reports",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_limit_report)],
    summary="Signaler un point critique (optionnel)",
)
def create_report(payload: ReportIn, db: Session = Depends(get_db)):
    if payload.zone_id is not None:
        get_zone_or_404(db, payload.zone_id)
    report = CitizenReport(**payload.model_dump(mode="json"))
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.get("/reports", response_model=list[ReportOut], summary="Signalements vérifiés (carte)")
def list_verified_reports(
    zone_id: int | None = None, limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)
):
    q = select(CitizenReport).where(CitizenReport.status == "verified")
    if zone_id:
        q = q.where(CitizenReport.zone_id == zone_id)
    return db.scalars(q.order_by(CitizenReport.created_at.desc()).limit(limit)).all()
