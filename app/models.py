from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.db import Base, UTCDateTime
from app.enums import AlertLevel, AlertStatus, Connectivity, ObstructionClass, ReportStatus
from app.utils import utcnow


class Zone(Base):
    """Quartier / zone d'étude (ex. Cocody — Riviera). Les habitants s'inscrivent par zone."""

    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    commune: Mapped[str] = mapped_column(String(80))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    points: Mapped[list[MeasurementPoint]] = relationship(back_populates="zone")


class MeasurementPoint(Base):
    """Point de mesure (canalisation équipée d'un ESP32 + capteurs ultrasons, réel ou simulé)."""

    __tablename__ = "measurement_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    api_key_hash: Mapped[str | None] = mapped_column(String(64))

    # Géométrie (cm)
    canal_depth_cm: Mapped[float] = mapped_column(Float)  # hauteur utile
    sensor_height_cm: Mapped[float] = mapped_column(Float)  # capteur -> fond de la canalisation
    d0_cm: Mapped[float | None] = mapped_column(Float)  # distance de référence « canalisation propre »

    # Seuils configurables (valeurs illustratives, cf. cahier des charges §6)
    orange_pct: Mapped[float] = mapped_column(Float, default=50.0)
    red_pct: Mapped[float] = mapped_column(Float, default=80.0)
    fast_rise_cm_min: Mapped[float] = mapped_column(Float, default=2.0)
    fast_rise_min_pct: Mapped[float] = mapped_column(Float, default=10.0)
    obstruction_partial_pct: Mapped[float] = mapped_column(Float, default=20.0)
    obstruction_strong_pct: Mapped[float] = mapped_column(Float, default=50.0)
    obstruction_critical_pct: Mapped[float] = mapped_column(Float, default=80.0)
    blockage_delta_cm: Mapped[float] = mapped_column(Float, default=10.0)
    obstruction_from_level_sensor: Mapped[bool] = mapped_column(Boolean, default=False)

    # État courant (dénormalisé pour des listes rapides)
    current_level: Mapped[str] = mapped_column(String(10), default=AlertLevel.VERT.value)
    water_height_cm: Mapped[float | None] = mapped_column(Float)
    water_level_pct: Mapped[float | None] = mapped_column(Float)
    rise_rate_cm_min: Mapped[float | None] = mapped_column(Float)
    obstruction_pct: Mapped[float | None] = mapped_column(Float)
    obstruction_class: Mapped[str | None] = mapped_column(String(12))
    blockage_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    zone: Mapped[Zone] = relationship(back_populates="points")

    @property
    def level_color(self) -> str:
        return AlertLevel(self.current_level).color

    @property
    def connectivity(self) -> str:
        if self.last_seen_at is None:
            return Connectivity.UNKNOWN.value
        limit = timedelta(seconds=get_settings().sensor_offline_after_seconds)
        return (Connectivity.ONLINE if utcnow() - self.last_seen_at <= limit else Connectivity.OFFLINE).value

    @property
    def severity_key(self) -> tuple:
        obstruction = ObstructionClass(self.obstruction_class).rank if self.obstruction_class else 0
        return (
            -AlertLevel(self.current_level).rank,
            -obstruction,
            -(self.rise_rate_cm_min or 0.0),
            -(self.water_level_pct or 0.0),
            self.name,
        )


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (Index("ix_measurements_point_time", "point_id", "measured_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("measurement_points.id", ondelete="CASCADE"))
    measured_at: Mapped[datetime] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(16), default="http")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)

    distance_cm: Mapped[float] = mapped_column(Float)  # valeur filtrée (capteur amont)
    readings_count: Mapped[int] = mapped_column(Integer, default=1)
    distance_aval_cm: Mapped[float | None] = mapped_column(Float)
    obstruction_distance_cm: Mapped[float | None] = mapped_column(Float)

    water_height_cm: Mapped[float] = mapped_column(Float)
    water_level_pct: Mapped[float] = mapped_column(Float)
    rise_rate_cm_min: Mapped[float | None] = mapped_column(Float)
    obstruction_pct: Mapped[float | None] = mapped_column(Float)
    obstruction_class: Mapped[str | None] = mapped_column(String(12))
    blockage_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_level: Mapped[str] = mapped_column(String(10))

    point: Mapped[MeasurementPoint] = relationship()


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("measurement_points.id", ondelete="CASCADE"), index=True)
    measurement_id: Mapped[int | None] = mapped_column(ForeignKey("measurements.id", ondelete="SET NULL"))
    level: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default=AlertStatus.OPEN.value, index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    water_height_cm: Mapped[float | None] = mapped_column(Float)
    water_level_pct: Mapped[float | None] = mapped_column(Float)
    rise_rate_cm_min: Mapped[float | None] = mapped_column(Float)
    obstruction_pct: Mapped[float | None] = mapped_column(Float)
    obstruction_class: Mapped[str | None] = mapped_column(String(12))
    blockage_detected: Mapped[bool] = mapped_column(Boolean, default=False)

    message_residents: Mapped[str] = mapped_column(Text, default="")
    message_onpc: Mapped[str] = mapped_column(Text, default="")
    message_source: Mapped[str] = mapped_column(String(12), default="fallback")  # ia | fallback
    ai_error: Mapped[str | None] = mapped_column(Text)

    measurement_received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    point: Mapped[MeasurementPoint] = relationship()
    notifications: Mapped[list[NotificationLog]] = relationship(back_populates="alert", cascade="all, delete-orphan")


class Subscriber(Base):
    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # E.164
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # gestion (modif./désinscription)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    zone: Mapped[Zone] = relationship()


class DeviceToken(Base):
    """Jeton de notification push (FCM) d'un appareil de l'application mobile."""

    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(10), default="android")
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), index=True)  # None = toutes les zones
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(10))
    recipient: Mapped[str] = mapped_column(String(120))  # masqué
    provider: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(12))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    alert: Mapped[Alert | None] = relationship(back_populates="notifications")


class CitizenReport(Base):
    """Signalement citoyen d'un point critique (optionnel, F6.8)."""

    __tablename__ = "citizen_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default=ReportStatus.NEW.value, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    zone: Mapped[Zone | None] = relationship()
