"""Messages d'alerte : contexte, messages prédéfinis (secours F4.4), validation anti-invention (F4.5), texte SMS."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SMS_LIMIT = 160
ONPC_LIMIT = 500

# Alphabet de base GSM 03.38 : un SMS reste à 160 caractères s'il n'utilise que ces caractères.
_GSM7 = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
_TYPO = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "\u00a0": " ", "œ": "oe", "Œ": "OE"}
_FORBIDDEN = re.compile(r"(?i)\b(?:évacu\w*|evacu\w*|quitt\w*|abandonn\w*)")


@dataclass(frozen=True)
class AlertContext:
    zone_name: str
    commune: str
    point_name: str
    level: str  # orange | rouge
    water_level_pct: float | None
    water_height_cm: float | None
    rise_rate_cm_min: float | None
    obstruction_pct: float | None
    obstruction_class: str | None
    blockage_detected: bool

    @classmethod
    def from_parts(cls, point, zone, alert) -> AlertContext:
        return cls(
            zone_name=zone.name,
            commune=zone.commune,
            point_name=point.name,
            level=alert.level,
            water_level_pct=alert.water_level_pct,
            water_height_cm=alert.water_height_cm,
            rise_rate_cm_min=alert.rise_rate_cm_min,
            obstruction_pct=alert.obstruction_pct,
            obstruction_class=alert.obstruction_class,
            blockage_detected=bool(alert.blockage_detected),
        )

    @classmethod
    def from_alert(cls, alert) -> AlertContext:
        return cls.from_parts(alert.point, alert.point.zone, alert)

    def to_prompt_data(self) -> dict:
        return {
            "quartier": self.zone_name,
            "commune": self.commune,
            "point": self.point_name,
            "niveau_alerte": self.level,
            "niveau_eau_pct": self.water_level_pct,
            "hauteur_eau_cm": self.water_height_cm,
            "vitesse_montee_cm_par_min": self.rise_rate_cm_min,
            "obstruction_pct": self.obstruction_pct,
            "obstruction_classe": self.obstruction_class,
            "blocage_amont_aval": self.blockage_detected,
        }


@dataclass(frozen=True)
class Messages:
    habitants: str
    onpc: str


# --------------------------------------------------------------------------- SMS
def to_sms_text(text: str, limit: int = SMS_LIMIT) -> str:
    """Rend le texte compatible GSM-7 (accents non supportés retirés) et le tronque à `limit` caractères."""
    out: list[str] = []
    for ch in text:
        ch = _TYPO.get(ch, ch)
        for c in ch:
            if c in _GSM7:
                out.append(c)
            else:
                base = "".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x))
                out.append(base if base and all(x in _GSM7 for x in base) else "?")
    s = "".join(out)
    return s if len(s) <= limit else s[: limit - 3].rstrip() + "..."


# --------------------------------------------------------------------------- messages prédéfinis
def _fmt(x: float | None, nd: int = 1) -> str:
    return f"{x:.{nd}f}".rstrip("0").rstrip(".") if x is not None else ""


def fallback_messages(ctx: AlertContext) -> Messages:
    pct = f"{round(ctx.water_level_pct)}" if ctx.water_level_pct is not None else None
    zone = ctx.zone_name
    if ctx.level == "rouge":
        templates = [
            f"ALERTE ROUGE inondation - {zone} : niveau d'eau critique ({pct} %). "
            "Eloignez-vous du canal, ne traversez pas les zones inondees. IntelliFlood",
            f"ALERTE ROUGE inondation - {zone} : risque de debordement. Eloignez-vous du canal. IntelliFlood",
        ]
    else:
        templates = [
            f"PRE-ALERTE inondation - {zone} : l'eau monte dans le canal ({pct} %). "
            "Restez vigilant et evitez de vous approcher du canal. IntelliFlood",
            f"PRE-ALERTE inondation - {zone} : l'eau monte. Restez vigilant. IntelliFlood",
        ]
    if pct is None:
        templates = [t.replace(" (None %)", "") for t in templates]
    habitants = next((t for t in templates if len(t) <= SMS_LIMIT), None)
    if habitants is None:  # nom de quartier très long
        short = f"ALERTE inondation - {zone[:60]}. Eloignez-vous du canal. IntelliFlood"
        habitants = short[:SMS_LIMIT]

    parts = [f"[IntelliFlood] {'ALERTE ROUGE' if ctx.level == 'rouge' else 'PRE-ALERTE ORANGE'} - {zone} ({ctx.commune}), point « {ctx.point_name} »."]
    if ctx.water_level_pct is not None:
        h = f" ({_fmt(ctx.water_height_cm)} cm)" if ctx.water_height_cm is not None else ""
        parts.append(f"Niveau d'eau : {_fmt(ctx.water_level_pct, 0)} % de la hauteur utile{h}.")
    if ctx.rise_rate_cm_min is not None:
        parts.append(f"Évolution : {_fmt(ctx.rise_rate_cm_min)} cm/min.")
    if ctx.obstruction_class:
        o = f" ({_fmt(ctx.obstruction_pct, 0)} %)" if ctx.obstruction_pct is not None else ""
        parts.append(f"Obstruction : {ctx.obstruction_class}{o}.")
    if ctx.blockage_detected:
        parts.append("Blocage amont/aval détecté.")
    return Messages(habitants=habitants, onpc=" ".join(parts)[:ONPC_LIMIT])


# --------------------------------------------------------------------------- validation des sorties IA (F4.5)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _allowed_numbers(ctx: AlertContext) -> set[float]:
    allowed: set[float] = set()
    for v in (ctx.water_level_pct, ctx.water_height_cm, ctx.rise_rate_cm_min, ctx.obstruction_pct):
        if v is not None:
            allowed.update({round(v), round(v, 1), round(v, 2), float(int(abs(v))), abs(round(v, 1))})
    for text in (ctx.zone_name, ctx.commune, ctx.point_name):
        allowed.update(float(n.replace(",", ".")) for n in _NUM.findall(text))
    return allowed


def validate_ai_output(ctx: AlertContext, habitants: object, onpc: object) -> str | None:
    """Retourne None si le message est acceptable, sinon la raison du rejet."""
    if not isinstance(habitants, str) or not isinstance(onpc, str) or not habitants.strip() or not onpc.strip():
        return "messages vides ou mal formés"
    if len(habitants) > SMS_LIMIT:
        return f"message habitants trop long ({len(habitants)} > {SMS_LIMIT})"
    if len(onpc) > ONPC_LIMIT:
        return f"message ONPC trop long ({len(onpc)} > {ONPC_LIMIT})"
    for label, text in (("habitants", habitants), ("onpc", onpc)):
        if _FORBIDDEN.search(text):
            return f"ordre d'évacuation ou consigne interdite dans le message {label}"
    allowed = _allowed_numbers(ctx)
    for label, text in (("habitants", habitants), ("onpc", onpc)):
        for raw in _NUM.findall(text):
            val = float(raw.replace(",", "."))
            if not any(abs(val - a) < 0.051 for a in allowed):
                return f"chiffre non fourni dans les données ({raw}) dans le message {label}"
    return None
