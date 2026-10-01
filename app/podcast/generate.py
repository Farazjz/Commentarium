"""Podcast generation for the app.

A NotebookLM-style feature: pick one or more documents (or a whole project),
and the app writes a conversational "deep dive" script between 1-3 podcast
hosts (named, gendered, each with a voice), then optionally turns it into an
audio file with the configured TTS provider:
  - "localhost"  -> any OpenAI-compatible /v1/audio/speech endpoint (private)
  - "cloudflare" -> Cloudflare Workers AI TTS
  - "google"     -> Google Cloud Text-to-Speech
  - "edge-tts"   -> free Microsoft Edge TTS (needs internet + pip install)
  - "disabled"   -> transcript only

Pipeline:
  1. Gather context: per-document key-point summaries of the selected docs.
  2. Generate a multi-host dialogue script with the configured chat model.
  3. (Optional) Synthesize audio via the active TTS provider.
  4. Persist script + audio path in the podcasts table.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings
from app.db.metadata import MetadataStore
from app.models_openrouter import LLMClientError, chat_completion
from app.podcast.hosts import Host, get_hosts, host_by_index
from app.rag.summarize import summarize_many_documents

logger = logging.getLogger("app")


class PodcastError(Exception):
    pass


# Legacy fallback matcher for lines that still say "HOST A:", "HOST B:" etc.
_HOST_LEGACY_RE = re.compile(r"^\s*HOST\s*([ABC])\s*:\s*(.*)$", re.IGNORECASE)


def _match_line(line: str, hosts: list[Host]) -> tuple[int, str] | None:
    """Return (host_index, text) for a script line, else None.

    Tries configured host names first (e.g. "Alice: ..."), then the legacy
    "HOST A/B/C:" form.
    """
    stripped = line.strip()
    for h in hosts:
        prefix = h.name.strip()
        if not prefix:
            continue
        if stripped.lower().startswith(prefix.lower() + ":"):
            return h.index, stripped[len(prefix) + 1:].strip()
    m = _HOST_LEGACY_RE.match(stripped)
    if m:
        idx = ord(m.group(1).upper()) - ord("A") + 1
        if 1 <= idx <= 3:
            return idx, m.group(2).strip()
    return None


@dataclass
class ScriptLine:
    host_index: int       # 1-based index into the configured hosts list
    text: str


def parse_script(script: str, hosts: list[Host]) -> list[ScriptLine]:
    """Parse raw generated text into (host_index, text) turns."""
    lines: list[ScriptLine] = []
    for ln in (script or "").splitlines():
        matched = _match_line(ln, hosts)
        if not matched:
            continue  # skip markdown / blank noise between dialogue lines
        idx, text = matched
        if text:
            lines.append(ScriptLine(host_index=idx, text=text))
    return lines


def _gather_context(document_ids: list[str], meta: MetadataStore) -> str:
    """Build a compact, per-document context block from key-point summaries."""
    docs = {}
    for did in document_ids:
        d = meta.get_document(did)
        if d:
            docs[did] = d
    if not docs:
        raise PodcastError("None of the selected documents exist.")

    summaries = summarize_many_documents(list(docs.keys()), kind="key_points")
    by_id = {s["document_id"]: s for s in summaries}
    blocks = []
    for did, d in docs.items():
        summ = by_id.get(did)
        body = summ["summary"] if summ else "(could not summarize this document)"
        blocks.append(
            f"Document: {d.get('filename')}\n"
            f"Title/metadata: {(d.get('title') or '—')}; {', '.join(_authors(d)) or 'unknown authors'} "
            f"({d.get('year') or 'n/a'})\n"
            f"Summary: {body}"
        )
    return "\n\n----\n\n".join(blocks)


def _authors(doc: dict) -> list[str]:
    raw = doc.get("authors") or "[]"
    try:
        v = json.loads(raw) if isinstance(raw, str) else list(raw)
        return [str(a) for a in v if str(a).strip()]
    except json.JSONDecodeError:
        return []


def _build_script_system(hosts: list[Host]) -> str:
    """System prompt describing the host lineup to the LLM."""
    cast = ", ".join(
        f"{h.name} ({h.gender})" for h in hosts
    )
    line_format = "\n".join(f"{h.name}: <{h.name}'s spoken line>" for h in hosts)
    n = len(hosts)
    return (
        "You are a podcast producer creating a lively, conversational 'deep dive' "
        f"episode between {n} host(s) discussing a set of research papers for a "
        "curious but non-expert listener.\n\n"
        f"The cast is: {cast}.\n\n"
        "Rules:\n"
        "- Base every claim strictly on the provided summaries; never invent facts, "
        "numbers, or studies.\n"
        "- Write natural, engaging dialogue with a clear flow: intro -> what the "
        "papers are about -> key findings and how they connect -> why it matters -> "
        "wrap-up.\n"
        "- Every spoken line starts on its own line with the speaker's name followed "
        "by a colon and the spoken text (no markdown, no quotes). Example:\n"
        f"{line_format}\n"
        f"- Use all {n} host(s) and keep the dialogue balanced between them.\n"
        f"- Keep it roughly {600 + 200 * n}-{900 + 200 * n} words total.\n"
        "- Output ONLY the dialogue lines, nothing else."
    )


def _generate_script(
    title: str, context: str, hosts: list[Host], model: str | None = None
) -> str:
    user_msg = (
        f"The deep-dive episode is titled '{title}'.\n\n"
        f"Here are the papers the hosts will discuss:\n\n{context}\n\n"
        "Write the episode dialogue now."
    )
    messages = [
        {"role": "system", "content": _build_script_system(hosts)},
        {"role": "user", "content": user_msg},
    ]
    try:
        return chat_completion(messages, temperature=0.8, max_tokens=2600, model=model)
    except LLMClientError as exc:
        raise PodcastError(f"Could not generate podcast script: {exc}") from exc


# ---------------------------------------------------------------------------
# TTS providers
# ---------------------------------------------------------------------------


def _voice_map(hosts: list[Host]) -> dict[int, str]:
    """Map host_index -> voice id (falling back to a sensible default)."""
    return {h.index: (h.voice or "") for h in hosts}


def _synthesize_localhost_tts(script_lines, out_path, hosts, cfg, progress=None):
    """OpenAI-compatible /v1/audio/speech endpoint (your local server)."""
    url = cfg.tts_api_url.strip()
    if not url:
        raise PodcastError("Localhost TTS URL is not configured (Settings → Podcast).")
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url

    model = cfg.tts_api_model.strip() or "tts-1"
    fmt = cfg.tts_api_format.strip().lower() or "mp3"
    headers = {"Content-Type": "application/json"}
    if cfg.tts_api_key.strip():
        headers["Authorization"] = f"Bearer {cfg.tts_api_key.strip()}"

    voices = _voice_map(hosts)

    # Optional: validate voice ids via /voices (non-fatal if unsupported).
    known_voices: set[str] | None = None
    try:
        base = url.rsplit("/audio/speech", 1)[0]
        with httpx.Client(timeout=10) as client:
            r = client.get(f"{base}/voices", headers=headers)
            if r.status_code == 200:
                data = r.json()
                cands = data.get("data", [])
                if cands and isinstance(cands[0], dict):
                    known_voices = {c.get("voice_id") or c.get("id") for c in cands}
    except Exception as exc:  # noqa: BLE001
        logger.debug("TTS /voices probe unavailable: %s", exc)
        known_voices = None

    if known_voices:
        for h in hosts:
            v = voices.get(h.index)
            if v and v not in known_voices:
                raise PodcastError(
                    f"Voice '{v}' ({h.name}) not found on the TTS server. "
                    f"Available: {', '.join(sorted(known_voices))}"
                )

    media = b""
    total = len(script_lines) or 1
    with httpx.Client(timeout=120) as client:
        for idx, line in enumerate(script_lines, start=1):
            voice = voices.get(line.host_index) or voices.get(1) or "tts-1"
            payload = {"model": model, "input": line.text, "voice": voice, "format": fmt}
            try:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                media += resp.content
            except Exception as exc:  # noqa: BLE001
                _log_line_error("localhost TTS", voice, exc)
            _notify_progress(progress, idx, total, "synthesizing", f"Synthesizing line {idx}/{total} ({voice})")

    return _finalize(media, out_path, ext="mp3" if fmt == "mp3" else "mp3", provider="localhost")


def _synthesize_cloudflare_tts(script_lines, out_path, hosts, cfg, progress=None):
    """Cloudflare Workers AI text-to-speech."""
    account = cfg.cf_account_id.strip()
    token = cfg.cf_api_token.strip()
    model = cfg.cf_model.strip() or "@cf/microsoft/windows-captioning-or-tts"
    if not account or not token:
        raise PodcastError(
            "Cloudflare TTS needs an Account ID and API Token (Settings → Podcast)."
        )
    endpoint = (
        f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    voices = _voice_map(hosts)

    media = b""
    total = len(script_lines) or 1
    with httpx.Client(timeout=120) as client:
        for idx, line in enumerate(script_lines, start=1):
            voice = voices.get(line.host_index) or ""
            payload = {"text": line.text}
            if voice:
                payload["voice"] = voice
            try:
                resp = client.post(endpoint, headers=headers, json=payload)
                resp.raise_for_status()
                media += _extract_audio_bytes(resp)
            except Exception as exc:  # noqa: BLE001
                _log_line_error("Cloudflare TTS", voice or model, exc)
            _notify_progress(progress, idx, total, "synthesizing", f"Synthesizing line {idx}/{total} ({voice or model})")

    return _finalize(media, out_path, ext="mp3", provider="cloudflare")


def _synthesize_google_tts(script_lines, out_path, hosts, cfg, progress=None):
    """Google Cloud Text-to-Speech (v1 text:synthesize)."""
    api_key = cfg.google_api_key.strip()
    lang = cfg.google_language_code.strip() or "en-US"
    if not api_key:
        raise PodcastError("Google TTS needs an API key (Settings → Podcast).")

    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    # gender -> ssmlGender mapping; Google also picks a standard voice from language+gender.
    ssml = {"female": "FEMALE", "male": "MALE", "neutral": "NEUTRAL"}
    headers = {"Content-Type": "application/json"}

    media = b""
    total = len(script_lines) or 1
    with httpx.Client(timeout=120) as client:
        for idx, line in enumerate(script_lines, start=1):
            host = next((h for h in hosts if h.index == line.host_index), hosts[0] if hosts else None)
            if host is None:
                continue
            gender = ssml.get(host.gender, "NEUTRAL")
            voice_spec = {"languageCode": lang}
            if host.voice.strip():
                voice_spec["name"] = host.voice.strip()
            else:
                voice_spec["ssmlGender"] = gender
            payload = {
                "input": {"text": line.text},
                "voice": voice_spec,
                "audioConfig": {"audioEncoding": "MP3"},
            }
            try:
                resp = client.post(url, headers=headers, json=payload, timeout=120)
                resp.raise_for_status()
                data = resp.json()
                audio_b64 = data.get("audioContent") or ""
                if audio_b64:
                    media += base64.b64decode(audio_b64)
            except Exception as exc:  # noqa: BLE001
                _log_line_error("Google TTS", host.voice or lang, exc)
            _notify_progress(progress, idx, total, "synthesizing", f"Synthesizing line {idx}/{total} ({host.name})")

    return _finalize(media, out_path, ext="mp3", provider="google")


def _synthesize_edge_tts(script_lines, out_path, hosts, cfg, progress=None):
    """Microsoft edge-tts (free, needs internet)."""
    import asyncio
    import edge_tts

    voices = _voice_map(hosts)

    media = b""
    total = len(script_lines) or 1
    for idx, line in enumerate(script_lines, start=1):
        voice = voices.get(line.host_index) or voices.get(1)
        if not voice:
            continue
        communicate = edge_tts.Communicate(line.text, voice)

        async def _gather():
            chunks = b""
            async for ch in communicate.stream():
                if ch["type"] == "audio":
                    chunks += ch["data"]
            return chunks

        try:
            media += asyncio.run(_gather())
        except Exception as exc:  # noqa: BLE001
            logger.warning("edge-tts failed on a line (%s): %s", voice, exc)
        _notify_progress(progress, idx, total, "synthesizing", f"Synthesizing line {idx}/{total} ({voice})")

    return _finalize(media, out_path, ext="mp3", provider="edge-tts")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_audio_bytes(resp: httpx.Response) -> bytes:
    """Return audio bytes from a response that may be raw audio or JSON/base64."""
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype:
        data = resp.json()
        # Cloudflare returns {"result":{"audio":"base64..."}} for some TTS models
        result = data.get("result") or {}
        b64 = result.get("audio") if isinstance(result, dict) else None
        if isinstance(result, dict) and not b64:
            b64 = data.get("audio")
        if b64:
            return base64.b64decode(b64)
        return b""
    return resp.content


def _notify_progress(progress, done: int, total: int, action: str, message: str = "") -> None:
    """Call an optional progress callback with (done, total, action, message)."""
    if progress is None:
        return
    try:
        progress(done, total, action, message)
    except Exception:  # noqa: BLE001
        logger.debug("progress callback raised, ignoring")


def _log_line_error(provider: str, voice: str, exc: Exception) -> None:
    try:
        body = ""
        if exc and getattr(exc, "response", None) is not None:
            try:
                body = (exc.response.text or "")[:200]
            except Exception:  # noqa: BLE001
                pass
        logger.warning("%s failed on a line (%s): %s %s", provider, voice, exc, body)
    except Exception:  # noqa: BLE001
        logger.warning("%s failed on a line (%s): %s", provider, voice, exc)


def _finalize(
    media: bytes, out_path: Path, *, ext: str, provider: str
) -> tuple[float, Path]:
    if not media:
        raise PodcastError(
            f"{provider} produced no audio. Check the Settings and the Log Viewer."
        )
    ext = ext.lstrip(".")
    target = out_path.with_suffix("." + ext)
    target.write_bytes(media)
    # rough duration estimate: ~16 KB/s of compressed audio ~ 1 sec
    duration = round(len(media) / 16000, 1)
    logger.info("%s wrote audio (%d bytes) to %s", provider, len(media), target)
    return duration, target


def _synthesize_audio(
    script_lines: list[ScriptLine],
    out_path: Path,
    hosts: list[Host],
    provider: str | None = None,
    progress=None,
) -> tuple[float, Path]:
    """Dispatch to the configured TTS provider.

    `provider` overrides cfg.tts_provider when given (for per-episode picks).
    `progress` is an optional callback(done, total, action, message).
    Returns (approximate duration in seconds, path of written audio file).
    Raises PodcastError on failure.
    """
    cfg = get_settings()
    provider = (provider or cfg.tts_provider or "edge-tts").strip().lower()
    if provider == "disabled":
        raise PodcastError("TTS is disabled (transcript-only mode).")
    if provider == "localhost":
        return _synthesize_localhost_tts(script_lines, out_path, hosts, cfg, progress=progress)
    if provider == "cloudflare":
        return _synthesize_cloudflare_tts(script_lines, out_path, hosts, cfg, progress=progress)
    if provider == "google":
        return _synthesize_google_tts(script_lines, out_path, hosts, cfg, progress=progress)
    if provider == "edge-tts":
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise PodcastError(
                "edge-tts is not installed. Install it with `pip install edge-tts`, "
                "or pick another TTS provider in Settings."
            ) from exc
        return _synthesize_edge_tts(script_lines, out_path, hosts, cfg, progress=progress)
    raise PodcastError(f"Unknown TTS provider '{provider}'.")


def generate_podcast(
    *,
    project_id: str,
    document_ids: list[str],
    title: str,
    model: str | None = None,
    provider: str | None = None,
    progress=None,
) -> dict:
    """Generate a podcast for the given documents and persist it.

    `provider` optionally overrides the configured TTS provider for this episode.
    `progress` is an optional callback(done, total, action, message) invoked as
    generation moves through its stages (summarize -> script -> synthesize) and
    per synthesized line, so a UI can show a live progress bar.
    Returns the podcast row dict (status 'ready' or 'error').
    """
    cfg = get_settings()
    hosts = get_hosts(cfg)
    use_provider = (provider or cfg.tts_provider or "").strip().lower()
    meta = MetadataStore()
    try:
        pod = meta.create_podcast(
            project_id=project_id, title=title or "Untitled deep dive",
            document_ids=document_ids,
        )
        pod_id = pod["id"]
        meta.update_podcast(pod_id, status="generating", tts_provider=use_provider)

        try:
            _notify_progress(progress, 0, 100, "summarize", "Summarizing selected documents…")
            context = _gather_context(document_ids, meta)
            _notify_progress(progress, 20, 100, "script", "Writing the episode script…")
            script = _generate_script(title or "Untitled deep dive", context, hosts, model=model)
            meta.update_podcast(pod_id, script=script)

            lines = parse_script(script, hosts)
            audio_path = ""
            duration = 0.0
            if lines:
                def _tts_progress(done, total, action, message):
                    # scale the per-line synthesis (0..100) into the 20..100 band
                    frac = done / max(total, 1)
                    _notify_progress(progress, 20 + int(frac * 80), 100, action, message)
                try:
                    out = cfg.podcast_dir / f"{pod_id}.audio"
                    duration, audio_path = _synthesize_audio(
                        lines, out, hosts, provider=use_provider, progress=_tts_progress,
                    )
                    audio_path = str(audio_path)
                except PodcastError as exc:
                    logger.warning("Podcast audio skipped: %s", exc)
            _notify_progress(progress, 100, 100, "done", "Done.")
            meta.update_podcast(
                pod_id, status="ready", audio_path=audio_path, duration_sec=duration,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Podcast generation failed for %s", pod_id)
            meta.update_podcast(pod_id, status="error", error=str(exc))
        return meta.get_podcast(pod_id)
    finally:
        meta.close()


def preview_voice(
    *,
    provider: str | None = None,
    voice: str | None = None,
    name: str | None = None,
    gender: str | None = None,
    text: str | None = None,
) -> tuple[bytes, str]:
    """Synthesize a short sample ("preview") with a single host's voice.

    `provider` defaults to the configured TTS provider; `voice`/`gender`/`name`
    fall back to the configured host(s) so a quick preview works with no args.
    Returns (audio_bytes, file_extension_without_dot).
    Raises PodcastError on failure.
    """
    cfg = get_settings()
    provider = (provider or cfg.tts_provider or "edge-tts").strip().lower()
    hosts = get_hosts(cfg)

    # resolve a voice + gender for the preview
    if not voice:
        for h in hosts:
            if h.voice:
                voice = h.voice
                gender = gender or h.gender
                name = name or h.name
                break
    gender = (gender or "").strip().lower()

    sample_text = (text or "").strip() or (
        f"Hi there! I'm {name or 'your host'}, and this is how I'll sound "
        "on your deep dive podcast."
    )

    if provider == "localhost":
        if not voice:
            raise PodcastError("No voice configured to preview. Set host voices in Settings → Podcast.")
        payload = {
            "model": cfg.tts_api_model.strip() or "tts-1",
            "input": sample_text,
            "voice": voice,
            "format": cfg.tts_api_format.strip().lower() or "mp3",
        }
        url = cfg.tts_api_url.strip() or "http://localhost:20128/v1/audio/speech"
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "http://" + url
        headers = {"Content-Type": "application/json"}
        if cfg.tts_api_key.strip():
            headers["Authorization"] = f"Bearer {cfg.tts_api_key.strip()}"
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        return resp.content, cfg.tts_api_format.strip().lower() or "mp3"

    if provider == "cloudflare":
        account = cfg.cf_account_id.strip()
        token = cfg.cf_api_token.strip()
        model = cfg.cf_model.strip() or "@cf/microsoft/windows-captioning-or-tts"
        if not account or not token:
            raise PodcastError("Cloudflare TTS needs an Account ID and API Token (Settings → Podcast).")
        endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"text": sample_text}
        if voice:
            payload["voice"] = voice
        with httpx.Client(timeout=60) as client:
            resp = client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
        return _extract_audio_bytes(resp), "mp3"

    if provider == "google":
        api_key = cfg.google_api_key.strip()
        lang = cfg.google_language_code.strip() or "en-US"
        if not api_key:
            raise PodcastError("Google TTS needs an API key (Settings → Podcast).")
        ssml = {"female": "FEMALE", "male": "MALE", "neutral": "NEUTRAL"}
        voice_spec = {"languageCode": lang}
        if voice:
            voice_spec["name"] = voice
        else:
            voice_spec["ssmlGender"] = ssml.get(gender, "NEUTRAL")
        payload = {
            "input": {"text": sample_text},
            "voice": voice_spec,
            "audioConfig": {"audioEncoding": "MP3"},
        }
        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
        headers = {"Content-Type": "application/json"}
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        b64 = data.get("audioContent") or ""
        if not b64:
            raise PodcastError("Google TTS returned no audio for the preview.")
        return base64.b64decode(b64), "mp3"

    if provider == "edge-tts":
        import asyncio
        import edge_tts
        if not voice:
            raise PodcastError("No voice configured to preview. Set host voices in Settings → Podcast.")
        communicate = edge_tts.Communicate(sample_text, voice)

        async def _gather():
            chunks = b""
            async for ch in communicate.stream():
                if ch["type"] == "audio":
                    chunks += ch["data"]
            return chunks

        return asyncio.run(_gather()), "mp3"

    if provider == "disabled":
        raise PodcastError("TTS is disabled. Pick a provider in Settings → Podcast to preview voices.")

    raise PodcastError(f"Unknown TTS provider '{provider}'.")
