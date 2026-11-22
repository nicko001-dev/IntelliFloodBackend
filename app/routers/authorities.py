"""Vue autorités (ONPC / SODEXAM) : tableau de tous les points classés par gravité (F6.6)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.deps import alert_out
from app.enums import Connectivity
from app.models import Alert, MeasurementPoint
from app.schemas import DashboardOut, DashboardSummary, PointOut
from app.security import require_admin
from app.utils import utcnow

router = APIRouter(prefix="/authorities", tags=["Autorités"], dependencies=[Depends(require_admin)])


@router.get("/dashboard", response_model=DashboardOut, summary="Tableau de bord classé par gravité")
def dashboard(db: Session = Depends(get_db)):
    points = db.scalars(
        select(MeasurementPoint).options(joinedload(MeasurementPoint.zone)).where(MeasurementPoint.active.is_(True))
    ).all()
    points = sorted(points, key=lambda p: p.severity_key)
    open_alerts = (
        db.scalars(
            select(Alert)
            .options(joinedload(Alert.point).joinedload(MeasurementPoint.zone))
            .where(Alert.status == "open")
            .order_by(Alert.created_at.desc())
        )
        .unique()
        .all()
    )
    count = lambda lvl: sum(1 for p in points if p.current_level == lvl)  # noqa: E731
    return DashboardOut(
        generated_at=utcnow(),
        summary=DashboardSummary(
            total_points=len(points),
            rouge=count("rouge"),
            orange=count("orange"),
            vert=count("vert"),
            offline=sum(1 for p in points if p.connectivity == Connectivity.OFFLINE.value),
            open_alerts=len(open_alerts),
        ),
        points=[PointOut.model_validate(p) for p in points],
        open_alerts=[alert_out(a) for a in open_alerts],
    )
