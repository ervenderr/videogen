#!/usr/bin/env python3
"""topic -> mp4. No API keys: Wikipedia (script) + Wikimedia Commons (images)
+ edge-tts neural voiceover (macOS `say` fallback) + Pillow (captions) + ffmpeg (Ken Burns, mix, music)."""
import asyncio, concurrent.futures as cf, html, os, json, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
UA = {"User-Agent": "videogen-prototype/0.1 (local script)"}
VOICE = "en-US-GuyNeural"  # edge-tts neural voice
RATE = "-5%"  # slightly slower, documentary pace
TTS_RETRIES = 2
SAY_VOICE = "Samantha"  # fallback if edge-tts fails (or VIDEOGEN_FORCE_SAY=1)
TICKS = 10_000_000  # edge-tts offsets/durations are in 100 ns units
MAX_SENTENCES = 6
WORDS_PER_CAPTION = 3
PAD = 0.35
TRIM_TAIL = "areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse"  # drops trailing TTS silence
MUSIC_VOL = 0.05
RETRIES, BACKOFF = 3, 1.0
CC_FILTER = "EgIwAQ=="  # YouTube search filter: Creative Commons
SEARCH_RESULTS = 15
MIN_DURATION, MAX_DURATION = 12, 900
CLIP_START, CLIP_LEN = 0.3, 10  # window starts at 30% of the video, 10 s long
SENTENCE_CANDS, TITLE_CANDS, IMAGE_CANDS = 4, 6, 12  # candidates per sentence search / title search / Commons images
WORKERS = 4  # parallel downloads
FRAMES_PER_CLIP = 3
MIN_SIM = 0.24  # min CLIP cosine(frames, text), averaged over frames and the two text variants;
# junk (text cards, screen recordings, wrong topic) scored 0.10-0.23, relevant clips/images 0.24-0.35 for a candidate to be accepted
CLIP_MODEL = "clip-ViT-B-32"
KEYWORDS_PER_SENTENCE = 2
PIXABAY_API = "https://pixabay.com/api/videos/"
PIXABAY_PER_PAGE = 10  # hits per search
PIXABAY_SENTENCE_CANDS, PIXABAY_TITLE_CANDS = 3, 5  # candidates kept per sentence search / title search
PIXABAY_MIN_DURATION = 8  # seconds; shorter hits can't fill a segment
PIXABAY_RENDITIONS = ("small", "tiny")  # preferred -> fallback (small is ~1280x720)
ENV_FILE = Path(__file__).resolve().parent / ".env"
BAD_TITLE = re.compile(r"tutorial|explained|lecture|slideshow|how to|review|presentation|infographic|facts|description|documentary|chapter|audiobook|class|course|lesson", re.I)
GENERIC = {"history", "list", "the", "and"}  # too vague to require in a video title
STOPWORDS = set("""about after also another because been before being between both called could during each early
first from have however into known largest later like made many more most much named other over several since some
such than that their them then there these they this those through under until used using very was were what when
where which while with within would""".split())
CAPTION_SIZE = 88
CAPTION_MARGIN = 40
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def fetch(req: urllib.request.Request, timeout: int = 20) -> bytes:
    """GET with short backoff on HTTP 429/5xx."""
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == RETRIES - 1:
                raise
            time.sleep(BACKOFF * 2 ** attempt)


def api_json(host: str, params: dict) -> dict:
    url = f"https://{host}/w/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    return json.loads(fetch(urllib.request.Request(url, headers=UA)))


def get_json(params: dict) -> dict:
    return api_json("en.wikipedia.org", params)


def commons_json(params: dict) -> dict:
    return api_json("commons.wikimedia.org", params)


SCENE_QUERIES: dict[str, str] = {}  # narration -> LLM stock-footage query (filled by write_script)


