import json

import httpx

from app.config import Settings
from app.services import mqtt, sms
from tests.conftest import ADMIN

API = "/api/v1"


def _resp(status, payload, url="https://x"):
    return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


def test_twilio_success_and_failure(monkeypatch):
    s = Settings(sms_provider="twilio", twilio_account_sid="AC1", twilio_auth_token="t", twilio_from="+1555")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(201, {"sid": "SM1"}))
    assert sms.TwilioSms(s).send("+2250707123456", "hi").ok
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(400, {"message": "bad"}))
    r = sms.TwilioSms(s).send("+2250707123456", "hi")
    assert not r.ok and "400" in r.error


def test_africastalking_status_codes(monkeypatch):
    s = Settings(sms_provider="africastalking", at_username="sandbox", at_api_key="k")
    ok = {"SMSMessageData": {"Recipients": [{"statusCode": 101, "messageId": "m1"}]}}
    ko = {"SMSMessageData": {"Recipients": [{"statusCode": 403, "status": "InvalidPhoneNumber"}]}}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(201, ok))
    assert sms.AfricasTalkingSms(s).send("+2250707123456", "hi").ok
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(201, ko))
    assert not sms.AfricasTalkingSms(s).send("+2250707123456", "hi").ok


def test_sms_failure_is_logged_not_fatal(client, setup, monkeypatch):
    from app.services import dispatch

    class Boom:
        name = "boom"

        def send(self, to, text):
            return sms.SendResult(False, error="réseau")

    monkeypatch.setattr(dispatch, "get_sms_provider", lambda: Boom())
    client.post(f"{API}/subscriptions", json={"phone": "0707123456", "zone_id": setup["zone_id"]})
    from tests.conftest import send

    assert send(client, setup, 90).status_code == 201
    logs = client.get(f"{API}/admin/notifications", headers=ADMIN).json()
    assert logs[0]["status"] == "failed" and logs[0]["error"] == "réseau"
    assert client.get(f"{API}/alerts").json()[0]["notified_at"] is None


def test_fcm_marks_unregistered_token_invalid(monkeypatch):
    from app.services import push

    p = push.FcmPush.__new__(push.FcmPush)
    p.s = Settings(fcm_project_id="proj")
    p._access_token = lambda: "tok"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(404, {"error": {"status": "NOT_FOUND", "details": "UNREGISTERED"}}))
    res = p.send("token", "t", "b", {})
    assert not res.ok and res.invalid_token


def test_mqtt_message_handling(client, setup):
    topic = "intelliflood/points/COC-001/measurements"
    good = json.dumps({"api_key": setup["key"], "distance_cm": 30}).encode()
    assert mqtt.handle_message(topic, good) is True
    assert client.get(f"{API}/points/{setup['point_id']}").json()["current_level"] == "rouge"
    assert len(client.get(f"{API}/alerts").json()) == 1
    assert mqtt.handle_message(topic, json.dumps({"api_key": "bad", "distance_cm": 30}).encode()) is False
    assert mqtt.handle_message(topic, b"not json") is False
    assert mqtt.handle_message("autre/topic", good) is False
    assert mqtt.handle_message(topic, json.dumps({"api_key": setup["key"]}).encode()) is False  # pas de distance


def test_production_settings_guard():
    import pytest

    with pytest.raises(RuntimeError):
        Settings(environment="production").assert_production_ready()
    Settings(environment="production", admin_api_key="x" * 20, cors_origins="https://app.example").assert_production_ready()
    assert Settings(database_url="postgres://u:p@h/db").database_url.startswith("postgresql+psycopg://")
