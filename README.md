# videogen

Give it a topic, get back a vertical (1080x1920) mp4 of about 35-55 seconds: narrated, captioned, with footage and quiet background music.

```
topic -> Wikipedia text -> LLM script (6 scenes) -> footage per scene -> voiceover -> captions -> music -> mp4
```

Prototype quality. It works end to end, but footage accuracy and script facts need a human check before publishing.

## How it works

1. **Script**: fetches the Wikipedia intro, then an LLM (DeepSeek-V4-Flash via the Dahl API) writes ~6 punchy scenes grounded in that text, each with a visual search phrase. If the LLM fails, it uses the Wikipedia sentences.
2. **Footage**: per scene, collects candidates from CC-BY YouTube clips (yt-dlp), then Pixabay video (only for scenes with no good YouTube clip), then Wikimedia Commons photos. A local CLIP model scores frames against each sentence and picks the best unused candidate.
3. **Voice + captions**: edge-tts neural voice with real word timings drives the captions. Falls back to macOS `say`.
4. **Assembly**: ffmpeg (Ken Burns on photos, crop to 9:16, generated ambient music bed). Captions are Pillow PNG overlays.

## Requirements

- Python 3, ffmpeg, Node.js (yt-dlp needs a JS runtime)
- Internet (Wikipedia, YouTube, edge-tts, LLM API)
- Optional: Postgres 16 + Node for the web UI

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create a `.env` next to `videogen.py` (git-ignored):

```
DAHL_API_KEY=...        # LLM script writer; without it the Wikipedia intro is used
PIXABAY_API_KEY=...     # optional; Pixabay fallback footage is skipped without it
```

The first run downloads the CLIP model (~600 MB).

## Use

```bash
.venv/bin/python videogen.py "Philippine eagle" out.mp4
```

Prints `1/5 ...` to `5/5 ...` progress lines, then `done: <path> (<secs>s)`. A run takes about 2 minutes. A `<out>.credits.txt` file is written next to the video.

| Env var | Effect |
|---|---|
| `VIDEOGEN_NO_LLM=1` | Skip the LLM, use the Wikipedia intro |
| `VIDEOGEN_FORCE_SAY=1` | Skip edge-tts, use macOS `say` |

Tunables (voice, rate, similarity threshold, candidate counts) are constants at the top of `videogen.py` and `script_llm.py`.

## Web UI

Next.js + Postgres job runner in `web/`: type a topic, watch progress, play the result. See `web/README.md`.

```bash
cd web
createdb videogen && npm install && npm run db:init
# set DATABASE_URL in web/.env.local
npm run build && npm start -- -p 3100   # http://localhost:3100
```

No auth, so run it locally only.

## Credits and licensing

- YouTube clips are CC-BY: credit the creators listed in `<out>.credits.txt` if you publish.
- Pixabay clips use the Pixabay Content License; Commons photos carry their own licenses (author and license are in the credits file).
- Script text comes from Wikipedia (CC BY-SA).

## Known limitations

- CLIP can't tell close species apart, so footage can show the wrong animal. Some clips have their own burned-in text under our captions.
- The LLM can drift slightly from the source text. Fact-check before publishing.
- `web/lib/config.ts` hardcodes absolute paths (`/Users/ervenderr/Projects/videogen`); edit them if you clone elsewhere.
- macOS-oriented: the `say` fallback and font path (`/System/Library/Fonts/...`) are macOS-specific.
- Untested by ear: voice and music quality.
