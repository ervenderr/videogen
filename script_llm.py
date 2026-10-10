"""LLM script writer: Wikipedia text -> [{"narration", "query"}] via an OpenAI-compatible endpoint (stdlib only)."""
import json, os, re, urllib.request
from pathlib import Path

API_URL = "https://inference.dahl.global/v1/chat/completions"
MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
MAX_TOKENS = 3000  # output is ~250 tokens with reasoning off; headroom for retries
REASONING_EFFORT = "none"  # measured: 2-6 s vs 15-90 s with default reasoning
TEMPERATURE = 0.7
TIMEOUT = 150
ATTEMPTS = 3  # retries on invalid output (each call is only ~3 s now)
WIKI_CHARS = 6000
SCENES_MIN, SCENES_MAX = 4, 8
WORDS_MIN, WORDS_MAX = 12, 25
QUERY_WORDS_MIN, QUERY_WORDS_MAX = 2, 4
KEY_NAME = "DAHL_API_KEY"
ENV_FILE = Path(__file__).resolve().parent / ".env"

SYSTEM = (
    "You write narration for short vertical documentary videos. Reply with ONE strict JSON object and nothing else: "
    '{"scenes": [{"narration": "...", "query": "..."}, ...]}.\n'
    "Rules:\n"
    "- 6 scenes. Scene 1 is a punchy hook built on the most surprising fact (never start with 'This is'); the last scene is a memorable closer. Use complete, grammatical sentences.\n"
    "- Each narration is ONE sentence of 14-22 words, vivid documentary style, plain text only "
    "(no markdown, no URLs, no quotes, no em dashes, no emojis).\n"
    "- Every fact (names, numbers, dates, places) MUST come from the supplied Wikipedia text. Do not invent or "
    "add facts from memory. If unsure, leave it out.\n"
    "- Each query is a 2-4 word (never more) stock-footage search phrase showing a concrete, filmable visual for that scene "
    "(animals, places, objects, people doing things). No abstract words, no names of unfilmable concepts."
)


def api_key() -> str:
    """DAHL_API_KEY from the environment, else from .env next to this file. Empty string if absent."""
    if os.environ.get(KEY_NAME):
        return os.environ[KEY_NAME].strip()
    try:
        for line in ENV_FILE.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == KEY_NAME:
                return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


def chat(key: str, messages: list[dict]) -> str:
    """POST one chat completion; return ONLY message.content. Errors never include the key."""
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": MAX_TOKENS,
                       "temperature": TEMPERATURE, "reasoning_effort": REASONING_EFFORT}).encode()
    req = urllib.request.Request(API_URL, body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.load(r)
    choice = data["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("LLM output truncated")
    return choice["message"]["content"] or ""


def extract_json(text: str) -> dict:
    """Strip <think> blocks and code fences, then parse the outermost {...}."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"<think>.*", "", text, flags=re.S)  # unterminated block
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in LLM output")
    return json.loads(text[start:end + 1])


BAD_TEXT = re.compile(r"https?://|www\.|[*#`_\[\]<>|]|—")


def validate(data: dict) -> list[dict]:
    """Return clean scenes or raise ValueError."""
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(scenes, list) or not SCENES_MIN <= len(scenes) <= SCENES_MAX:
        raise ValueError("bad scene count")
    out = []
    for i, s in enumerate(scenes, 1):
        if not isinstance(s, dict) or not isinstance(s.get("narration"), str) or not isinstance(s.get("query"), str):
            raise ValueError("scene fields must be strings")
        narration, query = " ".join(s["narration"].split()), " ".join(s["query"].split())
        if not WORDS_MIN <= len(narration.split()) <= WORDS_MAX:
            raise ValueError(f"scene {i} narration has {len(narration.split())} words, need {WORDS_MIN}-{WORDS_MAX}")
        query = " ".join(query.split()[:QUERY_WORDS_MAX])  # repair: keep the first 4 words
        if len(query.split()) < QUERY_WORDS_MIN:
            raise ValueError("query too short")
        if BAD_TEXT.search(narration) or BAD_TEXT.search(query):
            bad = sorted({m.group() for t in (narration, query) for m in BAD_TEXT.finditer(t)})
            raise ValueError(f"scene {i} has forbidden characters {bad}; reword without them")
        out.append({"narration": narration, "query": query})
    return out


def llm_script(topic: str, wiki_text: str) -> list[dict]:
    """Scenes [{"narration", "query"}] grounded in wiki_text. Raises on any failure."""
    key = api_key()
    if not key:
        raise RuntimeError("no API key")
    user = f"Topic: {topic}\n\nWikipedia text (the ONLY allowed source of facts):\n\"\"\"\n{wiki_text[:WIKI_CHARS]}\n\"\"\""
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    last: Exception = ValueError("no attempts")
    for _ in range(ATTEMPTS):
        raw = ""
        try:
            raw = chat(key, messages)
            return validate(extract_json(raw))
        except (ValueError, KeyError, IndexError, TypeError) as e:  # invalid output: retry once, telling the model why
            last = e
            if raw:
                messages = messages + [{"role": "assistant", "content": raw},
                                       {"role": "user", "content": f"Invalid: {e}. Reread the rules and reply with corrected JSON only."}]
    raise last
