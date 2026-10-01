"""Podcast generation for the app.

A NotebookLM-style feature: pick one or more documents (or a whole project),
and the app writes a two-host "deep dive" conversation script about them,
then optionally turns it into an MP3 with edge-tts (free, no API key, needs
internet). If TTS is unavailable the transcript is still saved and viewable.

Pipeline:
  1. Gather context: per-document key-point summaries of the selected docs.
  2. Generate a two-host dialogue script with the configured chat model.
  3. (Optional) Synthesize audio via edge-tts: host A + host B voices.
  4. Persist script + audio path in the podcasts table.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings
from app.db.metadata import MetadataStore
from app.models_openrouter import LLMClientError, chat_completion
from app.rag.summarize import summarize_many_documents

logger = logging.getLogger("app")


class PodcastError(Exception):
    pass


# Matches script lines like:  HOST A: ...   /   HOST B: ...
_LINE_RE = re.compile(r"^\s*(HOST\s*A|HOST\s*B|Host\s*A|Host\s*B)\s*:\s*(.*)$", re.IGNORECASE)

_SCRIPT_SYSTEM = (
    "You are a podcast producer creating a lively, conversational 'deep dive' "
    "episode between two hosts, HOST A and HOST B, who are discussing a set of "
    "research papers for a curious but non-expert listener.\n\n"
    "Rules:\n"
    "- Base every claim strictly on the provided summaries; never invent facts, "
    "numbers, or studies.\n"
    "- Write natural, engaging dialogue with a clear flow: intro -> what the "
    "papers are about -> key findings and how they connect -> why it matters -> "
    "wrap-up.\n"
    "- Alternate between HOST A and HOST B. Each line starts on its own line with "
    "'HOST A:' or 'HOST B:' followed by the spoken text (no markdown, no quotes).\n"
    "- Keep it roughly 700-1000 words total.\n"
    "- Output ONLY the dialogue lines, nothing else."
)


@dataclass
class ScriptLine:
    host: str  # "A" | "B"
    text: str


def parse_script(script: str) -> list[ScriptLine]:
    """Parse raw generated text into (host, text) turns."""
    lines: list[ScriptLine] = []
    for ln in (script or "").splitlines():
        m = _LINE_RE.match(ln)
        if not m:
            # skip markdown/blank noise between dialogue lines
            continue
        host = "A" if m.group(1).upper().endswith("A") else "B"
        text = m.group(2).strip()
        if text:
            lines.append(ScriptLine(host=host, text=text))
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


def _generate_script(title: str, context: str, model: str | None = None) -> str:
    user_msg = (
        f"The deep-dive episode is titled '{title}'.\n\n"
        f"Here are the papers the hosts will discuss:\n\n{context}\n\n"
        "Write the episode dialogue now."
    )
    messages = [
        {"role": "system", "content": _SCRIPT_SYSTEM},
        {"role": "user", "content": user_msg},
    ]
    try:
        return chat_completion(messages, temperature=0.8, max_tokens=2200, model=model)
    except LLMClientError as exc:
        raise PodcastError(f"Could not generate podcast script: {exc}") from exc


def _synthesize_edge_tts(
    script_lines: list[ScriptLine], out_path: Path
) -> float:
    """Synthesize MP3 with Microsoft edge-tts (free, needs internet)."""
    import asyncio
    import edge_tts

    cfg = get_settings()
    voices = {"A": cfg.podcast_host_a_voice, "B": cfg.podcast_host_b_voice}

    media = b""
    for line in script_lines:
        voice = voices.get(line.host, voices["A"])
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
            logger.warning("edge-tts failed on a line: %s", exc)
            continue

    if not media:
        raise PodcastError("edge-tts produced no audio.")
    written = _write_audio(out_path, media, ext="mp3")
    return _estimate_duration(len(media)), written


def _synthesize_api_tts(
    script_lines: list[ScriptLine], out_path: Path
) -> float:
    """Synthesize audio via an OpenAI-compatible /v1/audio/speech endpoint.

    Your local TTS server (Kokoro, Silero, Piper, vLLM, etc.) typically also
    supports the `/v1/audio/voices` listing route — we call it, if present, so
    bad voice ids fail fast with a friendly message.
    """
    cfg = get_settings()
    url = cfg.tts_api_url.strip()
    if not url:
        raise PodcastError("TTS API URL is not configured (Settings → Podcast).")
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url

    model = cfg.tts_api_model.strip() or "tts-1"
    fmt = cfg.tts_api_format.strip().lower() or "mp3"
    headers = {"Content-Type": "application/json"}
    if cfg.tts_api_key.strip():
        headers["Authorization"] = f"Bearer {cfg.tts_api_key.strip()}"

    voices = {"A": cfg.podcast_host_a_voice, "B": cfg.podcast_host_b_voice}

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
        for host, voice in voices.items():
            if voice and voice not in known_voices:
                raise PodcastError(
                    f"Voice '{voice}' (Host {host}) not found on the TTS server. "
                    f"Available: {', '.join(sorted(known_voices))}"
                )

    media = b""
    with httpx.Client(timeout=120) as client:
        for line in script_lines:
            voice = voices.get(line.host, voices["A"])
            payload = {
                "model": model,
                "input": line.text,
                "voice": voice,
                "format": fmt,
            }
            try:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                media += resp.content
            except Exception as exc:  # noqa: BLE001
                resp_body = ""
                try:
                    resp_body = (resp.text or "")[:200] if resp else ""
                except Exception:  # noqa: BLE001
                    pass
                logger.warning("TTS API failed on a line (%s): %s %s", voice, exc, resp_body)
                continue

    if not media:
        raise PodcastError(
            "TTS API produced no audio. Check the endpoint, model id, and voice ids "
            "in Settings → Podcast, and see the Log Viewer."
        )
    ext = "mp3" if fmt == "mp3" else ("wav" if fmt == "wav" else "mp3")
    written = _write_audio(out_path, media, ext=ext)
    return _estimate_duration(len(media)), written


def _write_audio(out_path: Path, media: bytes, *, ext: str) -> Path:
    """Write audio bytes to out_path, renaming the file's extension if needed."""
    target = out_path.with_suffix("." + (ext.lstrip(".")))
    target.write_bytes(media)
    logger.info("Wrote audio (%d bytes) to %s", len(media), target)
    return target


