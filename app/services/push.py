"""Notifications push via Firebase Cloud Messaging (HTTP v1). Compatible Flutter (firebase_messaging)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import httpx

from app.config import Settings

log = logging.getLogger(__name__)
_SCOPE = ["https://www.googleapis.com/auth/firebase.messaging"]


@dataclass(frozen=True)
class PushResult:
    ok: bool
    invalid_token: bool = False
    simulated: bool = False
    error: str | None = None


class FcmPush:
    name = "fcm"

    def __init__(self, s: Settings):
        if not (s.fcm_project_id and s.fcm_service_account_file):
            raise ValueError("FCM_PROJECT_ID et FCM_SERVICE_ACCOUNT_FILE sont requis")
        from google.oauth2 import service_account  # import tardif : dépendance utile seulement avec FCM

        self.s = s
        self._creds = service_account.Credentials.from_service_account_file(s.fcm_service_account_file, scopes=_SCOPE)
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        from google.auth.transport.requests import Request

        with self._lock:
            if not self._creds.valid:
                self._creds.refresh(Request())
            return self._creds.token

    def send(self, token: str, title: str, body: str, data: dict[str, str]) -> PushResult:
        message = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": data,
                "android": {"priority": "high"},
                "apns": {"headers": {"apns-priority": "10"}},
            }
        }
        try:
            r = httpx.post(
                f"https://fcm.googleapis.com/v1/projects/{self.s.fcm_project_id}/messages:send",
                headers={"Authorization": f"Bearer {self._access_token()}"},
                json=message,
                timeout=10,
            )
        except Exception as exc:
            return PushResult(False, error=str(exc)[:200])
        if r.status_code == 200:
            return PushResult(True)
        invalid = r.status_code in (400, 404) and ("UNREGISTERED" in r.text or "INVALID_ARGUMENT" in r.text)
        return PushResult(False, invalid_token=invalid, error=f"HTTP {r.status_code}: {r.text[:200]}")


class ConsolePush:
    name = "console"

    def send(self, token: str, title: str, body: str, data: dict[str, str]) -> PushResult:
        log.info("[PUSH SIMULÉ] %s — %s", title, body)
        return PushResult(True, simulated=True)


def build_push_provider(s: Settings):
    """`none` = simulation en console (la notification est tout de même diffusée en WebSocket)."""
    return FcmPush(s) if s.push_provider == "fcm" else ConsolePush()
