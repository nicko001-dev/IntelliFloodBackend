"""Ingestion MQTT optionnelle (alternative au HTTP) — topic : intelliflood/points/<code>/measurements.

Le payload JSON est identique à celui de POST /api/v1/measurements (sans point_code) et doit contenir
"api_key" : la clé du capteur. Nécessite un broker (ex. Mosquitto) : voir docker-compose.yml (profil mqtt).
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import MeasurementPoint
from app.schemas import MeasurementReading
from app.security import sensor_key_valid
from app.services.dispatch import dispatch_alert
from app.services.ingestion import ingest_measurement
from app.services.realtime import alert_event, measurement_event, publish

log = logging.getLogger(__name__)


def handle_message(topic: str, payload: bytes, settings: Settings | None = None) -> bool:
    """Traite un message MQTT. Retourne True si la mesure a été acceptée."""
    settings = settings or get_settings()
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "intelliflood" or parts[1] != "points" or parts[3] != "measurements":
        log.warning("Topic MQTT ignoré : %s", topic)
        return False
    code = parts[2]
    try:
        body = json.loads(payload)
        api_key = body.pop("api_key", None)
        reading = MeasurementReading.model_validate(body)
    except (ValueError, ValidationError, AttributeError) as exc:
        log.warning("Message MQTT invalide pour %s : %s", code, exc)
        return False

    alert_id = None
    with SessionLocal() as db:
        point = db.scalar(select(MeasurementPoint).where(MeasurementPoint.code == code).with_for_update())
        if point is None or not point.active or not sensor_key_valid(point, api_key, settings):
            log.warning("Message MQTT refusé (point inconnu, inactif ou clé invalide) : %s", code)
            return False
        try:
            result = ingest_measurement(db, point, reading, source="mqtt")
        except ValueError as exc:
            log.warning("Mesure MQTT rejetée pour %s : %s", code, exc)
            db.rollback()
            return False
        db.commit()
        publish(measurement_event(point, result.measurement))
        if result.alert:
            alert_id = result.alert.id
            publish(alert_event(result.alert, "alert_created"))
    if alert_id:
        dispatch_alert(alert_id)
    return True


def start_mqtt(settings: Settings):  # pragma: no cover - nécessite un broker
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    def on_connect(c, userdata, flags, reason_code, properties):
        log.info("MQTT connecté (%s), abonnement à %s", reason_code, settings.mqtt_topic)
        c.subscribe(settings.mqtt_topic)

    client.on_connect = on_connect
    client.on_message = lambda c, u, msg: handle_message(msg.topic, msg.payload, settings)
    client.connect_async(settings.mqtt_host, settings.mqtt_port)
    client.loop_start()
    return client
