# 🧬 Commentarium

> **More info & full documentation:** [github.com/Farazjz/Commentarium](https://github.com/Farazjz/Commentarium)

A **local** research assistant for your MSc biotechnology thesis. Upload **PDF** and
**Word** documents, chat over them with the AI model of your choice, and get
answers with **page-level citations** in APA 7 or Vancouver style.

Built as a lean, thesis-focused alternative to
[open-notebook](https://github.com/lfnovo/open-notebook) — privacy-first, runs
entirely on your machine, using your own **OpenAI-compatible** API key — any
online router (OpenRouter, Gemini, OpenAI…) **or a fully local AI** (LM Studio,
Ollama, vLLM, LiteLLM…).

---

## ✨ Features

- 🔒 **100% local & private** — your files never leave your computer
- 📄 Upload **PDF** and **DOCX** files
- 🤖 Bring your own AI — paste any **OpenAI-compatible** key from an online
  router **or a local server** (OpenRouter, LiteLLM, LM Studio, Ollama via its
  OpenAI bridge, vLLM…), pick any **chat** model and **embedding** backend
  (local `sentence-transformers` is free & private)
- 🗂️ **Projects** to organise research (e.g. per thesis chapter)
- 💬 **Chat** over your documents, with grounded, page-level citations
  + resolved APA 7 / Vancouver references shown with each answer
- 🎛️ **Per-project model control** — each project can override the chat model and
  temperature (falling back to global defaults), plus a global temperature setting
- 📝 **Citations** — auto-extract metadata (title/authors/year/journal/DOI via
  Crossref), verify it, then render **APA 7** or **Vancouver** references
- 🏷️ **Tags & notes per document** — freeform tags (filterable) and personal notes
  on every paper
- 🧠 **Multi-kind per-document summaries** — Brief, Detailed, Key points, TL;DR,
  and a study **Quiz**, all cached so they don't burn tokens twice
- 🎙️ **Podcast studio (NotebookLM-style)** — select papers, and the app writes a
  two-host "deep dive" conversation and reads it aloud as an MP3. Choose the TTS:
  **edge-tts** (free, no key), **VoiceStudio** (locally running voice
  cloning/design server), or **any OpenAI-compatible `/v1/audio/speech`
  server** (Kokoro / Silero / Piper / vLLM — fully local & private), with
  per-host voice ids / languages and model/format controls (transcript-only fallback)
- 🛡️ **Embedding-dim guard** — prevents silent corruption if you change
  embedding models; Projects page shows corpus-model identity and warns on mismatch
- 🪵 **Log viewer** — live, filterable logs (file + console + in-app) for debugging

## 🧱 Architecture

```
Your files (PDF/DOCX)
      │  upload
      ▼
┌─────────────────────┐
│  Ingest pipeline     │  parse (PyMuPDF / python-docx)
│  parse → chunk       │  smart chunking w/ page mapping
│  embed → store       │  local embeddings → NumPy/SQLite vector store
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│  Retrieval (RAG)     │  cosine search → top-k chunks
│                      │  each chunk carries file + page(s)
└─────────┬───────────┘
          ▼
┌─────────────────────┐        ┌─────────────────────┐
│  Answer generation   │ ─────▶ │  Citation layer      │
│  (your AI gateway)   │        │  APA 7 / Vancouver   │
└─────────────────────┘        └─────────────────────┘
   answer + in-text markers → resolved to real Reference list
```

- **Backend:** FastAPI
- **Frontend:** Streamlit
- **AI:** your OpenAI-compatible chat gateway — online router (OpenRouter,
  Gemini…) or fully local (LM Studio, vLLM, Ollama proxy, LiteLLM…)
- **Embeddings:** sentence-transformers (local) or your gateway's `/embeddings`
- **Vector store:** custom lightweight NumPy + SQLite (no C-compiler needed)
- **Metadata store:** SQLite (projects, documents, citation metadata)
- **Parsers:** PyMuPDF, python-docx (with optional OCR of scanned pages)
- **Logging:** rotating file + console + in-app ring buffer

> **Why a custom vector store?** On Windows + Python 3.12, ChromaDB needs
> `chroma-hnswlib`, which must be compiled from source (Requires Visual C++
> Build Tools). At the single-user / 100–300 file scale, direct cosine search
> over a NumPy matrix is simpler, dependency-free and more than fast enough.

## 🚀 Getting started

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

> If `sentence-transformers` needs to download a model on first use, ensure you
> have internet access (the default local model `BAAI/bge-small-en-v1.5` is
> fetched from Hugging Face once).

### 2. Configure your AI provider

Commentarium talks to **any OpenAI-compatible chat API** — an online router or
a server running locally on your machine. Copy the template (you can also do
this in the UI):

```bash
copy .env.example .env
```

**Option A — online router (e.g. OpenRouter).** Set a key in `.env`:

```
OPENROUTER_API_KEY=sk-or-...
```

Get one at **https://openrouter.ai/keys** (a single key gives you access to
literally thousands of models — Claude, Gemini, Llama, etc.).

**Option B — fully local / offline.** No key needed. Point `OPENROUTER_BASE_URL`
(and leave the key empty, or use your local server's key if it requires one) at
the **OpenAI-compatible endpoint** of your local server — e.g. LM Studio
(`http://127.0.0.1:1234/v1`), Ollama (`http://localhost:11434/v1`), vLLM, or a
LiteLLM proxy. The Settings page in the UI can configure the same values.

> **Embeddings** are separate and work the same way: `local` (free &
> private `sentence-transformers`, no network), any OpenAI-compatible
> `/embeddings` endpoint (online or local), or Cloudflare Workers AI.

### 3. Run the app

Open **two terminals** from the project folder:

```bash
# Terminal 1 — the UI
run_ui.bat        # (or: streamlit run app/ui/main.py --server.port 8501)

# Terminal 2 — the backend
run_api.bat       # (or: python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload)
```

Then open **http://localhost:8501** in your browser.

### 4. First steps in the UI

1. **⚙️ Settings** → paste your AI/gateway key → **Test connection** → pick your
   chat model. Local setups: point the base URL at your local server instead of
   using a key. Set OCR backends (incl. PaddleOCR-VL / TeleOCR) and podcast voices here.
2. **📁 Projects & Files** → create a project, upload your PDF/Word papers, add
   tags/notes, and generate summaries (Brief/Key points/Quiz…).
3. **💬 Chat** → ask questions; answers come with page-level citations. Set a
   per-project model/temperature in the sidebar.
4. **📝 Citations** → verify document metadata; export APA 7 / Vancouver.
5. **🎙 Podcast studio** → select papers and generate an audio deep-dive (MP3).
6. **🪵 Log Viewer** → debug any errors live.

---

## 📁 Project layout

```
app/
  main.py               # FastAPI entry + endpoints (/health, /projects, /documents, /chat, /citations, /podcasts, ...)
  config.py             # .env settings, storage paths
  logging_setup.py      # rotating file + console + in-app log buffer
  models_openrouter.py  # OpenAI-compatible provider client (test connection, list models, chat, embeddings)
  db/
    vectorstore.py      # NumPy + SQLite vector store (dimension guard, model identity)
    metadata.py         # SQLite projects/documents/citation metadata + tags/notes/summaries/podcasts
    chat.py             # SQLite chat sessions + message history
  ingest/               # parsers (PDF/DOCX + OCR), chunking, embeddings, pipeline
  rag/                  # retrieval (focused + distributed), prompts, generation, chat engine, summaries
  podcast/              # NotebookLM-style script generation + edge-tts audio
  shutdown.py           # helper to stop the local API + UI servers (Exit button)
  citations/            # Crossref lookup, APA 7 / Vancouver formatters, resolution, service
  ui/                   # Streamlit pages (main/Home/Projects/Chat/Podcast/Citations/Settings/Logs)
data/                   # git-ignored: uploads, sqlite, vectors, logs, podcasts
.env                    # git-ignored: your secrets
```

## 🛠️ Development status (phases)

- ✅ **Phase 1 — Foundation**: project skeleton, config, logging system, FastAPI
  backend (`/health`, `/settings`, `/logs`), Streamlit UI shell, custom vector
  store with insert/search/delete + persistence.
- ✅ **Phase 2 — Ingestion**: PDF/DOCX parsers (PyMuPDF / python-docx), OCR of
  scanned pages (Tesseract / vision), smart chunking with page + section
  metadata, local & OpenAI-compatible embedding backends, SQLite metadata store, and
  full project/document management (create project, upload, ingest, re-index,
  delete) with a working Streamlit **Projects & Files** page.
- ✅ **Phase 3 — Chat + RAG**: query embedding + cosine retrieval (project
  filter), grounded answer generation with a strict "only from context +
  `[SRC:doc|page]` source tags" prompt, in-text source-tag parsing into
  structured citations, and SQLite-backed chat sessions/history with a working
  Streamlit **Chat** page.
- ✅ **Phase 4 — Citations**: Crossref metadata extraction (by DOI or title
  search with confidence ranking), user verification workflow, APA 7 +
  Vancouver (NLM) reference formatters, a resolution layer that turns the
  `[SRC:doc|page]` tags from Chat answers into real in-text citations +
  a bibliography, and a Streamlit **Citations** page (verify/edit metadata,
  preview the formatted reference).
- ✅ **Phase 5 — Polish**: embedding-dimension consistency guard (fail loudly
  on model switch + corpus-model mismatch banner), corpus model
  identity tracking, stale-artifact cleanup, `.env.example` completeness,
  README QA/troubleshooting, citations & prefill UI bugfixes.
- ✅ **Phase 6 — Management + retrieval fix**: full project/chat management
  (rename/edit/delete with cleanup), professional UI redesign (project cards,
  inline document actions with **on-demand summaries**), section-weighted
  retrieval (Reference chunks no longer crowd out real content), plus
  **distributed retrieval** (top-K per document) for overview questions —
  with auto-detect heuristics and a "Specific facts / Summarize papers"
  toggle in Chat.
- ✅ **Phase 7 — Exit / shutdown**: a "🛑 Exit" button in the sidebar (and a
  `POST /shutdown` API endpoint) that gracefully stops both the API and UI
  servers via `psutil` PID lookup on the listening ports.
- ✅ **Phase 8 — Research workflow + podcast studio**: multi-kind per-document
  summaries (Brief / Detailed / Key points / TL;DR / Quiz, cached), per-project
  model & temperature overrides, a global temperature setting, document `tags`
  (filterable) + freeform `notes`, and a **Podcast studio** that writes a
  multi-host "deep dive" script over selected papers and reads it aloud (MP3
  download; transcript-only fallback), with TTS via **edge-tts**, a local
  **VoiceStudio** server (voice cloning/design, per-host languages `en`/`fa`,
  dropdown voice picking, connection test), or any OpenAI-compatible
  `/v1/audio/speech` endpoint. Episode list supports **delete** (incl. failed /
  in-progress episodes). OCR also gains the [**PaddleOCR-VL-1.6**](https://github.com/PADDLEPADDLE/PADDLEOCR)
  and [**TeleOCR**](https://github.com/caipeng328/TeleOCR) vision-language backends
  (local VLM server first, gateway fallback).

## 📝 Notes & troubleshooting

- **OCR of scanned PDFs** requires [Tesseract](https://github.com/tesseract-ocr/tesseract)
  installed (free, local). Set its path in Settings (`C:/Program Files/Tesseract-OCR/tesseract.exe`).
- **Embedding model consistency**: the corpus vectors are tied to the embedding
  model used at ingest time. If you switch models afterwards, ingestion and chat
  will fail loudly with a "dimension mismatch" error. Fix it by switching the
  model back in **Settings**, or **Re-index every document** with the new model
  (Projects page → Re-index). The Projects page shows which model produced the
  current corpus and warns on mismatch.
- **Flaky internet**: embedding/chat calls and Crossref lookups need connectivity
  to your gateway / upstream providers. Timeouts and "Bad Gateway" errors usually
  resolve on retry; check the **Log Viewer** for details.
- **Garbled or missing text in a PDF** usually means the pages are scans — install
  Tesseract and set OCR Backend to `tesseract` (Settings → OCR & RAG tuning),
  then Re-index the document. For state-of-the-art document parsing, choose
  `paddleocr-vl` or `teleocr` and point them at a local VLM server (or let them
  fall back to the gateway's vision model).
- **Podcast audio** options (Settings → OCR & RAG tuning → Podcast audio):
  - **edge-tts** requires `pip install edge-tts` (in `requirements.txt`) + internet.
  - **api** uses any OpenAI-compatible `/v1/audio/speech` endpoint — fully local &
    private (e.g. Kokoro, Silero, Piper, vLLM). Set the URL, model id, and the two
    **voice ids** your server knows (some servers expose `/v1/audio/voices`; the
    app validates yours against it). A different voice per host makes them distinct.
  - **VoiceStudio** uses a locally running [VoiceStudio](https://github.com/debpalash/VoiceStudio)
    backend (default `http://localhost:3900`) — fully local voice cloning/design.
    In Settings, **Load voices** lists your cloned profiles / OpenAI aliases and
    you pick each host's voice. Each host's dialogue **language** (`en`/`fa`) is
    chosen per-episode on the Podcast page — it drives both the script language
    and the language sent to VoiceStudio synthesis. Keep the VoiceStudio app
    running while generating (mp3/opus/aac output needs VoiceStudio's bundled
    ffmpeg; wav/flac/pcm do not). Optionally set a Bearer API key when VoiceStudio
    runs on another machine. If the script model still writes English for a
    Persian (`fa`) episode, the app **auto-translates the entire script to
    Persian** before it reaches VoiceStudio, so the audio is guaranteed Persian.
  - Every episode has a **🗑 Delete** button (including failed or still-generating
    ones) that removes the episode and its audio file.
  - If TTS fails or is disabled, the podcast is still generated and saved as a
    transcript.
- All logs are stored in `data/logs/app.log` (rotating). Every UI page shows
  recent errors in a collapsible panel for quick debugging.
- Check `.env.example` for every available setting.

## 🔒 Privacy

Everything runs locally. Your API key is stored only in your local `.env` /
database and used only to call your chat gateway (OpenRouter or a fully local
provider). With a fully local setup nothing ever leaves your machine. Your
documents and embeddings never leave your machine.

## 🙏 Acknowledgements

Commentarium is built with, and relies on, several open-source projects.
Respect their licenses when distributing or modifying this app:

- **[OpenRouter](https://openrouter.ai)** — optional AI gateway (API key; usage
  subject to their [Terms](https://openrouter.ai/terms)).
- **[FastAPI](https://github.com/fastapi/fastapi)** (MIT) — backend web framework.
- **[Streamlit](https://github.com/streamlit/streamlit)** (Apache-2.0) — UI frontend.
- **[sentence-transformers](https://github.com/UKPLab/sentence-transformers)** (Apache-2.0) —
  local embedding models (e.g. `BAAI/bge-small-en-v1.5`, BAAI's own terms apply
  to the model weights).
- **[PyMuPDF](https://github.com/pymupdf/PyMuPDF)** (AGPL-3.0) — PDF parsing.
- **[python-docx](https://github.com/python-openxml/python-docx)** (MIT) — DOCX parsing.
- **[Tesseract OCR](https://github.com/tesseract-ocr/tesseract)** (Apache-2.0) —
  scanned-page OCR (installed separately, local).
- **[PaddleOCR / PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR)** (Apache-2.0,
  repo; the `PaddleOCR-VL-1.6` **model weights** are under separate terms — review
  them before distributing) — the vision-language OCR backends.
- **[TeleOCR](https://github.com/caipeng328/TeleOCR)** — the `teleocr` vision-language
  OCR backend (check its license before redistributing the model).
- **[Crossref API](https://www.crossref.org/documentation/retrieve-metadata/rest-api)**
  (public metadata API, used under their terms) — citation lookup.
- **[edge-tts](https://github.com/rany2/edge-tts)** (LGPL-3.0) — podcast TTS.
- **[VoiceStudio](https://github.com/debpalash/VoiceStudio)** (AGPL-3.0) — optional
  local voice-cloning TTS server (separate app, runs on `:3900`).
- **[open-notebook](https://github.com/lfnovo/open-notebook)** — the project
  Commentarium was inspired by (thesis-focused, leaner alternative).
- **[huggingface_hub / Hugging Face models](https://huggingface.co)** — model
  downloads; each model's license governs the weights.

If you reuse or extend Commentarium, keep this list current so downstream users
know the licenses of the components bundled or required at runtime.
