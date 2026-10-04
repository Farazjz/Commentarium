"""Podcast person + TTS provider configuration.

Provides a typed view of the configured podcast hosts (1-3 people, each with a
name, gender and voice) and the helper logic each TTS provider needs. Keeps the
generation pipeline and the Settings UI reading from the same single source of
truth (the .env-backed Settings object).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings

# Provider keys shown in settings / stored in TTS_PROVIDER
PROVIDERS = ("localhost", "gateway-edge-tts", "cloudflare", "google", "edge-tts", "voicestudio", "disabled")
PROVIDER_LABELS = {
    "localhost": "🌐 Localhost API (OpenAI-compatible)",
    "gateway-edge-tts": "🌐 Local gateway · edge-tts voices (9Router)",
    "cloudflare": "☁️ Cloudflare Workers AI",
    "google": "🔎 Google Cloud TTS API",
    "edge-tts": "🎙 edge-tts (free, internet)",
    "voicestudio": "🎙 VoiceStudio (local server)",
    "disabled": "🚫 Disabled (transcript only)",
}

GENDERS = ("female", "male", "neutral")

# Host dialogue / speaking languages (ISO 639-1). VoiceStudio is multilingual;
# the script is written in and synthesized for each host's chosen language.
LANGUAGES = ("en", "fa")


@dataclass
class Host:
    """A single podcast person ('host')."""

    index: int          # 1-based
    name: str
    gender: str         # female | male | neutral
    voice: str          # voice id for the active TTS provider
    language: str = "en"  # ISO 639-1 language code for dialogue (VoiceStudio)

    @property
    def label(self) -> str:
        return f"Host {self.index}"


def _clean_gender(g: str | None) -> str:
    g = (g or "").strip().lower()
    return g if g in GENDERS else "female"


def _clean_language(lang: str | None) -> str:
    l = (lang or "").strip().lower()
    return l if l in LANGUAGES else "en"


def get_hosts(cfg: Settings | None = None) -> list[Host]:
    """Return the configured podcast hosts (1..num_hosts), oldest first.

    Falls back gracefully if cfg hasn't run through auto-migration for the new
    per-host fields (e.g. a .env written by an older version).
    """
    cfg = cfg or get_settings()
    try:
        n = int(getattr(cfg, "podcast_num_hosts", 2) or 2)
    except (TypeError, ValueError):
        n = 2
    n = max(1, min(3, n))

    # Legacy fallback: if the per-host fields are absent but the old host_a/b
    # voices were set, preserve them so existing configs don't break.
    legacy_a = getattr(cfg, "podcast_host_a_voice", "") or ""
    legacy_b = getattr(cfg, "podcast_host_b_voice", "") or ""
    fell_back = legacy_a or legacy_b

    hosts: list[Host] = []
    for i in range(1, n + 1):
        name = str(getattr(cfg, f"podcast_host_{i}_name", "") or "").strip()
        gender = _clean_gender(getattr(cfg, f"podcast_host_{i}_gender", ""))
        voice = str(getattr(cfg, f"podcast_host_{i}_voice", "") or "").strip()
        language = _clean_language(getattr(cfg, f"podcast_host_{i}_language", ""))
        # legacy mapping: host1 = old A (JennyFemale-ish), host2 = old B
        if fell_back and not voice:
            voice = legacy_a if i == 1 else (legacy_b if i == 2 else "")
        try:
            name = name or f"Host {i}"
        except Exception:  # noqa: BLE001
            name = f"Host {i}"
        hosts.append(Host(index=i, name=name, gender=gender, voice=voice, language=language))
    return hosts


def host_by_index(cfg: Settings | None = None) -> dict[int, Host]:
    return {h.index: h for h in get_hosts(cfg)}