def write_script(topic: str) -> tuple[str, list[str]]:
    hits = get_json({"action": "query", "list": "search", "srsearch": topic, "srlimit": 1})["query"]["search"]
    if not hits:
        sys.exit(f"No Wikipedia article for {topic!r}")
    title = hits[0]["title"]
    pages = get_json({"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "titles": title})["query"]["pages"]
    text = next(iter(pages.values()))["extract"]
    text = re.sub(r"\([^)]*\)", "", text)  # drop parentheticals (pronunciations, dates)
    if not os.environ.get("VIDEOGEN_NO_LLM"):
        try:
            from script_llm import llm_script
            scenes = llm_script(title, text)
            SCENE_QUERIES.update({s["narration"]: s["query"] for s in scenes})
            return title, [s["narration"] for s in scenes]
        except Exception as e:
            print(f"    LLM script failed ({type(e).__name__}), using Wikipedia intro")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if 25 < len(s.strip()) < 220]
    return title, sentences[:MAX_SENTENCES]


def clean_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def find_images(title: str) -> list[dict]:
    """Commons image candidates (not downloaded yet): {thumb, credit}."""
    data = commons_json({
        "action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {title}",
        "gsrnamespace": 6, "gsrlimit": 30, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1280,
    })
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png") or info.get("width", 0) < 900:
            continue
        meta = info.get("extmetadata", {})
        who = clean_html(meta.get("Artist", {}).get("value", "")) or "unknown author"
        lic = clean_html(meta.get("LicenseShortName", {}).get("value", "")) or "unknown licence"
        out.append({"thumb": info["thumburl"], "credit": f"{p['title']} - {info.get('descriptionurl', '')} - {who} ({lic})"})
    return out


def fetch_image(c: dict, path: Path) -> bool:
    try:
        path.write_bytes(fetch(urllib.request.Request(c["thumb"], headers=UA), 30))
        Image.open(path).verify()
        return True
    except Exception as e:
        print(f"    skip image {c['credit'].split(' - ')[0]}: {e}", file=sys.stderr)
        return False


def ytdlp(args: list) -> subprocess.CompletedProcess:
    ytdlp_bin = Path(sys.executable).parent / "yt-dlp"
    return subprocess.run([str(ytdlp_bin), "--js-runtimes", "node", "-q", "--no-warnings", *map(str, args)],
                          capture_output=True, text=True)


def reason(r: subprocess.CompletedProcess) -> str:
    lines = (r.stderr or "").strip().splitlines()
    return lines[-1][:160] if lines else f"exit {r.returncode}"


def keywords(sentence: str, title: str) -> str:
    """1-2 longest non-stopword words of the sentence that aren't already in the title."""
    if sentence in SCENE_QUERIES:
        return SCENE_QUERIES[sentence]
    skip = {w.lower() for w in re.findall(r"[A-Za-z]+", title)}
    words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", sentence)} - STOPWORDS - skip
    return " ".join(sorted(words, key=lambda w: (-len(w), w))[:KEYWORDS_PER_SENTENCE])


def search_clips(query: str, must: str) -> list[dict]:
    """CC-licensed YouTube search results that pass the duration/title filters."""
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": query, "sp": CC_FILTER})
    r = ytdlp([url, "--flat-playlist", "--playlist-end", SEARCH_RESULTS,
               "--print", "%(id)s\t%(duration)s\t%(uploader,channel)s\t%(title)s"])
    if r.returncode and not r.stdout.strip():
        print(f"    yt-dlp search failed for {query!r}: {reason(r)}", file=sys.stderr)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4 or not parts[1].replace(".", "").isdigit():
            continue
        vid, dur, who, name = parts[0], float(parts[1]), parts[2], parts[3]
        if MIN_DURATION <= dur < MAX_DURATION and not BAD_TITLE.search(name) and must in name.lower():
            out.append({"id": vid, "duration": dur, "uploader": who, "title": name})
    return out


def download_clip(c: dict, path: Path) -> bool:
    """Download a CLIP_LEN window from ~30% into the video (skips intros/slates)."""
    start = int(c["duration"] * CLIP_START)
    r = ytdlp([f"https://youtu.be/{c['id']}", "-f", "bv*[height<=720][ext=mp4]/b[height<=720]",
               "--download-sections", f"*{start}-{start + CLIP_LEN}", "--force-keyframes-at-cuts",
               "-o", str(path.with_suffix(".%(ext)s"))])
    if r.returncode or not path.exists():
        print(f"    clip {c['id']} failed: {reason(r)}", file=sys.stderr)
        return False
    return True


_model = None


def clip_model():
    """Lazy-load the local CLIP model once (mps on Apple Silicon, else cpu)."""
    global _model
    if _model is None:
        import torch
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(CLIP_MODEL, device="mps" if torch.backends.mps.is_available() else "cpu")
    return _model


def embed(items: list):
    """L2-normalised CLIP embeddings for a list of strings or PIL images."""
    return clip_model().encode(items, convert_to_tensor=True, normalize_embeddings=True, show_progress_bar=False)


def clip_frames(path: Path) -> list[Image.Image]:
    """FRAMES_PER_CLIP evenly spread frames from a downloaded clip."""
    length = duration(path)
    frames = []
    for k in range(FRAMES_PER_CLIP):
        png = path.with_suffix(f".f{k}.png")
        t = length * (k + 0.5) / FRAMES_PER_CLIP
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=-2:360", str(png)])
        if png.exists():
            frames.append(Image.open(png).convert("RGB"))
    return frames


def build_candidates(title: str, sentences: list[str], images: list[dict], must: str, work: Path) -> list[dict]:
    """Download + embed a pool of clips (per-sentence and title searches) and Commons images.
    Each candidate: {kind, path, credit, emb (frames x dim)}."""
    queries = [f"{title} footage"] + [f"{title} {keywords(s, title)}".strip() for s in sentences]
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        pools = list(ex.map(lambda q: search_clips(q, must), queries))
    picked: dict[str, dict] = {}
    for pool, n in zip(pools, [TITLE_CANDS] + [SENTENCE_CANDS] * len(sentences)):
        for c in pool[:n]:
            picked.setdefault(c["id"], c)
    print(f"    {len(picked)} clip candidates, downloading...")

    def get_clip(c: dict) -> dict | None:
        path = work / f"cand_{c['id']}.mp4"
        if not download_clip(c, path):
            return None
        frames = clip_frames(path)
        if not frames:
            return None
        return {"kind": "clip", "path": path, "frames": frames, "credit": f"https://youtu.be/{c['id']} - {c['uploader']} - {c['title']} (CC-BY)"}

    def get_image(ic: tuple[int, dict]) -> dict | None:
        path = work / f"img{ic[0]}.jpg"
        if not fetch_image(ic[1], path):
            return None
        return {"kind": "image", "path": path, "frames": [Image.open(path).convert("RGB")], "credit": ic[1]["credit"]}

    with cf.ThreadPoolExecutor(WORKERS) as ex:
        cands = [c for c in ex.map(get_clip, picked.values()) if c]
    cands += [c for c in map(get_image, enumerate(images[:IMAGE_CANDS])) if c]  # sequential: Commons rate-limits (429)
    for c in cands:
        c["emb"] = embed(c.pop("frames"))
    return cands


def pixabay_key() -> str:
    """PIXABAY_API_KEY from the environment (even if empty), else from .env next to this script."""
    if "PIXABAY_API_KEY" in os.environ:
        return os.environ["PIXABAY_API_KEY"].strip()
    try:
        for line in ENV_FILE.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == "PIXABAY_API_KEY":
                return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


def pixabay_search(key: str, query: str, seen: set, limit: int) -> list[dict]:
    """Up to `limit` unseen, long-enough hits: {id, user, page, url}. Errors are masked, never raised."""
    qs = urllib.parse.urlencode({"key": key, "q": query[:100], "per_page": PIXABAY_PER_PAGE, "safesearch": "true"})
    try:
        hits = json.loads(fetch(urllib.request.Request(f"{PIXABAY_API}?{qs}", headers=UA)))["hits"]
    except Exception as e:
        print(f"    pixabay search failed for {query!r}: {type(e).__name__}: {str(e).replace(key, '***')}"[:200], file=sys.stderr)
        return []
    out = []
    for h in hits:
        rend = next((h["videos"][r] for r in PIXABAY_RENDITIONS if h.get("videos", {}).get(r, {}).get("url")), None)
        if rend and h.get("duration", 0) >= PIXABAY_MIN_DURATION and h["id"] not in seen:
            seen.add(h["id"])
            out.append({"id": h["id"], "user": h.get("user", "unknown"), "page": h.get("pageURL") or f"https://pixabay.com/videos/id-{h['id']}/",
                        "url": rend["url"], "duration": h["duration"]})
        if len(out) == limit:
            break
    return out


def pixabay_clip(h: dict, work: Path) -> dict | None:
    """Download the rendition and cut the same CLIP_LEN window (from CLIP_START) as YouTube clips."""
    src, path = work / f"pix_{h['id']}_src.mp4", work / f"pix_{h['id']}.mp4"
    try:
        src.write_bytes(fetch(urllib.request.Request(h["url"], headers=UA), 60))
        start = max(0.0, min(h["duration"] * CLIP_START, h["duration"] - CLIP_LEN))
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", str(CLIP_LEN), "-i", str(src), "-an",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(path)], capture_output=True, text=True)
        if r.returncode or not path.exists():
            raise RuntimeError(r.stderr.strip()[-120:] or "ffmpeg failed")
        frames = clip_frames(path)
    except Exception as e:
        print(f"    pixabay clip {h['id']} failed: {type(e).__name__}: {e}"[:200], file=sys.stderr)
        return None
    return {"kind": "pixabay", "path": path, "frames": frames, "credit": f"{h['page']} - {h['user']} (Pixabay Content License)"} if frames else None


def pixabay_candidates(key: str, title: str, sentences: list[str], missing: list[int], work: Path) -> list[dict]:
    """Search (1 per missing sentence + 1 title query) and download Pixabay clips, embedded like the others."""
    seen: set = set()
    jobs = [(f"{title} {keywords(sentences[i], title)}".strip(), PIXABAY_SENTENCE_CANDS) for i in missing] + [(title, PIXABAY_TITLE_CANDS)]
    hits = [h for q, n in jobs for h in pixabay_search(key, q, seen, n)]
    print(f"    pixabay: {len(jobs)} searches, {len(hits)} candidates, downloading...")
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        cands = [c for c in ex.map(lambda h: pixabay_clip(h, work), hits) if c]
    for c in cands:
        c["emb"] = embed(c.pop("frames"))
    return cands


def score_matrix(cands: list[dict], title: str, sentences: list[str]) -> list[list[float]]:
    """sims[sentence][candidate]: cosine of frames with "<title>: <sentence>" and with the plain
    sentence, averaged over frames and both texts (the title text anchors the subject, the plain one
    adds sentence-specific signal)."""
    t = (embed([f"{title}: {s}" for s in sentences]) + embed(sentences)) / 2
    return [[float((c["emb"] @ t[i]).mean()) for c in cands] for i in range(len(sentences))]


def greedy_pass(sims: list[list[float]], cands: list[dict], kinds: tuple[str, ...], floor: float,
                chosen: dict[int, tuple[int, float]], used: set[int]) -> None:
    """Globally greedy: best (sentence, candidate of `kinds`) pairs >= floor first; mutates chosen/used."""
    pairs = sorted(((sims[i][j], i, j) for i in range(len(sims)) for j, c in enumerate(cands) if c["kind"] in kinds), reverse=True)
    for sim, i, j in pairs:
        if sim >= floor and i not in chosen and j not in used:
            chosen[i] = (j, sim)
            used.add(j)


def uncovered(sims: list[list[float]], cands: list[dict]) -> list[int]:
    """Sentences left without a passing clip after the YouTube pass."""
    chosen: dict[int, tuple[int, float]] = {}
    greedy_pass(sims, cands, ("clip",), MIN_SIM, chosen, set())
    return [i for i in range(len(sims)) if i not in chosen]


def assign(sims: list[list[float]], cands: list[dict]) -> list[tuple[int, float]]:
    """Pick one unused candidate per sentence: passing YouTube clips, then passing Pixabay clips,
    then passing images, then best available (with a warning)."""
    n = len(sims)
    chosen: dict[int, tuple[int, float]] = {}
    used: set[int] = set()
    for kinds in (("clip",), ("pixabay",), ("image",)):
        greedy_pass(sims, cands, kinds, MIN_SIM, chosen, used)
    for i in range(n):
        if i not in chosen:
            greedy_pick = max((j for j in range(len(cands)) if j not in used), key=lambda j: sims[i][j], default=None)
            if greedy_pick is None:
                sys.exit("No usable clips or images found")
            chosen[i] = (greedy_pick, sims[i][greedy_pick])
            used.add(greedy_pick)
            print(f"    WARNING: sentence {i + 1} best match below threshold ({sims[i][greedy_pick]:.3f} < {MIN_SIM})")
    return [chosen[i] for i in range(n)]


def run(cmd: list) -> str:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"FAILED: {' '.join(map(str, cmd))[:200]}\n{r.stderr[-1500:]}")
    return r.stdout


