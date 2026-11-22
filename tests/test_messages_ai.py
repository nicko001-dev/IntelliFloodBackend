import httpx
import pytest

from app.config import Settings
from app.services import ai
from app.services.messages import AlertContext, fallback_messages, to_sms_text, validate_ai_output

CTX = AlertContext("Cocody — Riviera", "Cocody", "Canal 1", "rouge", 85.0, 42.5, 3.2, 65.0, "forte", False)


class FakeProvider:
    name = "fake"

    def __init__(self, out=None, exc=None):
        self.out, self.exc = out, exc

    def complete(self, system, user):
        if self.exc:
            raise self.exc
        return self.out


def use(monkeypatch, provider):
    monkeypatch.setattr(ai, "build_provider", lambda s: provider)


def test_fallback_fits_sms_and_has_no_evacuation_order():
    for level in ("rouge", "orange"):
        ctx = AlertContext("Cocody — Riviera", "Cocody", "Canal 1", level, 85.0, 42.5, None, None, None, False)
        m = fallback_messages(ctx)
        assert len(m.habitants) <= 160 and "vacu" not in m.habitants.lower()
    long_zone = AlertContext("Z" * 200, "Cocody", "P", "rouge", 90, 40, None, None, None, False)
    assert len(fallback_messages(long_zone).habitants) <= 160


def test_sms_text_is_gsm7_and_truncated():
    text = to_sms_text("Éloignez-vous – l’eau monte… ê î ô ç œ " + "x" * 200)
    assert len(text) <= 160 and text.endswith("...")
    assert "’" not in text and "ê" not in text and "–" not in text
    assert to_sms_text("é è à ù") == "é è à ù"  # accents GSM-7 conservés


def test_validation_rules():
    assert validate_ai_output(CTX, "ALERTE Riviera : eau à 85 %. Éloignez-vous du canal.", "Eau 85 %, 42,5 cm, 3,2 cm/min, obstruction 65 %") is None
    assert "interdite" in validate_ai_output(CTX, "Évacuez immédiatement", "ok")
    assert "interdite" in validate_ai_output(CTX, "ok", "Quittez la zone")
    assert "chiffre" in validate_ai_output(CTX, "Eau à 90 %", "ok")
    assert "trop long" in validate_ai_output(CTX, "a" * 161, "ok")
    assert "vides" in validate_ai_output(CTX, "", "ok")


def test_generate_uses_ai_when_valid(monkeypatch):
    use(monkeypatch, FakeProvider('Voici : {"habitants": "ALERTE Riviera : eau à 85 %. Éloignez-vous du canal.", "onpc": "Riviera : 85 %."}'))
    g = ai.generate_messages(CTX, Settings())
    assert g.source == "ia" and g.error is None and "85" in g.messages.habitants


@pytest.mark.parametrize(
    "provider",
    [
        FakeProvider(exc=httpx.ConnectTimeout("timeout")),
        FakeProvider("pas de json"),
        FakeProvider('{"habitants": "Évacuez le quartier", "onpc": "x"}'),
        FakeProvider('{"habitants": "Eau à 99 %", "onpc": "x"}'),
    ],
)
def test_generate_falls_back_on_any_problem(monkeypatch, provider):
    use(monkeypatch, provider)
    g = ai.generate_messages(CTX, Settings())
    assert g.source == "fallback" and g.error and g.messages == fallback_messages(CTX)


def test_no_provider_configured_uses_fallback():
    g = ai.generate_messages(CTX, Settings(ai_provider="none"))
    assert g.source == "fallback" and g.error is None


def test_anthropic_and_openai_response_parsing(monkeypatch):
    captured = {}

    def fake_post(url, **kw):
        captured["url"], captured["json"], captured["headers"] = url, kw["json"], kw.get("headers", {})
        if "anthropic" in url:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "{}"}]}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    s = Settings(ai_provider="anthropic", ai_api_key="k", ai_model="claude-haiku-5-5")
    assert ai.AnthropicProvider(s).complete("sys", "usr") == "{}"
    assert captured["headers"]["x-api-key"] == "k" and captured["json"]["model"] == "claude-haiku-5-5"
    s2 = Settings(ai_provider="openai_compatible", ai_base_url="http://localhost:11434/v1/")
    assert ai.OpenAICompatibleProvider(s2).complete("sys", "usr") == "{}"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
