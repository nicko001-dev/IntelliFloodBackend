"""Logique métier pure (sans base de données) : filtrage, hauteur d'eau, vitesse de montée,
obstruction, niveau d'alerte et règle combinée. Facile à tester et à expliquer au jury.

Convention : le niveau d'alerte est décidé UNIQUEMENT ici, à partir des seuils (F4.1) — jamais par l'IA.
Bornes : vert < 50 % ; orange 50–80 % (ou montée rapide) ; rouge > 80 %.
         libre < 20 % ; partielle [20, 50[ ; forte [50, 80] ; critique > 80 %.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.enums import AlertLevel, ObstructionClass

SENSOR_MIN_CM = 2.0  # plage utile du HC-SR04
SENSOR_MAX_CM = 400.0


@dataclass(frozen=True)
class PointConfig:
    canal_depth_cm: float
    sensor_height_cm: float
    d0_cm: float
    orange_pct: float = 50.0
    red_pct: float = 80.0
    fast_rise_cm_min: float = 2.0
    fast_rise_min_pct: float = 10.0
    obstruction_partial_pct: float = 20.0
    obstruction_strong_pct: float = 50.0
    obstruction_critical_pct: float = 80.0
    blockage_delta_cm: float = 10.0
    obstruction_from_level_sensor: bool = False

    @classmethod
    def from_point(cls, p) -> PointConfig:
        return cls(
            canal_depth_cm=p.canal_depth_cm,
            sensor_height_cm=p.sensor_height_cm,
            d0_cm=p.d0_cm if p.d0_cm is not None else p.sensor_height_cm,
            orange_pct=p.orange_pct,
            red_pct=p.red_pct,
            fast_rise_cm_min=p.fast_rise_cm_min,
            fast_rise_min_pct=p.fast_rise_min_pct,
            obstruction_partial_pct=p.obstruction_partial_pct,
            obstruction_strong_pct=p.obstruction_strong_pct,
            obstruction_critical_pct=p.obstruction_critical_pct,
            blockage_delta_cm=p.blockage_delta_cm,
            obstruction_from_level_sensor=p.obstruction_from_level_sensor,
        )


@dataclass(frozen=True)
class Evaluation:
    distance_cm: float
    water_height_cm: float
    water_level_pct: float
    rise_rate_cm_min: float | None
    obstruction_pct: float | None
    obstruction_class: str | None
    blockage_detected: bool
    water_level: str  # niveau lié à l'eau seule (avant règle combinée)
    level: str  # niveau final
    reasons: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- filtrage (F1.2)
def filter_readings(readings: Sequence[float]) -> tuple[float, int]:
    """Moyenne robuste : écarte les valeurs hors plage et les aberrations (médiane + MAD).

    Retourne (valeur filtrée, nombre de lectures conservées). Lève ValueError si aucune lecture valide.
    """
    valid = [float(r) for r in readings if math.isfinite(r) and SENSOR_MIN_CM <= r <= SENSOR_MAX_CM]
    if not valid:
        raise ValueError("Aucune lecture valide (plage capteur 2–400 cm)")
    med = statistics.median(valid)
    mad = statistics.median(abs(v - med) for v in valid)
    tolerance = max(3 * 1.4826 * mad, 2.0)
    kept = [v for v in valid if abs(v - med) <= tolerance]
    return round(statistics.fmean(kept), 2), len(kept)


# --------------------------------------------------------------------------- niveau d'eau (F1.1)
def water_height_cm(cfg: PointConfig, distance_cm: float) -> float:
    """Hauteur d'eau = distance capteur->fond (canalisation vide) − distance mesurée, bornée à ≥ 0."""
    return round(max(0.0, cfg.sensor_height_cm - distance_cm), 2)


def water_level_pct(cfg: PointConfig, height_cm: float) -> float:
    return round(height_cm / cfg.canal_depth_cm * 100.0, 1)


# --------------------------------------------------------------------------- vitesse de montée (F1.3)
def compute_rise_rate(
    now: datetime,
    height_cm: float,
    history: Sequence[tuple[datetime, float]],
    window_minutes: float,
    min_span_seconds: float = 15.0,
) -> float | None:
    """Pente (cm/min) par régression linéaire sur la fenêtre glissante (historique + mesure courante).

    Retourne None s'il n'y a pas assez de recul (moins de 2 points ou fenêtre trop courte).
    """
    points = [(t, h) for t, h in history if 0 < (now - t).total_seconds() <= window_minutes * 60]
    points.append((now, height_cm))
    if len(points) < 2:
        return None
    t0 = min(t for t, _ in points)
    xs = [(t - t0).total_seconds() / 60.0 for t, _ in points]
    ys = [h for _, h in points]
    if (max(xs) - min(xs)) * 60.0 < min_span_seconds:
        return None
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denom
    return round(slope, 2)