def duration(path: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]))


def caption_png(text: str, path: Path) -> None:
    img = Image.new("RGBA", (W, 260), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = CAPTION_SIZE
    while True:  # shrink until the caption fits inside the frame
        font = ImageFont.truetype(FONT, size)
        box = d.textbbox((0, 0), text, font=font, stroke_width=8)
        if box[2] <= W - 2 * CAPTION_MARGIN or size <= 40:
            break
        size -= 4
    d.text(((W - box[2]) / 2, (260 - box[3]) / 2), text, font=font, fill="white", stroke_width=8, stroke_fill="black")
    img.save(path)


def caption_chunks(sentence: str, total: float) -> list[tuple[str, float, float]]:
    words = sentence.split()
    chunks = [" ".join(words[i:i + WORDS_PER_CAPTION]) for i in range(0, len(words), WORDS_PER_CAPTION)]
    weight = sum(len(c) for c in chunks)
    t, out = 0.0, []
    for c in chunks:
        d = total * len(c) / weight
        out.append((c, t, t + d))
        t += d
    return out


def edge_speech(text: str, mp3: Path) -> list[tuple[str, float, float]]:
    """Synthesize to mp3 with edge-tts; return [(word, start_s, end_s)] from WordBoundary events."""
    import edge_tts

    async def go() -> list[tuple[str, float, float]]:
        words, audio = [], bytearray()
        async for ch in edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary").stream():
            if ch["type"] == "audio":
                audio.extend(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["text"], ch["offset"] / TICKS, (ch["offset"] + ch["duration"]) / TICKS))
        if not audio or not words:
            raise RuntimeError("edge-tts returned no audio/word boundaries")
        mp3.write_bytes(bytes(audio))
        return words

    return asyncio.run(go())


