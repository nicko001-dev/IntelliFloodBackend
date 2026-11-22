from datetime import UTC, datetime

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_phone(raw: str, default_region: str = "CI") -> str:
    """Normalise un numéro en E.164 (ex. 0707123456 -> +2250707123456). Lève ValueError si invalide."""
    try:
        number = phonenumbers.parse(raw.strip(), default_region)
    except NumberParseException as exc:
        raise ValueError("Numéro de téléphone invalide") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValueError("Numéro de téléphone invalide")
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)


def mask_phone(phone: str) -> str:
    """+2250707123456 -> +225*******456 (collecte minimale : on n'expose jamais le numéro complet)."""
    if len(phone) <= 7:
        return "*" * len(phone)
    return phone[:4] + "*" * (len(phone) - 7) + phone[-3:]