# --------------------------------------------------------------------------- obstruction (F2.x)
def obstruction_rate_pct(cfg: PointConfig, distance_cm: float) -> float:
    """Taux d'obstruction = (D0 − D mesurée) / hauteur utile × 100, borné à [0, 100]."""
    rate = (cfg.d0_cm - distance_cm) / cfg.canal_depth_cm * 100.0
    return round(min(100.0, max(0.0, rate)), 1)


def classify_obstruction(cfg: PointConfig, rate_pct: float) -> ObstructionClass:
    if rate_pct > cfg.obstruction_critical_pct:
        return ObstructionClass.CRITIQUE
    if rate_pct >= cfg.obstruction_strong_pct:
        return ObstructionClass.FORTE
    if rate_pct >= cfg.obstruction_partial_pct:
        return ObstructionClass.PARTIELLE
    return ObstructionClass.LIBRE


def detect_blockage(cfg: PointConfig, upstream_height_cm: float, downstream_height_cm: float | None) -> bool:
    """Blocage pendant l'écoulement : l'eau s'accumule en amont par rapport à l'aval (F2.3).

    Hypothèse : les deux capteurs sont montés à la même hauteur (sensor_height_cm).
    """
    if downstream_height_cm is None:
        return False
    return upstream_height_cm > 0 and (upstream_height_cm - downstream_height_cm) >= cfg.blockage_delta_cm


# --------------------------------------------------------------------------- niveau d'alerte (F1.4, F2.5)
def classify_water_level(cfg: PointConfig, pct: float, rise_rate: float | None) -> AlertLevel:
    if pct > cfg.red_pct:
        return AlertLevel.ROUGE
    if pct >= cfg.orange_pct:
        return AlertLevel.ORANGE
    if rise_rate is not None and rise_rate >= cfg.fast_rise_cm_min and pct >= cfg.fast_rise_min_pct:
        return AlertLevel.ORANGE  # montée rapide
    return AlertLevel.VERT


def evaluate(
    cfg: PointConfig,
    *,
    distance_cm: float,
    distance_aval_cm: float | None = None,
    obstruction_distance_cm: float | None = None,
    rise_rate_cm_min: float | None = None,
) -> Evaluation:
    height = water_height_cm(cfg, distance_cm)
    pct = water_level_pct(cfg, height)
    water_level = classify_water_level(cfg, pct, rise_rate_cm_min)
    reasons: list[str] = []

    if pct > cfg.red_pct:
        reasons.append(f"niveau d'eau à {pct:.0f} % de la hauteur utile (seuil rouge : {cfg.red_pct:.0f} %)")
    elif pct >= cfg.orange_pct:
        reasons.append(f"niveau d'eau à {pct:.0f} % de la hauteur utile (seuil orange : {cfg.orange_pct:.0f} %)")
    elif water_level == AlertLevel.ORANGE:
        reasons.append(f"montée rapide de l'eau ({rise_rate_cm_min:.1f} cm/min)")

    # Obstruction
    obstruction_pct: float | None = None
    obstruction_cls: ObstructionClass | None = None
    if obstruction_distance_cm is not None:
        obstruction_pct = obstruction_rate_pct(cfg, obstruction_distance_cm)
    elif cfg.obstruction_from_level_sensor:
        obstruction_pct = obstruction_rate_pct(cfg, distance_cm)
    if obstruction_pct is not None:
        obstruction_cls = classify_obstruction(cfg, obstruction_pct)

    blockage = False
    if distance_aval_cm is not None:
        blockage = detect_blockage(cfg, height, water_height_cm(cfg, distance_aval_cm))
    if blockage:
        reasons.append("blocage détecté : l'eau s'accumule en amont par rapport à l'aval")
        if obstruction_cls is None or obstruction_cls == ObstructionClass.LIBRE:
            obstruction_cls = ObstructionClass.PARTIELLE  # un blocage en écoulement n'est jamais « libre »

    if obstruction_cls in (ObstructionClass.FORTE, ObstructionClass.CRITIQUE) and obstruction_pct is not None:
        reasons.append(f"obstruction {obstruction_cls.value} ({obstruction_pct:.0f} %)")

    # Règle combinée : obstruction forte (ou critique) + eau orange => rouge (F2.5)
    level = water_level
    if water_level == AlertLevel.ORANGE and obstruction_cls in (ObstructionClass.FORTE, ObstructionClass.CRITIQUE):
        level = AlertLevel.ROUGE
        reasons.append("règle combinée : obstruction forte + niveau orange => alerte rouge")

    return Evaluation(
        distance_cm=distance_cm,
        water_height_cm=height,
        water_level_pct=pct,
        rise_rate_cm_min=rise_rate_cm_min,
        obstruction_pct=obstruction_pct,
        obstruction_class=obstruction_cls.value if obstruction_cls else None,
        blockage_detected=blockage,
        water_level=water_level.value,
        level=level.value,
        reasons=reasons,
    )
