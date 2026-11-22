"""Fournisseurs SMS interchangeables. `console` simule l'envoi (démo sans compte) ;
`twilio` et `africastalking` appellent l'API réelle. Vérifier la couverture Côte d'Ivoire
du fournisseur choisi avant la démonstration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SendResult:
    ok: bool
    simulated: bool = False
    error: str | None = None
    provider_id: str | None = None


class SmsProvider(Protocol):
    name: str

    def send(self, to: str, text: str) -> SendResult: ...


class ConsoleSms:
    name = "console"

    def send(self, to: str, text: str) -> SendResult:
        log.info("[SMS SIMULÉ] -> %s : %s", to[:4] + "***" + to[-3:], text)
        return SendResult(ok=True, simulated=True)


class TwilioSms:
    name = "twilio"

    def __init__(self, s: Settings):
        if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_from):
            raise ValueError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN et TWILIO_FROM sont requis")
        self.s = s

    def send(self, to: str, text: str) -> SendResult:
        try:
            r = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self.s.twilio_account_sid}/Messages.json",
                auth=(self.s.twilio_account_sid, self.s.twilio_auth_token),  # type: ignore[arg-type]
                data={"To": to, "From": self.s.twilio_from, "Body": text},
                timeout=10,
            )
            if r.status_code >= 400:
                return SendResult(False, error=f"HTTP {r.status_code}: {r.text[:200]}")
            return SendResult(True, provider_id=r.json().get("sid"))
        except httpx.HTTPError as exc:
            return SendResult(False, error=str(exc)[:200])


class AfricasTalkingSms:
    name = "africastalking"

    def __init__(self, s: Settings):
        if not (s.at_username and s.at_api_key):
            raise ValueError("AT_USERNAME et AT_API_KEY sont requis")
        self.s = s

    def send(self, to: str, text: str) -> SendResult:
        host = "api.sandbox.africastalking.com" if self.s.at_sandbox else "api.africastalking.com"
        data = {"username": self.s.at_username, "to": to, "message": text}
        if self.s.at_sender_id:
            data["from"] = self.s.at_sender_id
        try:
            r = httpx.post(
                f"https://{host}/version1/messaging",
                headers={"apiKey": self.s.at_api_key or "", "Accept": "application/json"},
                data=data,
                timeout=10,
            )
            if r.status_code >= 400:
                return SendResult(False, error=f"HTTP {r.status_code}: {r.text[:200]}")
            recipients = r.json().get("SMSMessageData", {}).get("Recipients", [])
            if recipients and all(rec.get("statusCode") in (100, 101, 102) for rec in recipients):
                return SendResult(True, provider_id=recipients[0].get("messageId"))
            return SendResult(False, error=f"refusé : {recipients or r.text[:200]}")
        except (httpx.HTTPError, ValueError) as exc:
            return SendResult(False, error=str(exc)[:200])


def build_sms_provider(s: Settings) -> SmsProvider:
    if s.sms_provider == "twilio":
        return TwilioSms(s)
    if s.sms_provider == "africastalking":
        return AfricasTalkingSms(s)
    return ConsoleSms()
