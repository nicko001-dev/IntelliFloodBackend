import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ENVIRONMENT"] = "test"
os.environ["ADMIN_API_KEY"] = "test-admin-key-1234567890"
os.environ.pop("SENSOR_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import *  # noqa: E402,F401,F403

ADMIN = {"X-Admin-Key": "test-admin-key-1234567890"}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin():
    return ADMIN


@pytest.fixture
def setup(client):
    """Une zone + un point (hauteur utile 100 cm, capteur à 120 cm) ; retourne ids et clé d'API."""
    z = client.post("/api/v1/admin/zones", json={"name": "Riviera", "commune": "Cocody", "latitude": 5.35, "longitude": -3.97}, headers=ADMIN)
    assert z.status_code == 201, z.text
    zone_id = z.json()["id"]
    p = client.post(
        "/api/v1/admin/points",
        json={
            "code": "COC-001", "name": "Canal Riviera", "zone_id": zone_id, "latitude": 5.351, "longitude": -3.968,
            "canal_depth_cm": 100, "sensor_height_cm": 120, "d0_cm": 120,
        },
        headers=ADMIN,
    )
    assert p.status_code == 201, p.text
    body = p.json()
    return {"zone_id": zone_id, "point_id": body["id"], "key": body["api_key"], "headers": {"X-API-Key": body["api_key"]}}


def send(client, setup, water_cm=None, **extra):
    """Envoie une mesure : water_cm = hauteur d'eau voulue ; distance = 120 - water."""
    body = {"point_code": "COC-001", **extra}
    if water_cm is not None:
        body["distance_cm"] = 120 - water_cm
    return client.post("/api/v1/measurements", json=body, headers=setup["headers"])
