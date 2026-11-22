"""Données de démonstration : zones d'Abidjan et points de mesure fictifs/simulés.

Usage :  python -m scripts.seed
Les coordonnées sont approximatives (points FICTIFS pour la démonstration, cf. cahier des charges §4.1).
Les clés d'API des points créés sont affichées une seule fois : notez-les pour les ESP32 / le simulateur.
"""

from sqlalchemy import select

from app.db import SessionLocal, init_db
from app.models import MeasurementPoint, Zone
from app.security import generate_api_key, hash_key

ZONES = [
    ("cocody-riviera", "Riviera", "Cocody", 5.3490, -3.9680),
    ("yopougon-sicogi", "Sicogi", "Yopougon", 5.3350, -4.0870),
    ("abobo-pk18", "PK 18", "Abobo", 5.4260, -4.0360),
    ("port-bouet-vridi", "Vridi", "Port-Bouët", 5.2550, -3.9700),
    ("adjame-liberte", "Liberté", "Adjamé", 5.3600, -4.0230),
    ("koumassi-remblais", "Remblais", "Koumassi", 5.2950, -3.9450),
]

# code, nom, zone slug, décalage lat/lon, profondeur utile (cm), hauteur capteur (cm), simulé
POINTS = [
    ("COC-001", "Canal Riviera — amont", "cocody-riviera", (0.0010, 0.0008), 100, 120, True),
    ("YOP-001", "Caniveau Sicogi", "yopougon-sicogi", (0.0008, -0.0010), 80, 100, True),
    ("ABO-001", "Canal PK 18", "abobo-pk18", (-0.0009, 0.0007), 120, 140, True),
    ("POR-001", "Collecteur Vridi", "port-bouet-vridi", (0.0006, 0.0006), 100, 120, True),
]


def main() -> None:
    init_db()
    with SessionLocal() as db:
        zones: dict[str, Zone] = {}
        for slug, name, commune, lat, lon in ZONES:
            zone = db.scalar(select(Zone).where(Zone.slug == slug))
            if zone is None:
                zone = Zone(slug=slug, name=name, commune=commune, latitude=lat, longitude=lon)
                db.add(zone)
                db.flush()
            zones[slug] = zone

        created = []
        for code, name, slug, (dlat, dlon), depth, sensor_h, simulated in POINTS:
            if db.scalar(select(MeasurementPoint).where(MeasurementPoint.code == code)):
                continue
            zone = zones[slug]
            key = generate_api_key()
            db.add(
                MeasurementPoint(
                    code=code,
                    name=name,
                    zone_id=zone.id,
                    latitude=(zone.latitude or 0) + dlat,
                    longitude=(zone.longitude or 0) + dlon,
                    canal_depth_cm=depth,
                    sensor_height_cm=sensor_h,
                    d0_cm=sensor_h,
                    simulated=simulated,
                    api_key_hash=hash_key(key),
                )
            )
            created.append((code, key))
        db.commit()

    print(f"{len(ZONES)} zones OK.")
    if created:
        print("Points créés — CLÉS D'API (affichées une seule fois) :")
        for code, key in created:
            print(f"  {code}: {key}")
    else:
        print("Aucun nouveau point (déjà présents).")


if __name__ == "__main__":
    main()