def _estimate_duration(nbytes: int) -> float:
    """Rough duration estimate in seconds (conservative ~16 KB/s for audio)."""
    return round(nbytes / 16000, 1)


def _synthesize_audio(script_lines: list[ScriptLine], out_path: Path) -> tuple[float, Path]:
    """Dispatch to the configured TTS backend.

    Returns (approximate duration in seconds, path of written audio file).
    Raises PodcastError on failure.
    """
    cfg = get_settings()
    backend = (cfg.tts_backend or "edge-tts").strip().lower()
    if backend == "disabled":
        raise PodcastError("TTS is disabled (transcript-only mode).")
    if backend == "api":
        return _synthesize_api_tts(script_lines, out_path)
    if backend == "edge-tts":
        try:
            import edge_tts  # noqa: F401  (checked here for an early, clear error)
        except ImportError as exc:
            raise PodcastError(
                "edge-tts is not installed. Install it with `pip install edge-tts`, "
                "or switch to TTS backend 'api' to use a local TTS server, or set "
                "TTS_BACKEND=disabled for transcript-only."
            ) from exc
        return _synthesize_edge_tts(script_lines, out_path)
    raise PodcastError(f"Unknown TTS backend '{backend}'.")


def generate_podcast(
    *,
    project_id: str,
    document_ids: list[str],
    title: str,
    model: str | None = None,
) -> dict:
    """Generate a podcast for the given documents and persist it.

    Returns the podcast row dict (status 'ready' or 'error').
    """
    meta = MetadataStore()
    try:
        pod = meta.create_podcast(
            project_id=project_id, title=title or "Untitled deep dive",
            document_ids=document_ids,
        )
        pod_id = pod["id"]

        # 1. context
        meta.update_podcast(pod_id, status="generating")
        try:
            context = _gather_context(document_ids, meta)
            # 2. script (allow a model override, else default chat model)
            script = _generate_script(title or "Untitled deep dive", context, model=model)

            # save the script immediately so a TTS failure still leaves a podcast
            meta.update_podcast(pod_id, script=script)

            # 3. audio (optional)
            lines = parse_script(script)
            audio_path = ""
            duration = 0.0
            if lines:
                try:
                    cfg3 = get_settings()
                    out = cfg3.podcast_dir / f"{pod_id}.audio"
                    duration, audio_path = _synthesize_audio(lines, out)
                    audio_path = str(audio_path)
                except PodcastError as exc:
                    logger.warning("Podcast audio skipped: %s", exc)
            meta.update_podcast(
                pod_id, status="ready", audio_path=audio_path, duration_sec=duration,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Podcast generation failed for %s", pod_id)
            meta.update_podcast(pod_id, status="error", error=str(exc))
        return meta.get_podcast(pod_id)
    finally:
        meta.close()
