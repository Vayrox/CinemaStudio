# CinemaStudio

AI short-film pipeline. Logline → screenplay → shot list → moodboards → Seedance 2.0 clips, delivered as labeled MP4s ready for manual editing in DaVinci/Premiere/Final Cut.

## Quickstart (web UI)

```bash
pip install -e .
cinema serve
```

This launches the web UI at `http://127.0.0.1:7777`. On first visit it asks you to paste your **ai-auto.io** and **Anthropic** keys (saved locally to `cinemastudio/config.py`, which is gitignored). From there you create projects, watch live progress logs, preview keyframes/clips inline, and regenerate any single shot with one click.

## CLI alternative

```bash
cinema setup
cinema make "A lonely lighthouse keeper discovers a glowing creature in the surf at dawn." \
    --ratio 9:16 --minutes 1 --slug lighthouse
```

Output lands in `projects/<slug>/clips/` as labeled MP4s plus an EDL CSV you can import into your editor.

Native audio (ambient, dialogue, music cues) is emitted by Seedance 2.0 directly from the `audio_prompt` baked into each shot. Toggle via `ENABLE_NATIVE_AUDIO` in `cinemastudio/config.py`.

## Pipeline

1. **Script** — Claude expands logline into a screenplay and shot list (JSON).
2. **Character bible** — `nano_banana_pro` generates locked reference portraits.
3. **Moodboards** — `nano_banana_pro` generates a multi-panel keyframe sheet per scene (4K).
4. **Per-shot keyframes** — `nano_banana_pro` renders each shot's still at 4K, photorealistic.
5. **Animate** — Seedance 2.0 image-to-video, 15s each, 4K, 16:9 or 9:16.
6. **Deliver** — clips named `shot_001_<scene>_<desc>.mp4` + `edl.csv` + `script.json`.

## Concurrency

VIP+ plan: 3 parallel Seedance jobs, 4 parallel image jobs. The tool queues the rest with backoff.

## Commands

| Command | What it does |
|---|---|
| `cinema serve` | Start the web UI (recommended) |
| `cinema setup` | First-run prompt for API keys |
| `cinema script "<logline>" --slug NAME` | Generate screenplay + shot list only |
| `cinema moodboard --slug NAME` | Generate moodboards from existing shot list |
| `cinema generate --slug NAME` | Animate shots into clips |
| `cinema make "<logline>" --slug NAME` | Run the full pipeline |
| `cinema quota` | Check ai-auto.io plan + remaining quota |

## Caveats

- Seedance 2.0 caps at 15s per clip and natively supports 9:16 / 16:9 / 1:1 / 4:3 / 3:4 (no 21:9). For cinematic 21:9 output, generate at 16:9 4K and crop in your editor.
- "Hyperrealistic, indistinguishable from real life" is not achievable today. Output is cinematic AI video — close, but a careful viewer can still spot artifacts (hands, micro-expressions, complex physics).
- Audio (music, SFX, voiceover) is left to you in post.
