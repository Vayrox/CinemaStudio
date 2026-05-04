# CinemaStudio

AI short-film pipeline. Logline → screenplay → shot list → moodboards → Seedance 2.0 clips, delivered as labeled MP4s ready for manual editing in DaVinci/Premiere/Final Cut.

## Quickstart

```bash
pip install -e .
cinema setup            # paste your ai-auto.io API key (saved locally, gitignored)
cinema setup-anthropic  # paste your Anthropic key for script generation
cinema make "A lonely lighthouse keeper discovers a glowing creature in the surf at dawn." \
    --ratio 9:16 --minutes 1 --slug lighthouse
```

Output lands in `projects/<slug>/clips/` as labeled MP4s plus an EDL CSV you can import into your editor.

## Pipeline

1. **Script** — Claude expands logline into a screenplay and shot list (JSON).
2. **Character bible** — `gtv_flux_2_pro` generates locked reference portraits.
3. **Moodboards** — `gpt-image-2` generates a 4-panel keyframe sheet per scene.
4. **Per-shot keyframes** — `gtv_seedream_4_5` regenerates each panel with the character ref locked in.
5. **Animate** — Seedance 2.0 image-to-video, 15s each, 4K, 16:9 or 9:16.
6. **Deliver** — clips named `shot_001_<scene>_<desc>.mp4` + `edl.csv` + `script.json`.

## Concurrency

VIP+ plan: 3 parallel Seedance jobs, 4 parallel image jobs. The tool queues the rest with backoff.

## Commands

| Command | What it does |
|---|---|
| `cinema setup` | First-run prompt for ai-auto.io API key |
| `cinema script "<logline>" --slug NAME` | Generate screenplay + shot list only |
| `cinema moodboard --slug NAME` | Generate moodboards from existing shot list |
| `cinema generate --slug NAME` | Animate shots into clips |
| `cinema make "<logline>" --slug NAME` | Run the full pipeline |

## Caveats

- Seedance 2.0 caps at 15s per clip and natively supports 9:16 / 16:9 / 1:1 / 4:3 / 3:4 (no 21:9). For cinematic 21:9 output, generate at 16:9 4K and crop in your editor.
- "Hyperrealistic, indistinguishable from real life" is not achievable today. Output is cinematic AI video — close, but a careful viewer can still spot artifacts (hands, micro-expressions, complex physics).
- Audio (music, SFX, voiceover) is left to you in post.