def timed_chunks(sentence: str, words: list[tuple[str, float, float]]) -> list[tuple[str, float, float]]:
    """Caption chunks of WORDS_PER_CAPTION words timed from real word boundaries. Each chunk
    stays up until the next one starts; shown text is the sentence's own words when counts match."""
    shown = sentence.split()
    labels = shown if len(shown) == len(words) else [w[0] for w in words]
    out = []
    for i in range(0, len(words), WORDS_PER_CAPTION):
        grp = words[i:i + WORDS_PER_CAPTION]
        end = words[i + WORDS_PER_CAPTION][1] if i + WORDS_PER_CAPTION < len(words) else grp[-1][2]
        out.append((" ".join(labels[i:i + WORDS_PER_CAPTION]), grp[0][1], end))
    return out


def say_speech(text: str, wav: Path, work: Path, i: int) -> None:
    aiff = work / f"v{i}.aiff"
    run(["say", "-v", SAY_VOICE, "-o", aiff, text])
    run(["ffmpeg", "-y", "-i", aiff, "-af", TRIM_TAIL, "-ar", 44100, "-ac", 2, wav])


def build_segment(i: int, sentence: str, media: Path, work: Path) -> Path:
    wav, seg = work / f"v{i}.wav", work / f"seg{i}.mp4"
    sentence = re.sub(r"\s+([,.;:!?])", r"\1", re.sub(r"\s+", " ", sentence)).strip()  # neural TTS: no double spaces / " ,"
    words = None
    if not os.environ.get("VIDEOGEN_FORCE_SAY"):
        for attempt in range(1 + TTS_RETRIES):
            try:
                mp3 = work / f"v{i}.mp3"
                words = edge_speech(sentence, mp3)
                run(["ffmpeg", "-y", "-i", mp3, "-af", TRIM_TAIL, "-ar", 44100, "-ac", 2, wav])
                break
            except Exception as e:
                last = f"{type(e).__name__}: {e}"[:160]
                time.sleep(BACKOFF)
        else:
            print(f"    WARNING: edge-tts failed for sentence {i + 1} ({last}); using say")
    if words is None:
        say_speech(sentence, wav, work, i)
    speech = duration(wav)
    total = speech + PAD
    frames = int(total * FPS) + 1
    chunks = timed_chunks(sentence, words) if words else caption_chunks(sentence, speech)
    is_video = media.suffix == ".mp4"
    cmd = ["ffmpeg", "-y", *(["-stream_loop", -1] if is_video else ["-loop", 1]), "-i", media, "-i", wav]
    for j, (text, _, _) in enumerate(chunks):
        png = work / f"cap{i}_{j}.png"
        caption_png(text, png)
        cmd += ["-i", png]
    zoom_dir = "min(zoom+0.0007,1.25)" if i % 2 == 0 else "if(eq(on,1),1.25,max(zoom-0.0007,1.0))"
    if is_video:
        f = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1[v0]"
    else:
        f = (f"[0:v]scale=1400:2488:force_original_aspect_ratio=increase,crop=1400:2488,"
             f"zoompan=z='{zoom_dir}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}[v0]")
    for j, (_, a, b) in enumerate(chunks):
        f += f";[v{j}][{j + 2}:v]overlay=0:1250:enable='between(t,{a:.2f},{b:.2f})'[v{j + 1}]"
    cmd += ["-filter_complex", f, "-map", f"[v{len(chunks)}]", "-map", "1:a", "-af", f"apad=pad_dur={PAD}",
            "-t", f"{total:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", FPS, "-c:a", "aac", seg]
    run(cmd)
    return seg


