from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import get_settings
from app.enums import ReportCategory, ReportStatus
from app.services.analysis import SENSOR_MAX_CM, SENSOR_MIN_CM
from app.utils import normalize_phone

Distance = Annotated[float, Field(ge=SENSOR_MIN_CM, le=SENSOR_MAX_CM, description="Distance capteur -> surface (cm)")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ zones
class ZoneBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    commune: str = Field(min_length=2, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ZoneCreate(ZoneBase):
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9-]{2,80}$")


class ZoneUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    commune: str | None = Field(default=None, min_length=2, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ZoneOut(ORM, ZoneBase):
    id: int
    slug: str


# ------------------------------------------------------------------ points
class PointBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    simulated: bool = False
    canal_depth_cm: float = Field(gt=0, le=1000, description="Hauteur utile de la canalisation (cm)")
    sensor_height_cm: float = Field(gt=0, le=SENSOR_MAX_CM, description="Distance capteur -> fond quand c'est vide (cm)")
    d0_cm: float | None = Field(default=None, ge=SENSOR_MIN_CM, le=SENSOR_MAX_CM)
    orange_pct: float = Field(default=50.0, gt=0, le=100)
    red_pct: float = Field(default=80.0, gt=0, le=200)
    fast_rise_cm_min: float = Field(default=2.0, gt=0)
    fast_rise_min_pct: float = Field(default=10.0, ge=0, le=100)
    obstruction_partial_pct: float = Field(default=20.0, ge=0, le=100)
    obstruction_strong_pct: float = Field(default=50.0, ge=0, le=100)
    obstruction_critical_pct: float = Field(default=80.0, ge=0, le=100)
    blockage_delta_cm: float = Field(default=10.0, gt=0)
    obstruction_from_level_sensor: bool = False

    @model_validator(mode="after")
    def _check_thresholds(self):
        if not self.orange_pct < self.red_pct:
            raise ValueError("orange_pct doit être inférieur à red_pct")
        if not self.obstruction_partial_pct < self.obstruction_strong_pct < self.obstruction_critical_pct:
            raise ValueError("Seuils d'obstruction : partielle < forte < critique")
        return self


class PointCreate(PointBase):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{2,40}$", description="Identifiant envoyé par l'ESP32")
    zone_id: int
    active: bool = True


class PointUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    zone_id: int | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    active: bool | None = None
    simulated: bool | None = None
    canal_depth_cm: float | None = Field(default=None, gt=0, le=1000)
    sensor_height_cm: float | None = Field(default=None, gt=0, le=SENSOR_MAX_CM)
    d0_cm: float | None = Field(default=None, ge=SENSOR_MIN_CM, le=SENSOR_MAX_CM)
    orange_pct: float | None = Field(default=None, gt=0, le=100)
    red_pct: float | None = Field(default=None, gt=0, le=200)
    fast_rise_cm_min: float | None = Field(default=None, gt=0)
    fast_rise_min_pct: float | None = Field(default=None, ge=0, le=100)
    obstruction_partial_pct: float | None = Field(default=None, ge=0, le=100)
    obstruction_strong_pct: float | None = Field(default=None, ge=0, le=100)
    obstruction_critical_pct: float | None = Field(default=None, ge=0, le=100)
    blockage_delta_cm: float | None = Field(default=None, gt=0)
    obstruction_from_level_sensor: bool | None = None


class CalibrateIn(BaseModel):
    d0_cm: float | None = Field(default=None, ge=SENSOR_MIN_CM, le=SENSOR_MAX_CM)
    use_latest_measurement: bool = False

    @model_validator(mode="after")
    def _one_source(self):
        if (self.d0_cm is None) == (not self.use_latest_measurement):
            if self.d0_cm is None:
                raise ValueError("Fournir d0_cm ou use_latest_measurement=true")
            raise ValueError("Fournir d0_cm OU use_latest_measurement, pas les deux")
        return self


class PointOut(ORM):
    id: int
    code: str
    name: str
    description: str | None
    zone: ZoneOut
    latitude: float
    longitude: float
    active: bool
    simulated: bool
    canal_depth_cm: float
    sensor_height_cm: float
    d0_cm: float | None
    orange_pct: float
    red_pct: float
    fast_rise_cm_min: float
    fast_rise_min_pct: float
    obstruction_partial_pct: float
    obstruction_strong_pct: float
    obstruction_critical_pct: float
    blockage_delta_cm: float
    obstruction_from_level_sensor: bool
    current_level: str
    level_color: str
    water_height_cm: float | None
    water_level_pct: float | None
    rise_rate_cm_min: float | None
    obstruction_pct: float | None
    obstruction_class: str | None
    blockage_detected: bool
    connectivity: str
    last_seen_at: datetime | None


class PointCreated(PointOut):
    api_key: str = Field(description="Clé d'API du capteur — affichée UNE seule fois")


class ApiKeyOut(BaseModel):
    api_key: str


# ------------------------------------------------------------------ mesures
class MeasurementReading(BaseModel):
    """Une mesure envoyée par l'ESP32 (HTTP ou MQTT)."""

    distance_cm: Distance | None = Field(default=None, description="Distance filtrée (capteur amont)")
    readings_cm: list[float] | None = Field(
        default=None, min_length=1, max_length=50, description="Lectures brutes : filtrées côté serveur"
    )
    distance_aval_cm: Distance | None = Field(default=None, description="Capteur aval (détection de blocage)")
    obstruction_distance_cm: Distance | None = Field(default=None, description="Lecture dédiée à l'obstruction")
    measured_at: datetime | None = Field(default=None, description="Horodatage (UTC) ; défaut = réception")
    simulated: bool = False

    @model_validator(mode="after")
    def _need_distance(self):
        if self.distance_cm is None and not self.readings_cm:
            raise ValueError("Fournir distance_cm ou readings_cm")
        return self


class MeasurementIn(MeasurementReading):
    point_code: str = Field(pattern=r"^[A-Za-z0-9_-]{2,40}$")


class MeasurementBatchIn(BaseModel):
    point_code: str = Field(pattern=r"^[A-Za-z0-9_-]{2,40}$")
    measurements: list[MeasurementReading] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _need_timestamps(self):
        if any(m.measured_at is None for m in self.measurements):
            raise ValueError("measured_at est obligatoire pour chaque mesure d'un lot")
        return self


class MeasurementOut(ORM):
    id: int
    point_id: int
    measured_at: datetime
    received_at: datetime
    source: str
    simulated: bool
    distance_cm: float
    readings_count: int
    distance_aval_cm: float | None
    obstruction_distance_cm: float | None
    water_height_cm: float
    water_level_pct: float
    rise_rate_cm_min: float | None
    obstruction_pct: float | None
    obstruction_class: str | None
    blockage_detected: bool
    alert_level: str


class IngestOut(BaseModel):
    measurement: MeasurementOut
    alert_level: str
    alert_id: int | None = Field(description="Renseigné si cette mesure a déclenché une nouvelle alerte")
    reasons: list[str]
    state_updated: bool = Field(description="False si la mesure est antérieure à la dernière connue (reçue en différé)")


class BatchIngestOut(BaseModel):
    accepted: int
    results: list[IngestOut]


class HistoryBucket(BaseModel):
    bucket_start: datetime
    avg_height_cm: float
    max_height_cm: float
    avg_level_pct: float
    max_level_pct: float
    max_obstruction_pct: float | None
    max_rise_rate_cm_min: float | None
    samples: int


# ------------------------------------------------------------------ alertes
class AlertOut(ORM):
    id: int
    point_id: int
    point_code: str
    point_name: str
    zone_id: int
    zone_name: str
    commune: str
    latitude: float
    longitude: float
    level: str
    status: str
    reason: str
    created_at: datetime
    resolved_at: datetime | None
    water_height_cm: float | None
    water_level_pct: float | None
    rise_rate_cm_min: float | None
    obstruction_pct: float | None
    obstruction_class: str | None
    blockage_detected: bool
    message_residents: str
    message_onpc: str
    message_source: str
    notified_at: datetime | None
    latency_ms: int | None


# ------------------------------------------------------------------ inscriptions / appareils
class SubscriptionIn(BaseModel):
    phone: str = Field(min_length=8, max_length=20, description="Ex. 0707123456 ou +2250707123456")
    zone_id: int

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v, get_settings().default_phone_region)


class SubscriptionUpdate(BaseModel):
    zone_id: int


class SubscriptionOut(BaseModel):
    id: int
    phone_masked: str
    zone: ZoneOut
    token: str | None = Field(default=None, description="Jeton de gestion (modif./désinscription), renvoyé une seule fois")
    already_subscribed: bool = False


class DeviceIn(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    platform: Literal["android", "ios", "web"] = "android"
    zone_id: int | None = None


class DeviceUnregister(BaseModel):
    token: str = Field(min_length=20, max_length=512)


# ------------------------------------------------------------------ signalements
class ReportIn(BaseModel):
    zone_id: int | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    category: ReportCategory
    description: str = Field(min_length=5, max_length=500)


class ReportOut(ORM):
    id: int
    zone_id: int | None
    latitude: float
    longitude: float
    category: str
    description: str
    status: str
    created_at: datetime


class ReportStatusUpdate(BaseModel):
    status: ReportStatus


# ------------------------------------------------------------------ autorités / admin
class DashboardSummary(BaseModel):
    total_points: int
    rouge: int
    orange: int
    vert: int
    offline: int
    open_alerts: int


class DashboardOut(BaseModel):
    generated_at: datetime
    summary: DashboardSummary
    points: list[PointOut]
    open_alerts: list[AlertOut]


class TestSmsIn(BaseModel):
    phone: str
    message: str = Field(default="Test IntelliFlood : SMS de démonstration.", max_length=160)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v, get_settings().default_phone_region)


class AiPreviewIn(BaseModel):
    """Scénario de test des messages IA (tâche « tester les messages sur plusieurs scénarios »)."""

    zone_name: str = "Cocody — Riviera"
    commune: str = "Cocody"
    point_name: str = "Canal de démonstration"
    level: Literal["orange", "rouge"] = "rouge"
    water_level_pct: float = Field(default=85.0, ge=0, le=300)
    water_height_cm: float = Field(default=42.5, ge=0)
    rise_rate_cm_min: float | None = 3.2
    obstruction_pct: float | None = 65.0
    obstruction_class: str | None = "forte"
    blockage_detected: bool = False


class AiPreviewOut(BaseModel):
    source: str
    error: str | None
    habitants: str
    habitants_length: int
    sms_text: str
    onpc: str


class LatencyStats(BaseModel):
    count: int
    avg_ms: float | None
    min_ms: int | None
    max_ms: int | None
    p95_ms: float | None
    recent: list[dict]


class NotificationLogOut(ORM):
    id: int
    alert_id: int | None
    channel: str
    recipient: str
    provider: str
    status: str
    error: str | None
    created_at: datetime
