# CinemaStudio

AI short-film pipeline. Logline → screenplay → shot list → moodboards → Seedance 2.0 clips, delivered as labeled MP4s ready for manual editing in DaVinci/Premiere/Final Cut.

## Quickstart — Web UI (recommended)

```bash
pip install -e .
cinema web              # open http://127.0.0.1:7878
```

The browser UI walks you through:
1. Pasting your API keys (saved locally to `cinemastudio/config.py`, gitignored).
2. Creating a film from a logline with the visual settings you want.
3. Running each pipeline step (script → moodboards → animate) or the whole thing in one click.
4. Watching a live log of progress, then browsing the generated screenplay, moodboards, keyframes, and rendered clips inline. Download the EDL CSV when you're ready to edit.

### Script generator: Google AI Studio (free) or Anthropic

Pick a provider on the Settings page:

* **Google AI Studio (default)** — grab a free key at <https://aistudio.google.com/>, paste it into Settings, done. Uses **Gemini 2.5 Pro with extended thinking** (the highest-quality Gemini model, allowed to reason for as long as it needs before writing).
* **Anthropic** — paste a Claude key from <https://console.anthropic.com/>. Uses Claude Sonnet 4.6.

Either provider produces the same screenplay JSON; the rest of the pipeline (images + video) always goes through ai-auto.io.

## Quickstart — CLI

```bash
pip install -e .
cinema setup            # paste your ai-auto.io and Anthropic API keys
cinema make "A lonely lighthouse keeper discovers a glowing creature in the surf at dawn." \
    --ratio 9:16 --minutes 1 --slug lighthouse
```

Output lands in `projects/<slug>/clips/` as labeled MP4s plus an EDL CSV you can import into your editor.

## Idea generator + character library + cast workflow

* **Idea generator** — on the *New film* page, click **"Need ideas?"**, type a vague theme, and Gemini returns 10 polished loglines. Click one to autofill.
* **Cast workflow** — after the screenplay is generated, the project's *Characters* tab shows every character with the count of shots they appear in (the most-frequent one gets a **lead** badge). Rename inline and the new name is rewritten across every scene, shot, description, motion prompt, and keyframe prompt in `screenplay.json`. Click *Replace from library* to swap a character for one you've already designed — name and 3-panel sheet both copy over.
* **Cross-project library** at `/library` — reusable characters with **3-panel reference sheets** (front · back · face close-up on white). Three ways to add one:
  - Manual: name + description, then click *Generate sheet* to render via Nano Banana Pro.
  - Upload: drag in a real photo to use as the sheet.
  - **From image URL** — paste a Pinterest pin URL or any direct image link. Pick *use as-is* (free, the photo becomes the sheet) or *generate sheet from this reference* (sends the photo to Nano Banana Pro and produces a proper 3-panel turnaround locked to it).
* **Pipeline wiring** — every shot's keyframe is generated with that shot's character sheets passed as `reference_images` to the image model (max 2 per shot). Same face, hair, wardrobe across every shot in your film.

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
| `cinema web` | Launch the web UI (default http://127.0.0.1:7878) |
| `cinema setup` | First-run prompt for API keys |
| `cinema script "<logline>" --slug NAME` | Generate screenplay + shot list only |
| `cinema moodboard --slug NAME` | Generate moodboards from existing shot list |
| `cinema generate --slug NAME` | Animate shots into clips |
| `cinema make "<logline>" --slug NAME` | Run the full pipeline |
| `cinema quota` | Show your ai-auto.io quota / plan info |

## Caveats

- Seedance 2.0 caps at 15s per clip and natively supports 9:16 / 16:9 / 1:1 / 4:3 / 3:4 (no 21:9). For cinematic 21:9 output, generate at 16:9 4K and crop in your editor.
- "Hyperrealistic, indistinguishable from real life" is not achievable today. Output is cinematic AI video — close, but a careful viewer can still spot artifacts (hands, micro-expressions, complex physics).
- Audio (music, SFX, voiceover) is left to you in post.