def music(length: float, path: Path) -> None:
    chord = [220.0, 277.18, 329.63, 440.0]
    inputs = []
    for f in chord:
        inputs += ["-f", "lavfi", "-i", f"sine=f={f}:d={length:.1f}"]
    run(["ffmpeg", "-y", *inputs, "-filter_complex",
         f"amix=inputs={len(chord)},tremolo=f=0.3:d=0.6,lowpass=f=1200,afade=t=in:d=2,afade=t=out:st={length - 2:.1f}:d=2",
         path])


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: videogen.py 'topic' [out.mp4]")
    topic = sys.argv[1]
    out = Path(sys.argv[2] if len(sys.argv) > 2 else re.sub(r"\W+", "_", topic).lower() + ".mp4").resolve()
    work = Path(tempfile.mkdtemp(prefix="videogen_"))
    print("1/5 script...")
    title, sentences = write_script(topic)
    print(f"    {title}: {len(sentences)} sentences")
    credits = [f"Script: Wikipedia - {title}"]
    print("2/5 images...")
    images = find_images(title)
    print(f"    {len(images)} image candidates")
    must = max((w for w in re.findall(r"[A-Za-z]{3,}", title) if w.lower() not in GENERIC), key=len, default="").lower()  # relevance: video title must mention it
    cands = build_candidates(title, sentences, images, must, work)
    sims = score_matrix(cands, title, sentences)
    key = pixabay_key()
    if key:  # fallback source: only for sentences no YouTube clip covers
        missing = uncovered(sims, cands)
        if missing:
            print(f"    {len(missing)} sentence(s) without a passing YouTube clip; trying Pixabay...")
            cands += pixabay_candidates(key, title, sentences, missing, work)
            sims = score_matrix(cands, title, sentences)
        else:
            print("    pixabay: 0 searches")
    media = []
    for i, (j, sim) in enumerate(assign(sims, cands)):
        c = cands[j]
        print(f"    sentence {i + 1}: {c['kind']} sim={sim:.3f} {c['credit'][:90]}")
        media.append(c["path"])
        credits.append(c["credit"])
    print("3/5 voice + captions + motion...")
    segs = [build_segment(i, s, media[i], work) for i, s in enumerate(sentences)]
    t0 = 0.0
    for i, seg in enumerate(segs):
        d = duration(seg)
        print(f"    segment {i + 1}: {t0:.2f}-{t0 + d:.2f}s")
        t0 += d
    print("4/5 concat...")
    listing = work / "list.txt"
    listing.write_text("".join(f"file '{s}'\n" for s in segs))
    joined = work / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", 0, "-i", listing, "-c", "copy", joined])
    total = duration(joined)
    print("5/5 music mix...")
    bed = work / "bed.wav"
    music(total, bed)
    run(["ffmpeg", "-y", "-i", joined, "-i", bed, "-filter_complex",
         f"[1:a]volume={MUSIC_VOL}[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", out])
    out.with_suffix(".credits.txt").write_text("\n".join(credits) + "\n")
    shutil.rmtree(work, ignore_errors=True)  # kept on failure (run() exits earlier)
    print(f"done: {out} ({total:.1f}s)")


if __name__ == "__main__":
    main()
