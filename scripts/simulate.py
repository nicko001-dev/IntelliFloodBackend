"""Simulateur de capteur (remplace l'ESP32 / Wokwi) : envoie des scénarios à l'API.

Exemples :
  python -m scripts.simulate --api-key if_xxx --point COC-001 --scenario demo
  python -m scripts.simulate --api-key if_xxx --scenario fast_rise --delay 0.2

Les horodatages sont simulés (1 mesure = --step secondes simulées, finissant à « maintenant »), ce qui donne
des vitesses de montée réalistes même si l'envoi est rapide (--delay).
Scénarios : stable | slow_rise | fast_rise | obstruction | demo (l'eau monte, l'obstacle apparaît, l'alerte part).
"""

import argparse
import random
import time
from datetime import UTC, datetime, timedelta

import httpx


def scenario_samples(name: str, depth: float, sensor_h: float) -> list[dict]:
    """Retourne une liste de {water_cm, obstruction_cm|None, aval_water_cm|None}."""
    s: list[dict] = []

    def add(water, obstruction=None, aval=None):
        s.append({"water": water, "obstruction": obstruction, "aval": aval})

    if name == "stable":
        for _ in range(20):
            add(depth * 0.15)
    elif name == "slow_rise":
        for i in range(30):
            add(depth * (0.15 + 0.85 * i / 29 * 0.7))  # monte lentement jusqu'à ~65 %
    elif name == "fast_rise":
        for _ in range(5):
            add(depth * 0.15)
        for i in range(25):
            add(min(depth * 1.05, depth * 0.15 + i * depth * 0.045))  # montée rapide jusqu'à > 80 %
    elif name == "obstruction":
        for i in range(25):
            obstacle = min(0.85, i * 0.04)  # l'obstacle grandit : 0 -> 85 %
            add(depth * 0.2, obstruction=sensor_h - depth * obstacle)
    elif name == "demo":
        for _ in range(8):
            add(depth * 0.15, obstruction=sensor_h)  # calme, canalisation propre
        for i in range(12):
            add(depth * (0.15 + i * 0.035), obstruction=sensor_h)  # l'eau monte (-> orange)
        for i in range(10):
            add(depth * 0.55, obstruction=sensor_h - depth * min(0.8, 0.1 + i * 0.08))  # l'obstacle apparaît
        for i in range(10):
            add(depth * (0.6 + i * 0.03), obstruction=sensor_h - depth * 0.8)  # -> rouge
    else:
        raise SystemExit(f"Scénario inconnu : {name}")
    return s


def noisy_readings(true_distance: float) -> list[float]:
    readings = [round(true_distance + random.uniform(-0.6, 0.6), 1) for _ in range(5)]
    if random.random() < 0.2:
        readings[random.randrange(5)] = random.choice([0.0, 399.0, true_distance + 80])  # lecture aberrante
    return readings


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--api-key", required=True)
    p.add_argument("--point", default="COC-001")
    p.add_argument("--scenario", default="demo", choices=["stable", "slow_rise", "fast_rise", "obstruction", "demo"])
    p.add_argument("--depth", type=float, default=100, help="hauteur utile du point (cm)")
    p.add_argument("--sensor-height", type=float, default=120, help="distance capteur->fond (cm)")
    p.add_argument("--step", type=float, default=30, help="secondes simulées entre deux mesures")
    p.add_argument("--delay", type=float, default=0.3, help="secondes réelles entre deux envois")
    a = p.parse_args()

    samples = scenario_samples(a.scenario, a.depth, a.sensor_height)
    start = datetime.now(UTC) - timedelta(seconds=a.step * (len(samples) - 1))
    with httpx.Client(base_url=a.base_url, headers={"X-API-Key": a.api_key}, timeout=15) as client:
        for i, smp in enumerate(samples):
            body = {
                "point_code": a.point,
                "readings_cm": noisy_readings(a.sensor_height - smp["water"]),
                "measured_at": (start + timedelta(seconds=a.step * i)).isoformat(),
                "simulated": True,
            }
            if smp["obstruction"] is not None:
                body["obstruction_distance_cm"] = round(max(2.0, smp["obstruction"]), 1)
            r = client.post("/api/v1/measurements", json=body)
            if r.status_code >= 400:
                raise SystemExit(f"Erreur {r.status_code}: {r.text}")
            d = r.json()
            m = d["measurement"]
            alert = f"  >>> ALERTE #{d['alert_id']}" if d["alert_id"] else ""
            print(
                f"[{i + 1:02d}/{len(samples)}] niveau={m['water_level_pct']:5.1f}%  montée={m['rise_rate_cm_min']}  "
                f"obstruction={m['obstruction_class']}  -> {d['alert_level'].upper()}{alert}"
            )
            time.sleep(a.delay)


if __name__ == "__main__":
    main()
