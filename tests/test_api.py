from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import ADMIN, send

API = "/api/v1"


def subscribe(client, zone_id, phone="0707123456"):
    return client.post(f"{API}/subscriptions", json={"phone": phone, "zone_id": zone_id})


# ------------------------------------------------------------------ sécurité (F3.5)
def test_sensor_requires_valid_api_key(client, setup):
    body = {"point_code": "COC-001", "distance_cm": 90}
    assert client.post(f"{API}/measurements", json=body).status_code == 401
    assert client.post(f"{API}/measurements", json=body, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post(f"{API}/measurements", json={**body, "point_code": "XXX-999"}, headers=setup["headers"]).status_code == 401
    assert client.post(f"{API}/measurements", json=body, headers=setup["headers"]).status_code == 201


def test_admin_endpoints_require_admin_key(client, setup):
    assert client.get(f"{API}/authorities/dashboard").status_code == 401
    assert client.get(f"{API}/authorities/dashboard", headers={"X-Admin-Key": "bad"}).status_code == 401
    assert client.post(f"{API}/admin/zones", json={"name": "X", "commune": "Y"}).status_code == 401
    assert client.get(f"{API}/authorities/dashboard", headers=ADMIN).status_code == 200


def test_rotate_key_invalidates_old_key(client, setup):
    new = client.post(f"{API}/admin/points/{setup['point_id']}/rotate-key", headers=ADMIN).json()["api_key"]
    assert send(client, setup, 10).status_code == 401
    assert client.post(f"{API}/measurements", json={"point_code": "COC-001", "distance_cm": 100}, headers={"X-API-Key": new}).status_code == 201


def test_point_api_key_not_exposed_after_creation(client, setup):
    assert "api_key" not in client.get(f"{API}/points/{setup['point_id']}").json()
    assert "api_key_hash" not in client.get(f"{API}/points/{setup['point_id']}").json()


def test_inactive_point_rejected(client, setup):
    client.patch(f"{API}/admin/points/{setup['point_id']}", json={"active": False}, headers=ADMIN)
    assert send(client, setup, 10).status_code == 403


# ------------------------------------------------------------------ validation des mesures
def test_invalid_measurements_rejected(client, setup):
    assert client.post(f"{API}/measurements", json={"point_code": "COC-001"}, headers=setup["headers"]).status_code == 422
    assert client.post(f"{API}/measurements", json={"point_code": "COC-001", "distance_cm": 900}, headers=setup["headers"]).status_code == 422
    r = client.post(f"{API}/measurements", json={"point_code": "COC-001", "readings_cm": [0, 0, 999]}, headers=setup["headers"])
    assert r.status_code == 422


def test_server_filters_raw_readings(client, setup):
    r = client.post(
        f"{API}/measurements",
        json={"point_code": "COC-001", "readings_cm": [70, 71, 69, 250, 70.5]},
        headers=setup["headers"],
    )
    m = r.json()["measurement"]
    assert r.status_code == 201 and 69 < m["distance_cm"] < 71 and m["readings_count"] == 4
    assert 49 < m["water_height_cm"] < 51


# ------------------------------------------------------------------ chaîne complète : niveau -> alerte -> notifications
def test_full_chain_red_alert_notifies_subscribers_onpc_and_logs_latency(client, setup, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "onpc_phone_numbers", "+2250505123456")
    monkeypatch.setattr(get_settings(), "onpc_emails", "onpc@example.org")
    sub = subscribe(client, setup["zone_id"])
    assert sub.status_code == 201 and sub.json()["phone_masked"].startswith("+225") and "0707123456" not in sub.text
    client.post(f"{API}/devices", json={"token": "t" * 40, "platform": "android", "zone_id": setup["zone_id"]})

    r = send(client, setup, 20)  # 20 % -> vert
    assert r.json()["alert_level"] == "vert" and r.json()["alert_id"] is None
    r = send(client, setup, 90)  # 90 % -> rouge
    body = r.json()
    assert body["alert_level"] == "rouge" and body["alert_id"]

    alert = client.get(f"{API}/alerts/{body['alert_id']}").json()
    assert alert["level"] == "rouge" and alert["status"] == "open"
    assert alert["zone_name"] == "Riviera" and len(alert["message_residents"]) <= 160
    assert alert["message_source"] == "fallback"  # IA non configurée -> message de secours (F4.4)
    assert alert["notified_at"] and alert["latency_ms"] is not None

    logs = client.get(f"{API}/admin/notifications", params={"alert_id": alert["id"]}, headers=ADMIN).json()
    kinds = sorted((l["channel"], l["recipient"].startswith("ONPC")) for l in logs)
    assert kinds == [("email", True), ("push", False), ("sms", False), ("sms", True)]
    assert all(l["status"] == "simulated" for l in logs)
    assert not any("0707123456" in l["recipient"] for l in logs)

    stats = client.get(f"{API}/admin/stats/latency", headers=ADMIN).json()
    assert stats["count"] == 1 and stats["max_ms"] >= 0


def test_orange_is_push_only_by_default(client, setup):
    subscribe(client, setup["zone_id"])
    client.post(f"{API}/devices", json={"token": "d" * 40, "zone_id": setup["zone_id"]})
    r = send(client, setup, 60)
    assert r.json()["alert_level"] == "orange" and r.json()["alert_id"]
    logs = client.get(f"{API}/admin/notifications", headers=ADMIN).json()
    assert [l["channel"] for l in logs] == ["push"]


def test_only_subscribers_of_the_zone_are_texted(client, setup):
    other = client.post(f"{API}/admin/zones", json={"name": "Sicogi", "commune": "Yopougon"}, headers=ADMIN).json()["id"]
    subscribe(client, setup["zone_id"], "0707123456")
    subscribe(client, other, "0101234567")
    send(client, setup, 95)
    sms = [l for l in client.get(f"{API}/admin/notifications", headers=ADMIN).json() if l["channel"] == "sms"]
    assert len(sms) == 1


def test_combined_rule_obstruction_plus_orange_gives_red(client, setup):
    r = send(client, setup, 65, obstruction_distance_cm=55)  # eau 65 % (orange), obstruction 65 % (forte)
    d = r.json()
    assert d["alert_level"] == "rouge" and d["measurement"]["obstruction_class"] == "forte"
    assert any("combinée" in x for x in d["reasons"])


def test_rise_rate_computed_from_history_and_fast_rise_is_orange(client, setup):
    now = datetime.now(UTC)
    for i, h in enumerate([20, 24, 28, 32]):  # +4 cm / 30 s = 8 cm/min
        r = send(client, setup, h, measured_at=(now - timedelta(seconds=90 - 30 * i)).isoformat())
    d = r.json()
    assert d["measurement"]["rise_rate_cm_min"] == 8.0 and d["alert_level"] == "orange"


# ------------------------------------------------------------------ cycle de vie des alertes
def test_alert_not_duplicated_resolved_on_return_to_green_and_cooldown_reopens(client, setup):
    a1 = send(client, setup, 90).json()["alert_id"]
    assert send(client, setup, 92).json()["alert_id"] is None  # même niveau : pas de doublon
    assert len(client.get(f"{API}/alerts").json()) == 1
    send(client, setup, 10)  # retour au calme
    assert client.get(f"{API}/alerts/{a1}").json()["status"] == "resolved"
    assert send(client, setup, 90).json()["alert_id"] is None  # rebond dans le cooldown : alerte rouverte, pas de SMS
    assert client.get(f"{API}/alerts/{a1}").json()["status"] == "open"
    assert len(client.get(f"{API}/alerts").json()) == 1


def test_escalation_orange_to_red_creates_second_alert(client, setup):
    send(client, setup, 60)
    send(client, setup, 90)
    alerts = client.get(f"{API}/alerts").json()
    assert [a["level"] for a in alerts] == ["rouge", "orange"]
    assert [a["status"] for a in alerts] == ["open", "resolved"]


def test_deescalation_red_to_orange_resolves_without_new_alert(client, setup):
    send(client, setup, 90)
    assert send(client, setup, 60).json()["alert_id"] is None
    assert client.get(f"{API}/points/{setup['point_id']}").json()["current_level"] == "orange"
    assert client.get(f"{API}/alerts", params={"status": "open"}).json() == []


def test_manual_resolve(client, setup):
    a = send(client, setup, 90).json()["alert_id"]
    assert client.post(f"{API}/admin/alerts/{a}/resolve", headers=ADMIN).json()["status"] == "resolved"


# ------------------------------------------------------------------ cas limites (perte réseau, capteur déconnecté)
def test_late_batch_does_not_overwrite_current_state(client, setup):
    now = datetime.now(UTC)
    send(client, setup, 30, measured_at=now.isoformat())
    batch = {
        "point_code": "COC-001",
        "measurements": [
            {"distance_cm": 20, "measured_at": (now - timedelta(minutes=3)).isoformat()},
            {"distance_cm": 25, "measured_at": (now - timedelta(minutes=2)).isoformat()},
        ],
    }
    r = client.post(f"{API}/measurements/batch", json=batch, headers=setup["headers"])
    assert r.status_code == 201 and r.json()["accepted"] == 2
    assert all(x["state_updated"] is False for x in r.json()["results"])
    assert client.get(f"{API}/points/{setup['point_id']}").json()["current_level"] == "vert"
    assert len(client.get(f"{API}/points/{setup['point_id']}/measurements").json()) == 3


def test_batch_requires_timestamps(client, setup):
    r = client.post(f"{API}/measurements/batch", json={"point_code": "COC-001", "measurements": [{"distance_cm": 50}]}, headers=setup["headers"])
    assert r.status_code == 422


def test_absurd_timestamp_falls_back_to_server_time(client, setup):
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    m = send(client, setup, 10, measured_at=future).json()["measurement"]
    assert abs((datetime.fromisoformat(m["measured_at"]) - datetime.now(UTC)).total_seconds()) < 5


def test_connectivity_states(client, setup, monkeypatch):
    p = lambda: client.get(f"{API}/points/{setup['point_id']}").json()  # noqa: E731
    assert p()["connectivity"] == "inconnu"
    send(client, setup, 10)
    assert p()["connectivity"] == "en_ligne"
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    from app.db import SessionLocal
    from app.models import MeasurementPoint

    with SessionLocal() as db:
        db.get(MeasurementPoint, setup["point_id"]).last_seen_at = datetime.fromisoformat(old)
        db.commit()
    assert p()["connectivity"] == "hors_ligne"
    dash = client.get(f"{API}/authorities/dashboard", headers=ADMIN).json()
    assert dash["summary"]["offline"] == 1


# ------------------------------------------------------------------ lecture pour l'application
def test_points_geojson_history_and_summary(client, setup):
    for h in (10, 20, 30):
        send(client, setup, h)
    gj = client.get(f"{API}/points/geojson").json()
    assert gj["type"] == "FeatureCollection" and gj["features"][0]["geometry"]["coordinates"] == [-3.968, 5.351]
    assert gj["features"][0]["properties"]["color"] == "#2ECC71"
    hist = client.get(f"{API}/points/{setup['point_id']}/history", params={"hours": 1, "bucket_minutes": 5}).json()
    assert sum(b["samples"] for b in hist) == 3 and max(b["max_height_cm"] for b in hist) == 30
    assert client.get(f"{API}/stats/summary").json()["vert"] == 1
    assert client.get(f"{API}/points", params={"level": "rouge"}).json() == []
    assert client.get(f"{API}/points/9999").status_code == 404
    assert client.get(f"{API}/points", params={"level": "bleu"}).status_code == 422


def test_dashboard_sorted_by_severity(client, setup):
    z = setup["zone_id"]
    for code, name in (("YOP-001", "Yopougon"), ("ABO-001", "Abobo")):
        r = client.post(f"{API}/admin/points", json={"code": code, "name": name, "zone_id": z, "latitude": 5.3, "longitude": -4.0,
                                                    "canal_depth_cm": 100, "sensor_height_cm": 120}, headers=ADMIN).json()
        client.post(f"{API}/measurements", json={"point_code": code, "distance_cm": 120 - (90 if code == "ABO-001" else 60)},
                    headers={"X-API-Key": r["api_key"]})
    send(client, setup, 10)
    dash = client.get(f"{API}/authorities/dashboard", headers=ADMIN).json()
    assert [p["code"] for p in dash["points"]] == ["ABO-001", "YOP-001", "COC-001"]
    assert dash["summary"] == {"total_points": 3, "rouge": 1, "orange": 1, "vert": 1, "offline": 0, "open_alerts": 2}


# ------------------------------------------------------------------ administration
def test_calibrate_and_threshold_update(client, setup):
    pid = setup["point_id"]
    assert client.post(f"{API}/admin/points/{pid}/calibrate", json={}, headers=ADMIN).status_code == 422
    assert client.post(f"{API}/admin/points/{pid}/calibrate", json={"use_latest_measurement": True}, headers=ADMIN).status_code == 409
    send(client, setup, 0.0 + 2)  # distance 118
    r = client.post(f"{API}/admin/points/{pid}/calibrate", json={"use_latest_measurement": True}, headers=ADMIN)
    assert r.json()["d0_cm"] == 118.0
    assert client.post(f"{API}/admin/points/{pid}/calibrate", json={"d0_cm": 121}, headers=ADMIN).json()["d0_cm"] == 121
    r = client.patch(f"{API}/admin/points/{pid}", json={"orange_pct": 40, "red_pct": 70}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["red_pct"] == 70
    assert send(client, setup, 75).json()["alert_level"] == "rouge"  # seuil rouge personnalisé
    assert client.patch(f"{API}/admin/points/{pid}", json={"orange_pct": 90}, headers=ADMIN).status_code == 422
    assert client.get(f"{API}/points/{pid}").json()["orange_pct"] == 40  # rollback effectif


def test_create_point_validation(client, setup):
    bad = {"code": "NEW-1", "name": "Xx", "zone_id": setup["zone_id"], "latitude": 5, "longitude": -4,
           "canal_depth_cm": 100, "sensor_height_cm": 120, "orange_pct": 90, "red_pct": 80}
    assert client.post(f"{API}/admin/points", json=bad, headers=ADMIN).status_code == 422
    dup = {**bad, "code": "COC-001", "orange_pct": 50}
    assert client.post(f"{API}/admin/points", json=dup, headers=ADMIN).status_code == 409
    assert client.post(f"{API}/admin/points", json={**dup, "code": "N-2", "zone_id": 999}, headers=ADMIN).status_code == 404


def test_ai_preview_and_test_sms(client):
    r = client.post(f"{API}/admin/ai/preview", json={"level": "rouge"}, headers=ADMIN).json()
    assert r["source"] == "fallback" and r["habitants_length"] <= 160
    assert client.post(f"{API}/admin/notifications/test-sms", json={"phone": "0707123456"}, headers=ADMIN).json()["simulated"] is True


# ------------------------------------------------------------------ habitants : inscriptions, appareils, signalements
def test_subscription_lifecycle(client, setup):
    s = subscribe(client, setup["zone_id"], "07 07 12 34 56")
    token = s.json()["token"]
    assert s.status_code == 201 and token
    again = subscribe(client, setup["zone_id"], "+225 0707123456")  # même numéro, autre format
    assert again.status_code == 200 and again.json()["already_subscribed"] and again.json()["token"] is None
    other = client.post(f"{API}/admin/zones", json={"name": "Sicogi", "commune": "Yopougon"}, headers=ADMIN).json()["id"]
    assert client.patch(f"{API}/subscriptions/{token}", json={"zone_id": other}).json()["zone"]["id"] == other
    assert client.delete(f"{API}/subscriptions/{token}").status_code == 204
    assert client.delete(f"{API}/subscriptions/{token}").status_code == 404


def test_subscription_validation(client, setup):
    assert subscribe(client, setup["zone_id"], "12345").status_code == 422
    assert subscribe(client, setup["zone_id"], "abcdefghij").status_code == 422
    assert subscribe(client, 999).status_code == 404


def test_device_registration_idempotent(client, setup):
    body = {"token": "x" * 50, "platform": "ios", "zone_id": setup["zone_id"]}
    assert client.post(f"{API}/devices", json=body).status_code == 204
    assert client.post(f"{API}/devices", json=body).status_code == 204
    from app.db import SessionLocal
    from app.models import DeviceToken

    with SessionLocal() as db:
        assert db.query(DeviceToken).count() == 1
    assert client.post(f"{API}/devices/unregister", json={"token": "x" * 50}).status_code == 204


def test_citizen_report_moderation(client, setup):
    rep = {"zone_id": setup["zone_id"], "latitude": 5.35, "longitude": -3.97, "category": "canal_obstrue", "description": "Canal bouché par des déchets"}
    created = client.post(f"{API}/reports", json=rep)
    assert created.status_code == 201 and created.json()["status"] == "new"
    assert client.get(f"{API}/reports").json() == []  # non vérifié : invisible publiquement
    rid = created.json()["id"]
    assert client.patch(f"{API}/admin/reports/{rid}", json={"status": "verified"}).status_code == 401
    assert client.patch(f"{API}/admin/reports/{rid}", json={"status": "verified"}, headers=ADMIN).status_code == 200
    assert len(client.get(f"{API}/reports").json()) == 1
    assert client.post(f"{API}/reports", json={**rep, "category": "bizarre"}).status_code == 422


# ------------------------------------------------------------------ temps réel + IA branchée de bout en bout
def test_websocket_receives_measurement_and_alert_events(setup):
    with TestClient(app) as c, c.websocket_connect("/ws") as ws:
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}
        send(c, setup, 90)
        types = []
        for _ in range(3):
            types.append(ws.receive_json()["type"])
        assert types[0] == "measurement" and "alert_created" in types and "alert_updated" in types


def test_ai_messages_used_when_valid(client, setup, monkeypatch):
    from app.services import ai

    class P:
        name = "fake"

        def complete(self, system, user):
            return '{"habitants": "ALERTE Riviera : eau à 90 %. Éloignez-vous du canal.", "onpc": "Riviera : niveau 90 %."}'

    monkeypatch.setattr(ai, "build_provider", lambda s: P())
    aid = send(client, setup, 90).json()["alert_id"]
    a = client.get(f"{API}/alerts/{aid}").json()
    assert a["message_source"] == "ia" and a["message_residents"].startswith("ALERTE Riviera")
