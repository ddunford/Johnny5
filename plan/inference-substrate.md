# Inference substrate — verified reality (inference.lan)

Verified end-to-end on **2026-05-26** by probing + SSH. This supersedes the original SPEC/`local/inference-services.md` assumptions, several of which were wrong. Phase 0 clients MUST build against what's documented here, not the old contract.

## Hardware
- Host `inference.lan`: **2× NVIDIA RTX 3060 (12 GB each = 24 GB VRAM)**, 23 GB system RAM (tight — swap was full).
- Runs ~18 containers (Open-WebUI, YOLO variants, OmniParser, OCR, Qdrant, Jupyter, TTS, STT, SearXNG, embeddings, ollama…). ~6 GB VRAM is consumed by non-Ollama services across the two cards.

## What was wrong in the original spec → corrected
| Assumption (old) | Reality (verified) |
|---|---|
| Model `qwen3.5:9b` | No such tag. Models present: `gemma4:e4b`, `qwen3.5-9b-128k`, `qwen2.5:14b`, `qwen2.5-coder:14b`, `llama3.1:8b`, `hermes3:8b`, `deepseek-r1:14b`, gemma-4 variants |
| Qwen is multimodal (vision) | Qwen tags here are **text-only**. **`gemma4:e4b` is the multimodal one** (vision verified — identified a red test image as "Red") |
| `:8001` replica for load-balance | **Down** (HTTP 000). Single Ollama instance only |
| TEI native embeddings (`/embed` + `{inputs,truncate}` → `[[...]]`) | Custom Flask server. Working call: `POST /embed {"inputs":"..."}` → `{"embeddings":[[...]]}`. 1024-d (bge-m3). `/v1/embeddings`, `/info` 404; `/health` 200 |
| Ollama on GPU | Was **CPU-only** — container had lost NVML ("Unknown Error"); fixed by `docker restart ollama`. Then was pinned to **GPU 0 only** (`device_ids:['0']`) — changed to `['0','1']` in `/opt/inference/docker-compose.yml` (backed up) so it uses both cards |
| `/no_think` makes Qwen return content | `qwen3.5-9b-128k` is a **thinking** model — returns empty `content`; reasoning is in a separate channel. Adapter must handle. **gemma4:e4b returns clean `content`** |

## Model routing (current decision)
| Role | Model | Placement | Notes |
|---|---|---|---|
| Fast/frequent: narrator, attention, affect, perception, **vision percepts** | **`gemma4:e4b`** | GPU-resident (100% GPU after the both-GPU fix; ~12 GB spread across both cards) | multimodal + tool-calling + clean content. The local workhorse |
| Heavier local: deliberation fallback, consolidation, long context | **`qwen3.5-9b-128k`** | on-demand (~38 s cold load; won't stay pinned alongside gemma4 — only ~6.5 GB free) | thinking-model output quirk; 128k ctx (KV cache q8_0) |
| Heavy reasoning (default): deliberation, metacognition, self-model | **Groq `llama-3.3-70b-versatile`** | cloud | key verified; budget-capped (`GROQ_DAILY_BUDGET_USD`) → "tired" degradation to local |
| Embeddings (all memory) | **bge-m3** via `:8002 /embed` | — | 1024-d |

**VRAM note:** gemma4 (~12 GB) + the box's other services leave too little to also pin qwen3.5-9b resident. So: gemma4 stays warm; qwen loads on demand; **heavy reasoning prefers Groq** (matches the SPEC's two-provider design anyway).

## Endpoints (all `http://inference.lan:<port>`)
| Port | Service | Use | Phase |
|---|---|---|---|
| 8000 | Ollama (OpenAI-compatible `/v1/...`) | LLM + vision | 0 |
| 8002 | embeddings (Flask, `/embed`) | memory embeddings | 1 |
| 8003 | YOLO11 | object detection | 2+ |
| 8006 | OmniParser | UI/screen parsing | later |
| 8004 | OCR | text-in-images | later |
| 8880 | **Kokoro TTS** (`/v1/audio/speech`, 26 voices) | voice out | 7 |
| 8890 | **Speaches STT** (`/v1/audio/transcriptions`) | voice in | 7 |
| 8889 | SearXNG | web/news search (no API key) | 6 |
| 6333 | Qdrant | optional alt vector store | (memory alt) |

## Status of verification
- [x] Ollama on GPU (both cards), `gemma4:e4b` text + **vision** working
- [x] `qwen3.5-9b-128k` loads on GPU (empty-content quirk noted)
- [x] Embeddings `/embed` → 1024-d vector
- [x] Groq `llama-3.3-70b-versatile` completes (key valid)
- [x] **TTS (Kokoro `:8880`)** works — synthesized valid 24 kHz WAV (~55 s on CPU; it's a CPU container)
- [x] **STT (Speaches `:8890`)** works — round-trip verified: Kokoro "Johnny Five is alive." → Speaches → `"Johnny 5 is alive."` (~22 s). The bundled `faster-whisper-large-v3-turbo` was corrupted (HTTP 500, Speaches v0.8.0 breaking change); **fixed by pulling `Systran/faster-whisper-small`** (`POST /v1/models/Systran/faster-whisper-small`) — also lighter/faster on CPU, better for real-time voice-in. Use `STT_MODEL=Systran/faster-whisper-small`.

> **Voice CPU note:** Kokoro + Speaches are CPU containers — TTS took ~55 s for one short phrase. Fine for occasional unprompted speech, but Phase 7 must treat voice latency as significant (stream/queue it; don't block the cognitive cycle on TTS/STT).
