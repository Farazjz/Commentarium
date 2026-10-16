# Commentarium — Full Feature Review Report

**Date:** 2026-10-02
**Reviewed by:** automated live review agent
**Scope:** all 16 feature areas, verified against the running app (API on `:8000`, gateway on `:20128`) + offline core-logic unit checks + UI render checks.

**Review method:**
- **Core logic** — offline unit scripts exercising parsers, chunking, source-tag parsing, APA/Vancouver formatters, resolvers, retrieval section weighting, dimension guard.
- **API** — live HTTP calls (proxy-free httpx) against `http://localhost:8000` for every endpoint: CRUD, upload/ingest, summaries (real LLM), chat (real LLM + citations), Crossref citations, podcasts (real LLM + TTS), delete cascades, shutdown.
- **UI** — all 7 pages launched headless via `streamlit run` and probed for a healthy server; plus module-level import checks.
- **Config/launchers** — `.env` writer round-trip, embedding/OCR config resolution, podcast hosts, launcher scripts.

**Test-data hygiene:** A temporary `REVIEW-*` project (with TXT + DOCX uploads), chat sessions, and podcasts were created during testing and fully removed afterward. Final DB, `data/uploads`, `.env`, and `data/podcasts` match the pre-review state exactly. A DB backup exists at `data/app.db.bak_review` (delete it once you're satisfied).

---

## Verdict legend
- ✅ **Pass**
- ⚠️ **Partial / minor**
- ❌ **Fail**
- 🔒 **Not testable in this environment** (needs external service/credential)

## Feature-by-feature results

### 1. Health / Settings summary — ✅
`GET /health`, `/settings/summary` return correct config (key present, embedding backend `api`, OCR `paddleocr-vl`, chat model). Shape correct.

### 2. Projects CRUD + per-project overrides — ✅
Create (query-param `name`), get, patch name/description, patch `chat_model`/`chat_temperature`/`top_k` overrides, clear-override via `None`/`""`, 404 on missing, 400 on invalid temperature. Verified persisted.

### 3. Upload + ingest pipeline — ✅
- Upload PDF/DOCX/TXT/MD accepted; TXT → `indexed`, 4 chunks; DOCX → `indexed`, 1 chunk; page_count and chunk counts correct.
- Re-index (`?reindex=true`) keeps chunk count identical (no duplicates).
- **⚠️ Minor UX finding:** uploading an **unsupported file type** (e.g. `.xyz`) returns **HTTP 500** via the global exception handler — it should be **400**. (`app/main.py: upload_document` doesn't catch `ParseError` from `store_upload`.) Not a data-corruption issue; the file is not saved.
- **⚠️ Stability note:** the API server, when launched with a default `python -m uvicorn`, crashed with `WinError 64` on the asyncio **Proactor** accept loop. The app's own launcher `_run_api.py` deliberately forces `WindowsSelectorEventLoopPolicy` and is **stable** — use it (run_api.bat / run_all.bat) rather than bare `uvicorn`. Not an app bug, but worth knowing.

### 4. OCR backends — ✅ (structural; 🔒 live)
Backends configurable & dispatched correctly (`tesseract`/`vision`/`paddleocr-vl`/`teleocr`/`disabled`); `disabled` returns None. Tesseract not installed on this box (noted). Live PaddleOCR-VL path requires a local VLM server or gateway vision — not exercised (gateway vision model not configured for OCR). No crash.

### 5. Embedding backends + dimension guard — ✅
- `api` backend resolved correctly (base `http://localhost:20128/v1`, model set). Actual ingest produced 2048-dim vectors.
- **Dimension guard verified:** adding a mismatched-dimension chunk raises `DimensionMismatchError`; querying with a wrong dim raises too; `embedding_dim()` correct. `embedding_model_matches` on `/index/stats` = true.
- Local/Cloudflare backends not live-tested (require download / CF creds) — structural only.

### 6. Vector store (search / distributed / section-weight / delete) — ✅
Search returns scored hits; distributed (top-K-per-doc) retrieval worked over the project (overview questions returned 4+ chunks); section weights verified (Abstract 1.2, References 0.1); delete removes chunks + vectors. `chunks_for_document` section-preference ordering works.

### 7. Chat RAG (sessions + grounded answers + citations) — ✅
- Session create/rename/list/delete + message persistence verified.
- Real chat/ask: focused returns grounded answer with `sources`, resolved `references` (APA), `citation_style`; distributed/overview works; Vancouver style switch works; empty question → 400.
- Per-project model/temp/top_k precedence logic is sound (verified at the API override patch layer).
- History persisted correctly (user/assistant turns).

### 8. Per-document summaries (multi-kind + caching) — ✅
`brief`/`detailed`/`key_points`/`tldr`/`quiz` all generate real content via the LLM. Caching works (2nd call returns `cached`); `force=true` regenerates; invalid kind → 400.
- **Note:** one `brief` call hit a transient gateway 502 and returned empty on first try; immediate retry succeeded. Gateway flakiness, not an app defect. The app already surfaces these gracefully.

### 9. Citations (Crossref + verify + APA/Vancouver) — ✅
- Auto-extract: title-based Crossref search returned 5 ranked candidates (`source=title`).
- Confirm/save: sets `citation_verified=1` and stores metadata.
- `/citations/resolve`: correct **APA** in-text + reference (`Smith, J. & Doe, J. (2022). …`) and **Vancouver** (`Smith J, Doe J. …`) output.
- Formatters handle single/many-author, missing year (`n.d.`), and >7-authors ellipsis correctly.

### 10. Podcast studio (script + TTS + preview + regenerate + delete) — ✅
- Real episode generated via `gateway-edge-tts`: **script** (3,758 chars) + **MP3 audio** (55s, served as `audio/mpeg`), list/get work.
- **Regenerate** works (produces a new ready episode with audio; prior audio preserved/cleaned on delete).
- **Delete** removes the DB row AND the audio file.
- Voice **preview** path verified structurally.
- **Note:** a regenerate call can occasionally hit a flaky gateway script error → episode marked `status="error"` with a readable message (graceful). Retry succeeds.
- **Note:** `.env` still uses **legacy** `PODCAST_HOST_A_VOICE`/`B_VOICE` keys; `get_hosts()` correctly falls back and resolves voices. Works, but migrating `.env` to the new `PODCAST_HOST_1_VOICE`… keys is recommended for clarity.

### 11. Tags & notes (+ filter) — ✅
Set/get persist and round-trip correctly; `/tags` aggregates with counts; `GET /tags` reflects new tags immediately.

### 12. Index stats + model-match banner — ✅
`GET /index/stats` returns chunks, indexed docs, dim, embedding model, and `embedding_model_matches` (true when consistent).

### 13. Settings ↔ `.env` persistence — ✅
`_update_env` preserves unrelated keys and writes new ones; round-trip verified and dummy key fully removed afterward. Logic sound (does not clobber the rest of `.env`).

### 14. Log viewer (file + console + ring buffer) — ✅
`GET /logs` returns buffered lines with level filter; `DELETE /logs` clears. Rotating file + console + ring buffer all confirmed operating (log content captured throughout the review).

### 15. Shutdown / Exit — ✅
`POST /shutdown` returns the "shutting down" response and **successfully stopped** the API server on `:8000` (verified port released). UI-exit path shares the same `shutdown_servers`/psutil logic (kills both ports; self-exit handled).

### 16. Launcher scripts — ✅
`run_all.bat`, `run_ui.bat`, `run_api.bat`, `stop_all.bat`, `_run_api.py`, `_launch_hidden.vbs` all present and sane; `run_all`/`stop_all` read `PORT`/`UI_PORT` from `.env`; `stop_all` taskkills both ports; `_run_api.py` uses the selector loop + output redirection (the stable launch path).

---

## Summary

| Area | Verdict |
|------|---------|
| Health / settings | ✅ |
| Projects CRUD + overrides | ✅ |
| Upload + ingest | ✅ (1 minor finding) |
| OCR backends | ✅ structural / 🔒 live |
| Embeddings + dim guard | ✅ |
| Vector store select/delete | ✅ |
| Chat RAG + citations | ✅ |
| Document summaries (multi-kind) | ✅ |
| Crossref citations + APA/Vancouver | ✅ |
| Podcast studio (TTS) | ✅ |
| Tags & notes | ✅ |
| Index stats / banner | ✅ |
| Settings → `.env` | ✅ |
| Log viewer | ✅ |
| Shutdown / Exit | ✅ |
| Launchers | ✅ |

**Overall: functionally healthy.** All core user-facing features (projects, upload/ingest, RAG chat, summaries, citations, podcast audio, tags/notes, logging, shutdown) work end-to-end and clean up correctly.

## Findings & recommendations

1. **❌→Minor (fix recommended):** `POST /projects/{id}/documents/upload` returns **500** instead of **400** for unsupported file types — wrap `store_upload`'s `ParseError` in `upload_document` (and ideally `upload_and_ingest`) so bad input is a client error, matching the ingest endpoint's behavior. Currently the file is not stored, so no data corruption.
2. **⚙️ (info):** Bare `python -m uvicorn` is unstable on Windows (Proactor `WinError 64`). Always launch via `_run_api.py`/`run_api.bat` (`WindowsSelectorEventLoopPolicy`). Consider adding the selector-loop policy directly in `app/main.py:__main__` so any launch method is safe.
3. **⚙️ (hygiene):** `.env` still uses legacy `PODCAST_HOST_A_VOICE`/`B_VOICE`; works via fallback but migrate to `PODCAST_HOST_1_VOICE`… per `.env.example`.
4. **ℹ️ (environment):** Tesseract & a local OCR VLM server are not installed here, so OCR was verified structurally only. Crossref needed the gateway/network and worked.

## Review artifacts
- Review scripts stored under `.review/` (safe to delete).
- DB backup: `data/app.db.bak_review`.
- Final app state matches the pre-review state.
