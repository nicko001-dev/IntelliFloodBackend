"""Module 4 — rédaction des messages par un modèle de langage.

Garde-fous : (1) le niveau d'alerte est imposé par les seuils, l'IA ne fait que rédiger ;
(2) prompt strict ; (3) validation de la sortie (longueur, chiffres, pas d'évacuation) ;
(4) message prédéfini si l'IA est indisponible ou si la sortie est rejetée.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings
from app.services.messages import AlertContext, Messages, fallback_messages, validate_ai_output

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu es le module de rédaction d'alertes du système IntelliFlood (prévention des inondations, Côte d'Ivoire).
Le niveau d'alerte est DÉJÀ décidé par le système : tu ne le discutes pas et tu ne le modifies pas.
Tu rédiges deux messages en français, à partir UNIQUEMENT des données JSON fournies.

Règles strictes :
- N'invente aucune information (lieu, chiffre, heure, cause, prévision, consigne officielle).
- Utilise uniquement les chiffres fournis, sans en calculer ni en ajouter d'autres.
- Ne donne JAMAIS d'ordre d'évacuation (pas de « évacuez », « quittez la zone »...) et ne cite aucune autorité.
- "habitants" : 160 caractères maximum, ton calme et clair ; indique le quartier, le niveau d'alerte et 1 ou 2 gestes de prudence simples
  (s'éloigner du canal, ne pas traverser une zone inondée, rester attentif aux informations).
- "onpc" : 500 caractères maximum, factuel : lieu, niveau d'alerte, niveau d'eau en % de la hauteur utile, évolution
  (vitesse de montée) et obstruction si ces données sont fournies.
- Réponds uniquement par un objet JSON valide : {"habitants": "...", "onpc": "..."} sans aucun texte autour."""


class AiProvider(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, s: Settings):
        self.s = s

    def complete(self, system: str, user: str) -> str:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.s.ai_api_key or "", "anthropic-version": "2023-06-01"},
            json={
                "model": self.s.ai_model,
                "max_tokens": 500,
                "temperature": 0.2,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.s.ai_timeout_seconds,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


class OpenAICompatibleProvider:
    """Ollama, LM Studio, vLLM, Groq, Mistral, OpenAI... (endpoint /chat/completions)."""

    name = "openai_compatible"

    def __init__(self, s: Settings):
        self.s = s

    def complete(self, system: str, user: str) -> str:
        headers = {"Authorization": f"Bearer {self.s.ai_api_key}"} if self.s.ai_api_key else {}
        r = httpx.post(
            self.s.ai_base_url.rstrip("/") + "/chat/completions",
            headers=headers,
            json={
                "model": self.s.ai_model,
                "temperature": 0.2,
                "max_tokens": 500,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=self.s.ai_timeout_seconds,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def build_provider(s: Settings) -> AiProvider | None:
    if s.ai_provider == "anthropic":
        return AnthropicProvider(s) if s.ai_api_key else None
    if s.ai_provider == "openai_compatible":
        return OpenAICompatibleProvider(s)
    return None


@dataclass(frozen=True)
class GeneratedMessages:
    messages: Messages
    source: str  # "ia" | "fallback"
    error: str | None = None


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("aucun objet JSON dans la réponse")
    return json.loads(match.group(0))


def generate_messages(ctx: AlertContext, settings: Settings) -> GeneratedMessages:
    """Ne lève jamais d'exception : en cas de problème, retourne le message prédéfini."""
    fallback = fallback_messages(ctx)
    provider = build_provider(settings)
    if provider is None:
        return GeneratedMessages(fallback, "fallback", None if settings.ai_provider == "none" else "IA non configurée")
    try:
        raw = provider.complete(SYSTEM_PROMPT, json.dumps(ctx.to_prompt_data(), ensure_ascii=False))
        data = _parse_json(raw)
        habitants, onpc = data.get("habitants"), data.get("onpc")
        problem = validate_ai_output(ctx, habitants, onpc)
        if problem:
            log.warning("Sortie IA rejetée (%s) — message prédéfini utilisé", problem)
            return GeneratedMessages(fallback, "fallback", f"sortie rejetée : {problem}")
        return GeneratedMessages(Messages(habitants.strip(), onpc.strip()), "ia")  # type: ignore[union-attr]
    except Exception as exc:  # IA indisponible, timeout, JSON invalide...
        log.warning("IA indisponible (%s) — message prédéfini utilisé", exc)
        return GeneratedMessages(fallback, "fallback", f"{type(exc).__name__}: {exc}"[:300])
