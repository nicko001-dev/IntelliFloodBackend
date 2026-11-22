import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.db import init_db
from app.routers import admin, authorities, citizens, health, public, sensors, ws

API_PREFIX = "/api/v1"

DESCRIPTION = """
API du système d'alerte précoce d'inondation **IntelliFlood** (capteur → serveur → IA → alerte → carte).

* **Capteurs** : `POST /api/v1/measurements` avec l'en-tête `X-API-Key`.
* **Application mobile** : zones, points, carte GeoJSON, historique, alertes, inscription SMS, push, signalements, WebSocket `/ws`.
* **Autorités / administration** : en-tête `X-Admin-Key`.

Le niveau d'alerte est décidé **uniquement par les seuils** ; l'IA ne fait que rédiger les messages.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.assert_production_ready()
    if settings.auto_create_tables:
        init_db()
    mqtt_client = None
    if settings.mqtt_enabled:
        from app.services.mqtt import start_mqtt

        mqtt_client = start_mqtt(settings)
    yield
    if mqtt_client is not None:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=__version__, description=DESCRIPTION, lifespan=lifespan)
    origins = settings.cors_origins_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix=API_PREFIX)
    for r in (sensors, public, citizens, authorities, admin):
        app.include_router(r.router, prefix=API_PREFIX)
    app.include_router(ws.router)
    return app


app = create_app()
