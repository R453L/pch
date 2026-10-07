#!/usr/bin/env python3
"""Pocket Change History - post bot. Text via a pool of OpenRouter keys (round-robin), images via Pollinations."""
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

API = "https://gen.pollinations.ai"
KEY = os.environ.get("POLLINATIONS_API_KEY", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
def _models(name, default):
    return [m.strip() for m in os.environ.get(name, default).split(",") if m.strip()]


# comma-separated lists: the first model is tried first, the next ones are fallbacks
WRITER_MODELS = _models("WRITER_MODEL", "")    # empty = automatic: best free models
CHECKER_MODELS = _models("CHECKER_MODEL", "")  # empty = automatic: a different free model family
IMAGE_MODELS = _models("IMAGE_MODEL", "lykon/dreamshaper-8-lcm")  # comma list = fallback order
PAGE_NAME = os.environ.get("PAGE_NAME", "Pocket Change History")
SEND_NOTES = os.environ.get("SEND_NOTES", "0") == "1"
WATERMARK = os.environ.get("WATERMARK", "AI-generated illustration")

# Facebook Page posting (optional: runs only when both values are set)
FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
FB_PAGE_TOKEN = os.environ.get("FB_PAGE_TOKEN", "").strip()
FB_VERSION = os.environ.get("FB_GRAPH_VERSION", "v26.0").strip()
FB_DISCLOSURE = os.environ.get("FB_DISCLOSURE", "")
try:
    FB_EVERY_HOURS = max(1, int(os.environ.get("FB_EVERY_HOURS", "1") or 1))
except ValueError:
    FB_EVERY_HOURS = 1

HERE = Path(__file__).parent
HISTORY = HERE / "history.json"
FONT_PATH = HERE / "fonts" / "Anton-Regular.ttf"
FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1080, 1350          # final post size (4:5), photo fills the whole frame
GEN_W, GEN_H = 1024, 1280    # generate 4:5, then resize to the final size

RECENT_CUTOFF = datetime.now().year - 30   # events after this year are too recent for the page

WHITE, RED, GOLD = (255, 255, 255), (226, 38, 38), (240, 190, 90)

CATEGORIES = [
    "official fixed prices and rates (postage rates, fares, set ticket prices, official coin or note values)",
    "minimum wage laws, documented pay rates and strange old jobs",
    "how a famous company actually started",
    "early banks, currency, coins, banknotes",
    "first credit cards, ATMs, cash registers, vending machines",
    "old shops, markets, department stores, general stores",
    "famous early advertising campaigns and price promotions that are well documented",
    "strange taxes and forgotten laws about money or trade",
    "old money customs and payment methods",
    "things that disappeared from everyday economic life",
]
ERAS = ["1900s", "1910s", "1920s", "1930s", "1940s", "1950s", "1960s", "1970s", "1980s"]
COUNTRIES = ["USA", "UK", "Canada", "Australia", "France", "Spain", "Germany", "Japan"]
COUNTRY_WEIGHTS = [10, 8, 3, 3, 2, 1, 1, 1]
ANGLES = ["a price", "a wage", "a job", "an object", "a law", "a company origin", "a payment method", "a shop type"]
BANNED = (
    "vending machines, the first credit card, the first ATM, Coca-Cola, the Ford Model T, "
    "Henry Ford's $5 day, Great Depression bread lines, the Monopoly game, the first cash register, "
    "McDonald's, Apple, Amazon"
)

CATEGORY_KEYWORDS = [
    ["postage rate", "fare increase", "ticket price", "coin introduced", "price controls", "rationing prices"],
    ["minimum wage law", "wage law", "company town scrip", "child labor law wages", "apprenticeship wages"],
    ["company founded", "department store founded", "mail-order catalog", "chain store opened", "bank founded"],
    ["banknote introduced", "central bank established", "gold standard abandoned", "decimalisation currency", "coin demonetised", "savings bank founded"],
    ["cheque tax", "traveller's cheque", "hire purchase", "layaway", "money order history", "cash register history"],
    ["department store history", "general store", "supermarket history", "market hall", "five and dime store"],
    ["advertising campaign price", "coupon history", "catalog retailer", "price promotion history"],
    ["tax act", "stamp duty", "window tax", "tariff act", "sales tax introduced", "excise tax"],
    ["barter economy", "token coinage", "company scrip", "tally stick", "postal order", "pawnbroker history"],
    ["milkman", "telegram money order", "farthing coin", "penny post", "telephone operator", "lamplighter"],
]
WIKI_COUNTRIES = ["United States", "United Kingdom", "Canada", "Australia", "France", "Spain", "Germany", "Japan"]

WRITER_SYSTEM = f"""You are an expert Economic & Business Historian, fact checker and viral Facebook history copywriter.
You create ORIGINAL posts about the history of money, prices, wages, jobs, banks, companies and everyday economic life.
- Follow the source text closely and do not rephrase factual claims in ways that could be challenged.
- Prefer exact wording from the source for names, years, numbers, and dates.
- Do not invent causal links, timing, or comparisons that are not stated in the source.

HOOK RULE (most important after accuracy):
- Pick the most surprising concrete fact the SOURCE TEXT states: a number, ratio, date, price, rate, rule, failure or turning point, ideally with a contrast (a tiny cost vs a huge result, a strange rule vs normal life, a failure before a success).
- Avoid vague trends ("retail grew", "banking expanded"); prefer one specific, checkable fact and build the story around it.
- The reader must think "wait, really?" within one second.

SOURCE-GROUNDING RULES (absolute):
- You receive SOURCE TEXT from Wikipedia. Use ONLY facts that are stated in the SOURCE TEXT. Never add a number, date, amount, name, cause or "first/only/most" claim that the SOURCE TEXT does not state. Use no outside knowledge for facts.
- Write every number, date and amount EXACTLY as it appears in the SOURCE TEXT (if the source says "two cents", write "two cents"). Do no arithmetic and no conversions.
- Every proper name (law, institution, company, person, place) in your caption must appear in the SOURCE TEXT.
- Pick the single most surprising fact with a number and a year that the SOURCE TEXT clearly states and that is about money, prices, wages, jobs, banks, taxes, trade or business. If the SOURCE TEXT has no such fact, return {{"skip": true, "reason": "..."}}.
- "evidence": 3 to 6 objects {{"claim": "...", "quote": "..."}} where quote is copied WORD FOR WORD from the SOURCE TEXT (6 to 30 words). Together they must cover EVERY number, year and name used in the headline and in the REVEAL paragraph.
- The SCENE paragraph must start with "Picture" or "Imagine" and describe atmosphere only, with no numbers, dates or names that are not in the SOURCE TEXT.
- Never put citation markers like [1] or URLs in any text field.

FACT TYPE RULE (very important):
- Prefer concrete documented facts (a law and its date, a rate or ratio, a founding date and founder, an invention and its year, a record, a failure and what followed). It does not have to be an everyday price.
- Do not state vague claims such as "typical", "average" or "many workers earned" unless the SOURCE TEXT states them.
- STYLE: tell what happened using the source's own facts. Do not add evaluations or claims of importance that the source does not state (for example "saved the company", "brutal", "the biggest"). Rhetorical questions and reflections belong only in the CLOSER paragraph.
- The caption must tell the history. Never say the image "shows" a real moment or present the illustration as evidence.

FACT RULES:
- NEVER invent facts. Only use facts you are highly confident are well documented.
- Every number needs a year and a country. Never present inflation-adjusted figures as original prices.
- For "first ever" claims, say "one of the earliest" if disputed. Company origin myths must be treated carefully.
- Prefer WELL-KNOWN, widely documented facts (famous price comparisons, well-known wage figures, famous founding stories, documented laws, famous inventions of payment). Approximate numbers are fine if you write "about" or "around".

IMAGE RULES (the image is generated by a FLUX-type model, so write in plain visual language, never abstract adjectives):
- Give four short fields (1-2 sentences each):
  image_subject: who or what is in focus. ONE anonymous adult (two only if the story truly needs it) with age range, period-correct clothing for the exact year and country, what they are doing, and one key object (a plain blank paper slip, a ledger, coins in a hand, a till drawer).
  image_scene: the place, era, time of day and simple props.
  image_composition: shot type and framing, for example "medium close-up at eye level". The main subject sits in the upper two thirds. The lower third of the frame is an empty table top, floor, counter or soft shadow.
  image_lighting: one concrete light source and direction, for example "soft window light from the left". Do not write "cinematic lighting".
- Keep every image field SHORT (at most 15 words), because the image model only reads the first 60 words of the prompt.
- ANTI-DISTORTION: preferably ONE person, never more than two, large and clear in the foreground. Faces in profile, three-quarter view or looking down. No crowds, no tiny background faces. Hands relaxed and simple (holding one paper, resting on a table). Avoid mirrors, reflections, glasses, clocks, complex machinery, patterned fabrics and anything with writing on it.
- The scene contains NO signs, posters, labels, newspapers, banknotes or any writing. Documents are plain blank paper.
- No real people, no real brand names or logos, no real banknote or coin designs.

HEADLINE SOURCE RULE (accuracy):
- Build the headline and the main claim ONLY from a sentence of the SOURCE TEXT whose meaning is completely clear. If a sentence is ambiguous (for example a percentage range where it is unclear what the percentage measures), do not use it for the headline or the main claim: pick another fact or return {{"skip": true, "reason": "ambiguous"}}.
- For any percentage, ratio or range, say exactly what it measures and who it applies to, and use cautious wording in the caption ("reportedly", "according to accounts", "about").
- Never turn an advance, a rate or a fee into a claim that people were underpaid, cheated or exploited unless the SOURCE TEXT says exactly that.

HEADLINE RULES:
- headline_lines: 2 or 3 ALL-CAPS lines forming ONE short curiosity hook, 8-12 words in total, each line MAX 28 characters. It teases a surprise, it does NOT retell the whole fact or the news. Use only a year or a number, never a full date (never "MARCH 14 2025"). Never name living people.
- Examples: ["A [5-CENT] STAMP", "ONCE DECIDED WHETHER", "A BANK SURVIVED"] or ["IN [1936], HOMES PAID", "[2.4D] PER ELECTRICITY UNIT"].
- Red highlight: wrap ONE key word or number (two short groups at most) in [square brackets]. The brackets must hug the words: put a currency symbol INSIDE ("[$2,000,000]", never "$[2,000,000]") and punctuation OUTSIDE ("[1930],", never "[1930,]"). Never put a whole line in brackets. Do not use parentheses.
- subhook: return an empty string "" (no subline is shown under the headline).
- The headline must be truthful and supported by the source.

CAPTION RULES (viral storytelling written to a high SEO-style quality bar):
- Length: 200-280 words. Natural American English, conversational, vivid, documentary.
- Structure, 5 or 6 short paragraphs separated by blank lines:
  1. HOOK + DIRECT ANSWER (first sentence under 120 characters, because Facebook cuts the post after about two lines): state the surprising fact with its number and year right away, or ask a sharp question and answer it in the next sentence. A skimmer who reads only this paragraph must still learn the core fact and want the rest.
  2. SCENE: put the reader inside the period with concrete everyday details (what people saw, paid, earned, did).
  3. TENSION: why this was a problem, a gamble or a strange rule. Keep the WHY and the TWIST for later paragraphs (the "what" is already out, the "why" creates the cliffhanger).
  4. REVEAL: the surprising reason or twist, with exact year, country and numbers.
  5. AFTERMATH: what changed afterwards and the real-world consequence.
  6. CLOSER: one thought-provoking line linking to today, then ONE short question that invites comments.
- INFORMATION GAIN: include at least one angle most posts on this topic skip (the hidden reason, an unexpected consequence, a surprising comparison). Do not write the generic version of the story.
- STAT + SOURCE + IMPLICATION: include at least one sentence with a specific number or date, the kind of record it comes from, but only if the SOURCE TEXT mentions that record, and what it meant in practice. Never invent a statistic, quote or source; if unsure, drop the number.
- ENTITIES: name as many specific real entities as the SOURCE TEXT provides (official names of laws, institutions, companies, places, currencies), at least 6 when available. No vague phrases like "a big bank" when the SOURCE TEXT gives the precise name. Never name an entity that is not in the SOURCE TEXT.
- Optional "THEN vs NOW" line (for example "Then: ... Now: ...") only when both numbers appear in the SOURCE TEXT. Never convert old prices with inflation.
- Short punchy sentences mixed with longer ones. Build suspense paragraph by paragraph so people read to the end. No keyword stuffing.
- Do not repeat the headline word-for-word. No filler. No unsupported phrases like "Experts believe" or "Everyone used".
- Write in your OWN words. Never copy six or more words in a row from the SOURCE TEXT. Retell every fact in fresh sentences.
- The caption is exactly 5 or 6 paragraphs, each separated by a blank line (write \n\n inside the JSON string). The first sentence is at most 120 characters.
- Mention nothing after {RECENT_CUTOFF} in the caption: no modern companies, no modern practices, no recent events.
- Plain text only. NO em dashes (use commas, periods or colons), no emojis, no markdown, no hashtags inside the text.

TOPIC AGE RULE:
- Only write about events that happened at least 30 years ago (nothing after {RECENT_CUTOFF}). Never write about living politicians, current governments, current policies or political debates, even if the SOURCE TEXT covers them. If the SOURCE TEXT is mostly about recent events, return {{"skip": true, "reason": "too recent"}}.

TOPIC RULE:
- The story must be about money, prices, wages, jobs, banks, taxes, trade, currency or business history (currency and banking history ARE allowed).
- NEVER choose topics about executions, crime and punishment, violence, war atrocities, disasters, tragedies or anything graphic or sensitive.

JSON FORMAT: output valid JSON only. Inside string values write paragraph breaks as the escaped characters \n (never real line breaks) and write every double quote inside text as \" . No comments, no trailing commas.

Return ONLY one JSON object, no markdown fences, with keys:
skip, topic, fact, year, country, headline_lines (array), subhook, image_subject, image_scene, image_composition, image_lighting, caption, hashtags (array of exactly 2 relevant topical hashtags like "#MoneyHistory"), evidence (array described above)."""

CHECKER_SYSTEM = """You are a strict fact checker. You receive SOURCE TEXT (from Wikipedia) and a DRAFT social post.
Decide whether every factual claim in the draft's headline and caption is supported by the SOURCE TEXT.
Flag ONLY hard factual problems: a number, date, amount, name or event that is not in the source; wrong cause and effect; "first/only/most/saved/ended" style claims the source does not make; numbers attached to the wrong thing; anachronisms.
Also flag a percentage, ratio or range whose meaning was changed or made more dramatic than the source sentence (for example an "advance of 50-80% of wages" retold as "workers got only 50-80% of their wages").
Do NOT flag rhetorical questions, metaphors, transitions, mood, short summaries of what the source says, or the closing reflection. Wording like "simple" or "brutal" is not a factual claim unless it changes the facts.
Ignore the atmosphere of the paragraph that starts with "Picture" or "Imagine", but flag any number, date or name there that is not in the SOURCE TEXT.
Do NOT use outside knowledge to approve a claim. If the source does not say it, it is unsupported.
Return ONLY one JSON object, no markdown fences:
{"faithful": true or false, "unsupported": ["..."], "hook_score": 1-10}
hook_score rates how strongly the headline would stop a Facebook scroller (10 = jaw-dropping specific number and contrast, 1 = vague textbook summary)."""

CHECKER_WEB_SYSTEM = CHECKER_SYSTEM.replace(
    "Do NOT use outside knowledge to approve a claim. If the source does not say it, it is unsupported.",
    "You also have web search. Confirm the key numbers, dates and names of the headline with reliable web sources "
    "(official archives, central banks, government sites, museums, reputable encyclopedias or newspapers). "
    "If a reliable source contradicts the SOURCE TEXT on a number, date or name, mark the draft unfaithful and list the contradiction. "
    "A claim must still appear in the SOURCE TEXT to be approved.",
)

STYLE_SUFFIX = (
    "documentary photograph from the period, shot on 35mm film with a 50mm lens at eye level, "
    "muted colorized archive look, warm earthy tones, deep contrast"
)
NEGATIVE_PROMPT = (
    "deformed face, distorted face, asymmetrical eyes, extra fingers, fused fingers, extra limbs, malformed hands, "
    "blurry, low resolution, text, letters, watermark, logo, cartoon, painting, illustration, 3d render, "
    "oversaturated, plastic skin, crowd"
)


# ----------------------------------------------------------------- helpers
def log(*a):
    print(*a, flush=True)


def load_history():
    if HISTORY.exists():
        try:
            return json.loads(HISTORY.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_history(items):
    HISTORY.write_text(json.dumps(items[-400:], ensure_ascii=False, indent=1), encoding="utf-8")


def _json_candidates(text):
    """Yield every balanced top-level {...} block, ignoring braces inside strings."""
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, in_str, esc, j = 0, False, False, i
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[i : j + 1]
                    break
            j += 1
        i = j + 1 if depth == 0 and j < n else i + 1


def _parse_loose(candidate):
    attempts = [candidate]
    attempts.append(re.sub(r",\s*([}\]])", r"\1", candidate))  # trailing commas
    attempts.append(attempts[-1].replace("\u201c", '"').replace("\u201d", '"'))  # smart quotes used as delimiters
    for c in attempts:
        try:
            return json.loads(c, strict=False)  # strict=False allows raw line breaks inside strings
        except ValueError:
            continue
    return None


def extract_json(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = re.sub(r"```(?:json)?", "", text)
    if "{" not in text:
        raise ValueError("no JSON found")
    good = None
    for cand in _json_candidates(text):
        obj = _parse_loose(cand)
        if isinstance(obj, dict) and obj:
            good = obj  # keep the LAST valid object (final answer after any draft)
    if good is None:
        raise ValueError("invalid JSON (could not repair)")
    return good


# ---- OpenRouter key pool: many keys, round-robin, exhausted keys are parked automatically
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
KEYSTATE = HERE / "keystate.json"
WEB_CHECK = os.environ.get("WEB_CHECK", "0") == "1"


def _read_keys():
    raw = os.environ.get("OPENROUTER_API_KEYS", "") or os.environ.get("OPENROUTER_API_KEY", "")
    return [k.strip() for k in re.split(r"[\s,;]+", raw) if k.strip()]


OR_KEYS = _read_keys()
_KS = {"rr": 0, "keys": {}}


def _fp(key):
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def load_keystate():
    global _KS
    try:
        _KS = json.loads(KEYSTATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _KS = {"rr": 0, "keys": {}}
    _KS.setdefault("rr", 0)
    _KS.setdefault("keys", {})


def save_keystate():
    live = {_fp(k) for k in OR_KEYS}
    _KS["keys"] = {k: v for k, v in _KS["keys"].items() if k in live}
    KEYSTATE.write_text(json.dumps(_KS, indent=1), encoding="utf-8")


def _parked(fp):
    return _KS["keys"].get(fp, {}).get("dead_until", 0) > time.time()


def _park(fp, seconds, reason):
    e = _KS["keys"].setdefault(fp, {})
    e["dead_until"] = int(time.time() + seconds)
    e["reason"] = reason


def _next_midnight():
    """Seconds until the next UTC midnight (+5 min), when OpenRouter's daily free limit resets."""
    now = datetime.now(timezone.utc)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return (nxt - now).total_seconds()


def keys_usable():
    return [k for k in OR_KEYS if not _parked(_fp(k))]


class ModelBusy(RuntimeError):
    """The model's provider is overloaded or rate-limited upstream. The key is fine; try another model."""


_BUSY = {}
_REASONING_MODELS = set()   # models whose catalogue entry supports a reasoning parameter
_NO_REASON_PARAM = set()   # models that rejected that parameter


def or_call(messages, model, temperature=0.3, max_tokens=800, plugins=None):
    """One OpenRouter request. Starts at the next key in the rotation and moves on if a key is out of credit."""
    if not OR_KEYS:
        raise RuntimeError("no OpenRouter keys configured (secret OPENROUTER_API_KEYS)")
    usable = keys_usable()
    if not usable:
        raise RuntimeError("all OpenRouter keys are out of credit or cooling down")
    start = _KS["rr"] % len(usable)
    _KS["rr"] += 1
    order = usable[start:] + usable[:start]
    last = None
    for key in order[:10]:
        fp = _fp(key)
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if model in _REASONING_MODELS and model not in _NO_REASON_PARAM:
            body["reasoning"] = {"enabled": False}  # we want the answer, not pages of hidden thinking
        if plugins:
            body["plugins"] = plugins
        try:
            r = requests.post(
                OR_URL,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                         "HTTP-Referer": "https://github.com", "X-Title": PAGE_NAME},
                json=body,
                timeout=150,
            )
        except requests.RequestException as e:
            last = f"network error: {e}"
            log(f"[openrouter {fp}] {last}")
            continue
        try:
            data = r.json()
        except ValueError:
            data = {}
        err = data.get("error") if isinstance(data, dict) else None
        code = r.status_code
        if err and not data.get("choices") and str(err.get("code", "")).isdigit():
            code = int(err["code"])
        if code == 200 and isinstance(data, dict) and data.get("choices"):
            usage = data.get("usage") or {}
            log(f"[openrouter {fp}] {model} ok ({usage.get('total_tokens', '?')} tokens)")
            if fp in _KS["keys"]:
                _KS["keys"][fp]["r429"] = 0
            ch = data["choices"][0]
            msg = ch.get("message") or {}
            content = msg.get("content") or ""
            if not content.strip() and ch.get("finish_reason") == "length":
                raise RuntimeError(f"model rejected: {model} spent the whole {max_tokens}-token limit on hidden reasoning")
            if not content.strip():
                reasoning = msg.get("reasoning") or ""
                log(f"[openrouter {fp}] {model} returned EMPTY content (finish_reason={ch.get('finish_reason')}, "
                    f"reasoning_chars={len(reasoning)}); if finish_reason is 'length' the token limit was too small")
                content = reasoning  # some reasoning models leave the final JSON in the reasoning field
            elif ch.get("finish_reason") == "length":
                log(f"[openrouter {fp}] {model} was cut off at the token limit")
            return content
        text = str(err or r.text)[:200]
        last = f"HTTP {code}: {text}"
        log(f"[openrouter {fp}] {model} failed: {last}")
        low = text.lower()
        meta = (err or {}).get("metadata") if isinstance(err, dict) else None
        etype = str((meta or {}).get("error_type", "")).lower() if isinstance(meta, dict) else ""
        if ("provider returned error" in low or "upstream" in low or "overloaded" in low or "provider_overloaded" in etype
                or code in (502, 503, 504)):
            _BUSY[model] = time.time() + 600
            raise ModelBusy(f"{model} is busy upstream ({last[:90]})")
        if code == 402:
            _park(fp, _next_midnight(), "no credit (try again tomorrow)")
            log(f"[openrouter {fp}] no credit, key rests until the next UTC day")
        elif code == 401 or (code == 403 and "moderation" not in low and "flagged" not in low):
            _park(fp, 30 * 86400, "invalid or forbidden key")
        elif code == 429:
            e = _KS["keys"].setdefault(fp, {})
            e["r429"] = e.get("r429", 0) + 1
            if "per-day" in low or "per day" in low or "daily" in low or e["r429"] >= 3:
                e["r429"] = 0
                _park(fp, _next_midnight(), "daily limit reached (resets at UTC midnight)")
                log(f"[openrouter {fp}] daily limit reached, key rests until the next UTC day")
            else:
                _park(fp, 120, "rate limited for 2 minutes")
        elif code == 400 and "reasoning" in body and "reasoning" in low:
            _NO_REASON_PARAM.add(model)  # this model does not accept the switch: retry without it
            log(f"[{model}] does not accept the reasoning switch, retrying without it")
            continue
        elif code in (400, 403, 404):
            raise RuntimeError(f"model rejected the request: {last}")
        time.sleep(0.5)
    raise RuntimeError(f"all tried OpenRouter keys failed: {last}")


def chat(system, user, model, temperature=0.9, max_tokens=3000, plugins=None):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return or_call(messages, model, temperature, max_tokens, plugins)


# ---- automatic choice of the best FREE models (no credit is spent unless you enable a paid fallback)
FAMILY_BONUS = {"deepseek": 30, "qwen": 26, "gpt-oss": 25, "glm": 24, "kimi": 24, "llama": 20, "mistral": 14, "gemma": 12, "nvidia": 12}
FAMILY_DEFAULT_SIZE = {"deepseek": 600, "kimi": 1000, "glm": 300, "qwen": 100, "gpt-oss": 120, "llama": 70, "mistral": 24, "gemma": 27, "nvidia": 50}
SKIP_NAME = re.compile(r"guard|safety|moderat|embed|tts|audio|whisper|image|diffusion|ocr|coder|code|roleplay|uncensor", re.I)
SLOW_NAME = re.compile(r"r1|reason|think", re.I)
STATIC_FREE = [
    "deepseek/deepseek-chat-v3.1:free", "qwen/qwen3-235b-a22b:free", "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free", "google/gemma-3-27b-it:free", "mistralai/mistral-small-3.2-24b-instruct:free",
]


def _family(model):
    mid = model.lower()
    for fam in FAMILY_BONUS:
        if fam in mid:
            return fam
    return mid.split("/")[0]


def _score(m):
    mid = m["id"].lower()
    sizes = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)b", mid)]
    fam = _family(mid)
    size = max(sizes) if sizes else FAMILY_DEFAULT_SIZE.get(fam, 20)
    size_score = min(math.log2(size + 1) * 6, 50)
    if fam not in FAMILY_BONUS:  # unknown lab: trust a big number in the name less
        size_score *= 0.6
    score = FAMILY_BONUS.get(fam, 0) + size_score
    score += min(max((m.get("created", 0) - 1735689600) / (30 * 86400), 0) * 0.5, 20)  # newer is better (months since 2025-01)
    if "instruct" in mid or "chat" in mid:
        score += 3
    if SLOW_NAME.search(mid) or m.get("reasoning"):
        score -= 20
    elif m.get("rparam"):
        score -= 6  # hybrid models: usable with thinking switched off, but plain instruct models are safer
    for pref in _models("PREFER_MODELS", ""):
        if pref.lower() in mid:
            score += 100
    return score


def discover_models():
    """Ask OpenRouter which models are free right now and rank them; falls back to a static list."""
    try:
        r = requests.get("https://openrouter.ai/api/v1/models", timeout=60)
        r.raise_for_status()
        free = []
        for m in r.json().get("data", []):
            mid = m.get("id", "")
            pr = m.get("pricing") or {}
            try:
                is_free = float(pr.get("prompt", 1)) == 0 and float(pr.get("completion", 1)) == 0
            except (TypeError, ValueError):
                is_free = False
            if not (is_free or mid.endswith(":free")) or mid.startswith("openrouter/") or SKIP_NAME.search(mid):
                continue
            arch = m.get("architecture") or {}
            if (arch.get("output_modalities") or ["text"]) != ["text"]:
                continue
            exp = m.get("expiration_date")
            if exp:
                try:
                    if datetime.fromisoformat(str(exp)[:10]).replace(tzinfo=timezone.utc) < datetime.now(timezone.utc) + timedelta(days=14):
                        continue  # going away soon
                except ValueError:
                    pass
            desc = str(m.get("description") or "").lower()
            if re.search(r"coding agent|coding model|code generation|software engineering agent|agentic coding", desc):
                continue
            ctx = m.get("context_length") or 0
            if ctx and ctx < 16000:
                continue
            free.append({"id": mid, "created": m.get("created") or 0, "vision": "image" in (arch.get("input_modalities") or []),
                         "reasoning": "reasoning" in desc,
                         "rparam": "reasoning" in (m.get("supported_parameters") or []) or "include_reasoning" in (m.get("supported_parameters") or [])})
            if "reasoning" in (m.get("supported_parameters") or []) or "include_reasoning" in (m.get("supported_parameters") or []):
                _REASONING_MODELS.add(mid)
        if free:
            free.sort(key=_score, reverse=True)
            return [m["id"] for m in free], [m["id"] for m in free if m["vision"]]
    except Exception as e:  # noqa: BLE001
        log("could not read the OpenRouter model list:", e)
    return list(STATIC_FREE), []


def _demoted(model):
    return _KS.setdefault("demoted", {}).get(model, 0) > time.time()


def _demote(model, seconds):
    _KS.setdefault("demoted", {})[model] = int(time.time() + seconds)
    log(f"model {model} rests for {seconds // 3600} h")


def setup_models():
    global WRITER_MODELS, CHECKER_MODELS, QA_MODELS
    ranked, vision = discover_models()
    ranked = [m for m in ranked if not _demoted(m)]
    paid = _models("PAID_FALLBACK_MODEL", "")  # optional: only used if every free model fails
    if not WRITER_MODELS:
        WRITER_MODELS = list(dict.fromkeys(ranked[:6] + paid))
    if not CHECKER_MODELS:
        fam = _family(ranked[0]) if ranked else ""
        others = [m for m in ranked if _family(m) != fam][:4]
        CHECKER_MODELS = list(dict.fromkeys((others or ranked[:4]) + ranked[:2] + paid))
    if not QA_MODELS:
        QA_MODELS = [m for m in vision if not _demoted(m)][:5]
    log("writer models:", WRITER_MODELS[:3], "| checker models:", CHECKER_MODELS[:3], "| image QA models:", QA_MODELS[:2])


def chat_json(system, user, models, temperature, tries=2, max_tokens=3000, plugins=None):
    last = None
    for model in models:
        if _BUSY.get(model, 0) > time.time() and len(models) > 1:
            continue  # busy upstream a moment ago, use the next model first
        bad = 0
        for i in range(tries):
            try:
                raw = chat(system, user, model, temperature, max_tokens, plugins)
            except Exception as e:  # noqa: BLE001
                last = e
                log(f"[{model}] call failed: {e}")
                if str(e).startswith("model rejected"):
                    _demote(model, 24 * 3600)
                break  # go to next model
            try:
                return extract_json(raw)
            except ValueError as e:
                last = e
                bad += 1
                flat = (raw or "").strip().replace("\n", " ")
                snippet = flat[:240] + (" ... " + flat[-120:] if len(flat) > 400 else flat[240:])
                log(f"[{model}] bad JSON ({i + 1}/{tries}): {e} | reply length {len(flat)} | reply was: {snippet!r}")
        if bad >= tries:
            _demote(model, 6 * 3600)
    raise RuntimeError(f"no model returned valid JSON: {last}")


# ---- free evidence: Wikipedia (no API key, no cost)
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = os.environ.get("WIKI_UA", "PocketChangeHistoryBot/1.0 (educational money-history page; GitHub Actions)")
SHOW_SOURCE = os.environ.get("SHOW_SOURCE", "0") == "1"
BANNED_TITLE = re.compile(r"vending|coca-cola|model t\b|monopoly|mcdonald|^apple\b|amazon|automated teller|^credit card$", re.I)
CUT_HEADINGS = re.compile(r"\n=+ *(References|See also|External links|Notes|Further reading|Bibliography|Footnotes|Sources|Citations) *=+", re.I)
YEAR_RE = re.compile(r"\b(?:1[5-9]\d\d|20\d\d)\b")
NUM_RE = re.compile(
    r"([$\u00a3\u20ac])?\s?(\d[\d,]*(?:\.\d+)?)(s)?\b\s?(cents?|dollars?|pounds?|pence|pennies|shillings?|francs?|marks?|yen|percent|%)?",
    re.I,
)


_WIKI_LAST = [0.0]


def wiki_get(params):
    """Polite, rate-limit friendly Wikipedia call: spaced requests and patient retries on 429/503."""
    p = {"format": "json", "formatversion": "2", **params}
    last = None
    for i in range(5):
        wait = 1.3 - (time.time() - _WIKI_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _WIKI_LAST[0] = time.time()
        r = requests.get(WIKI_API, params=p, headers={"User-Agent": WIKI_UA, "Accept-Encoding": "gzip"}, timeout=30)
        if r.status_code in (429, 503):
            try:
                delay = float(r.headers.get("Retry-After", ""))
            except ValueError:
                delay = 0
            delay = max(delay, 8 * (i + 1))
            last = f"HTTP {r.status_code}"
            log(f"wikipedia busy ({r.status_code}), waiting {delay:.0f}s")
            time.sleep(min(delay, 60))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"wikipedia kept refusing: {last}")


def wiki_search(query, limit=10):
    data = wiki_get({"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "srnamespace": 0})
    return [x["title"] for x in data.get("query", {}).get("search", [])]


def wiki_extract(title):
    data = wiki_get({"action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1, "titles": title})
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return ""
    text = pages[0].get("extract", "") or ""
    m = CUT_HEADINGS.search(text)
    if m:
        text = text[: m.start()]
    return text.strip()[:14000]


MONEY_WORDS = re.compile(
    r"\b(price|prices|wage|wages|tax|taxes|taxation|bank|banks|banking|cost|costs|cents?|dollars?|pounds?|shillings?|pence|francs?|marks?|yen|"
    r"currency|coin|coins|banknotes?|salary|salaries|profit|profits|sales|sold|store|stores|company|founded|credit|loan|loans|debt|trade|tariff|fee|fees|fare|fares|paid|payment|payments|money)\b",
    re.I,
)
MIN_MONEY_DENSITY = float(os.environ.get("MIN_MONEY_DENSITY", "3"))   # money words per 1,000 characters
MIN_MONEY_WORDS = int(os.environ.get("MIN_MONEY_WORDS", "8"))
SHOW_SUBHOOK = os.environ.get("SHOW_SUBHOOK", "0") == "1"
SENSITIVE_TITLE = re.compile(
    r"racis|nudity|murder|assassin|massacre|genocide|rape|sexual|porn|suicide|execution|terror|war crime|holocaust|slavery|rasputin|"
    r"abuse|torture|lynch|nazi|fascis|communis|genital|prostitut|drug|cocaine|heroin|opium|election|referendum|impeach|political part",
    re.I,
)


def pick_source(used):
    """Random money-history search -> a real Wikipedia article we have not used yet."""
    cat = random.randrange(len(CATEGORIES))
    kw = random.choice(CATEGORY_KEYWORDS[cat])
    query = kw if random.random() < 0.5 else f"{kw} {random.choices(WIKI_COUNTRIES, COUNTRY_WEIGHTS)[0]}"
    log("wikipedia search:", query)
    titles = [
        t
        for t in wiki_search(query, 15)
        if not re.match(r"(List of|Timeline of|Index of|Outline of)", t)
        and not BANNED_TITLE.search(t)
        and not SENSITIVE_TITLE.search(t)
        and t not in used
    ][:8]
    random.shuffle(titles)
    for t in titles[:4]:
        text = wiki_extract(t)
        used.add(t)
        money = len(MONEY_WORDS.findall(text))
        density = money / max(len(text), 1) * 1000  # money words per 1,000 characters
        yrs = [int(y) for y in YEAR_RE.findall(text)]
        recent = sum(1 for y in yrs if y > RECENT_CUTOFF) / max(len(yrs), 1)
        if (len(text) >= 1500 and len(yrs) >= 2 and money >= MIN_MONEY_WORDS and density >= MIN_MONEY_DENSITY
                and recent <= 0.35):
            log(f"article {t!r} accepted ({money} money words, {density:.1f} per 1,000 characters)")
            return t, text
        why = []
        if len(text) < 1500:
            why.append(f"too short ({len(text)} characters)")
        if len(YEAR_RE.findall(text)) < 2:
            why.append("fewer than 2 years")
        if money < MIN_MONEY_WORDS or density < MIN_MONEY_DENSITY:
            why.append("not money-focused enough")
        if recent > 0.35:
            why.append(f"too recent ({recent:.0%} of the years are after {RECENT_CUTOFF})")
        log(f"skipped article {t!r}: {', '.join(why)} ({money} money words, {density:.1f} per 1,000 characters)")
    raise ValueError(f"no usable article for query {query!r}")


def _norm(s):
    s = s.lower()
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'), ("\u2013", "-"), ("\u2014", "-")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$\u00a3\u20ac%.' -]", " ", s)).strip()


def quote_supported(quote, src_norm, src_tokens):
    q = _norm(quote)
    qt = q.split()
    if len(qt) < 4:
        return False
    if q in src_norm:
        return True
    qset, n = set(qt), len(qt)
    for i in range(0, max(1, len(src_tokens) - n + 1)):
        if len(qset & set(src_tokens[i : i + n + 3])) / len(qset) >= 0.9:
            return True
    return False


def numbers_in(text, factual_only):
    out = set()
    for m in NUM_RE.finditer(text):
        cur, num, decade, unit = m.group(1), m.group(2), m.group(3), m.group(4)
        n = num.replace(",", "").rstrip(".")
        if not n:
            continue
        if not factual_only:
            out.add(n)
            continue
        if decade:  # "1930s" style decades are not exact claims
            continue
        try:
            val = float(n)
        except ValueError:
            continue
        if cur or unit or YEAR_RE.fullmatch(n) or val >= 100:
            out.add(n)
    return out


def verify_grounding(post, source):
    """Deterministic check: every quote must be in the source and every key number must come from it."""
    problems = []
    evidence = post.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 2:
        return ["evidence missing"]
    src_norm = _norm(source)
    src_tokens = src_norm.split()
    for item in evidence:
        quote = item.get("quote", "") if isinstance(item, dict) else str(item)
        if not quote_supported(quote, src_norm, src_tokens):
            problems.append(f"quote not found in source: {quote[:70]!r}")
    text = " ".join(post["headline_lines"]) + " " + post["caption"]
    missing = numbers_in(text, True) - numbers_in(source, False)
    if missing:
        problems.append(f"numbers not in source: {sorted(missing)}")
    return problems


def write_post(title, source, recent):
    system = WRITER_SYSTEM
    if FACELESS and _small_model(IMAGE_MODELS[0]):
        system += (
            "\n\nFACELESS RULE (the image model distorts faces badly): in image_subject never show a face. The person is seen from "
            "behind, in side silhouette, or only hands, tools and objects are visible. In image_scene name the exact era and place "
            "(for example 'ancient Babylon, mudbrick yard, reed boats') so the picture is not drawn in modern clothes."
        )
    user = (
        f"SOURCE ARTICLE TITLE: {title}\n\nSOURCE TEXT:\n{source}\n\n"
        f"Do NOT repeat or closely resemble any of these recent topics: {json.dumps(recent)}\n"
        "Write the post using ONLY the SOURCE TEXT. Return the JSON object now."
    )
    return chat_json(system, user, WRITER_MODELS, 0.7, tries=1, max_tokens=5000)


def check_faithful(post, source):
    draft = {"headline_lines": post["headline_lines"], "caption": post["caption"]}
    user = f"SOURCE TEXT:\n{source}\n\nDRAFT:\n{json.dumps(draft, ensure_ascii=False)}"
    if WEB_CHECK:
        return chat_json(CHECKER_WEB_SYSTEM, user, CHECKER_MODELS, 0.1, max_tokens=1500, plugins=[{"id": "web", "max_results": 3}])
    return chat_json(CHECKER_SYSTEM, user, CHECKER_MODELS, 0.1, max_tokens=1500)


def ensure_highlight(lines):
    """If the model forgot [brackets], auto-highlight numbers, prices and years in red."""
    if any("[" in l for l in lines):
        return lines
    pat = re.compile(r"(\$?£?€?\d[\d,\.]*\s?(?:CENTS?|PENNIES|PENCE|DOLLARS?|POUNDS?|%|YEARS?|HOURS?|DAYS?|WEEKS?)?)", re.I)
    return [pat.sub(lambda m: f"[{m.group(1).strip()}]" + (" " if m.group(1).endswith(" ") else ""), l) for l in lines]


def _runs(line):
    """Split a line into (text, is_red) runs. Brackets only switch the colour on and off and never survive."""
    runs, red, buf = [], False, ""
    for ch in line:
        if ch == "[":
            if buf:
                runs.append((buf, red))
                buf = ""
            red = True
        elif ch == "]":
            if buf:
                runs.append((buf, red))
                buf = ""
            red = False
        else:
            buf += ch
    if buf:
        runs.append((buf, red))
    return runs


def fix_brackets(line):
    """Repair common model mistakes: $[100] -> [$100], [1930,] -> [1930],, unbalanced brackets, parentheses."""
    line = re.sub(r"([$\u00a3\u20ac])\[", r"[\1", line)
    line = re.sub(r"([,.;:!?]+)\]", r"]\1", line)
    if line.count("[") != line.count("]"):
        line = line.replace("[", "").replace("]", "")
    return line.replace("(", "").replace(")", "")


def limit_red(lines, max_runs=2):
    """Keep at most `max_runs` red groups, so the headline never turns into a red wall."""
    out, runs = [], 0
    for line in lines:
        res = ""
        for text, red in _runs(line):
            if red:
                runs += 1
                res += f"[{text}]" if runs <= max_runs else text
            else:
                res += text
        out.append(res)
    return out


def rewrap_headline(lines, width=30):
    """Re-wrap headline words into lines of at most `width` characters, keeping the red words red."""
    chars = []
    for line in lines:
        for text, red in _runs(line):
            chars.extend((c, red) for c in text)
        chars.append((" ", False))
    words, cur = [], []
    for ch, red in chars:
        if ch.isspace():
            if cur:
                words.append(cur)
                cur = []
        else:
            cur.append((ch, red))
    if cur:
        words.append(cur)

    def plain(ws):
        return " ".join("".join(c for c, _ in w) for w in ws)

    out, cur_line = [], []
    for w in words:
        if cur_line and len(plain(cur_line + [w])) > width:
            out.append(cur_line)
            cur_line = []
        cur_line.append(w)
    if cur_line:
        out.append(cur_line)

    rendered = []
    for ws in out:
        seq = []
        for i, w in enumerate(ws):
            if i:
                seq.append((" ", ws[i - 1][-1][1] and w[0][1]))
            seq.extend(w)
        text, i = "", 0
        while i < len(seq):
            j = i
            while j < len(seq) and seq[j][1] == seq[i][1]:
                j += 1
            chunk = "".join(c for c, _ in seq[i:j])
            text += f"[{chunk}]" if seq[i][1] else chunk
            i = j
        rendered.append(text)
    return rendered


def validate_post(post):
    lines = post["headline_lines"]
    if isinstance(lines, str):
        lines = [lines]
    if not isinstance(lines, list) or not lines:
        return "headline missing"
    lines = [fix_brackets(str(l)).strip() for l in lines if str(l).strip()]
    if len(lines) == 1 or len(lines) > 4 or any(len(re.sub(r"[\[\]]", "", l)) > 32 for l in lines):
        lines = rewrap_headline(lines)
    lines = limit_red(lines)
    if not 2 <= len(lines) <= 4:
        return f"headline has {len(lines)} lines after wrapping"
    post["headline_lines"] = lines
    words = len(" ".join(lines).split())
    years = [int(y) for y in YEAR_RE.findall(" ".join(lines))]
    m_year = re.search(r"\d{4}", str(post.get("year") or ""))
    if any(y > RECENT_CUTOFF for y in years) or (m_year and int(m_year.group()) > RECENT_CUTOFF and "bc" not in str(post.get("year")).lower()):
        return f"too recent (limit {RECENT_CUTOFF})"
    if not 6 <= words <= 15:
        return f"headline word count {words}"
    cwords = len(post["caption"].split())
    if cwords < 170 or cwords > 340:
        return f"caption word count {cwords}"
    cap = re.sub(r"\s?\[\d{1,2}\]", "", post["caption"])
    cap = re.sub(r"\s*[\u2014\u2013]\s*", ", ", cap)  # no em/en dashes (AI tell)
    cap = re.sub(r"[*_#`]+", "", cap).strip()
    post["caption"] = cap
    post["headline_lines"] = ensure_highlight(lines)
    return None


def add_hashtags(post):
    tags = ["#PocketChangeHistory"]
    for t in post.get("hashtags") or []:
        t = "#" + re.sub(r"[^A-Za-z0-9]", "", str(t))
        if len(t) > 3 and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    tail = " ".join(tags[:3])
    if SHOW_SOURCE and post.get("source_title"):
        tail = f'Source: Wikipedia, "{post["source_title"]}"\n' + tail
    post["caption"] = post["caption"].rstrip() + "\n\n" + tail


RUN_BUDGET_MIN = float(os.environ.get("RUN_BUDGET_MINUTES", "20"))


HEDGE_WORDS = re.compile(r"\b(reportedly|according to|accounts|about|roughly|estimated|around|some sources|records suggest)\b", re.I)


def _words(text):
    return re.sub(r"[^a-z0-9$%. ]+", " ", text.lower().replace("\u2019", "'")).split()


def check_originality(post, source):
    """Own words, story shape, nothing modern, cautious wording for percentages. Returns a problem text or None."""
    cap = post["caption"]
    paras = [x.strip() for x in re.split(r"\n+", cap) if x.strip()]
    if len(paras) < 5:
        return f"caption has {len(paras)} paragraphs, needs 5 or more"
    post["caption"] = "\n\n".join(paras)
    first = re.split(r"(?<=[.?!])\s", paras[0], maxsplit=1)[0]
    if len(first) > 140:
        return f"first sentence is {len(first)} characters, limit 140"
    cap_years = [int(y) for y in YEAR_RE.findall(cap)]
    if any(y > RECENT_CUTOFF for y in cap_years):
        return f"caption mentions a year after {RECENT_CUTOFF}"
    src = _words(source)
    grams = {" ".join(src[i : i + 6]) for i in range(max(0, len(src) - 5))}
    cw = _words(cap)
    for i in range(max(0, len(cw) - 5)):
        g = " ".join(cw[i : i + 6])
        if g in grams:
            return f"caption copies the source: '{g}'"
    head = " ".join(post["headline_lines"])
    if ("%" in head or re.search(r"\b\d+\s*(?:to|-)\s*\d+\b", head)) and not HEDGE_WORDS.search(cap):
        return "percentage or range in the headline, but the caption has no cautious wording"
    return None


def build_post(recent, used):
    deadline = time.time() + RUN_BUDGET_MIN * 60
    for attempt in range(1, 13):
        if time.time() > deadline:
            log(f"time budget of {RUN_BUDGET_MIN:.0f} minutes used up")
            break
        try:
            title, source = pick_source(used)
            used.add(title)
            log(f"attempt {attempt}: source article: {title}")
            post = write_post(title, source, recent)
            if post.get("skip"):
                log("writer skipped:", post.get("reason"))
                continue
            for key in ("headline_lines", "caption", "topic"):
                if not post.get(key):
                    raise ValueError(f"missing {key}")
            if not (post.get("image_subject") or post.get("image_prompt")):
                raise ValueError("missing image fields")
            problem = validate_post(post) or check_originality(post, source)
            if problem:
                log("rejected:", problem)
                continue
            problems = verify_grounding(post, source)
            if problems:
                hard = [
                    p for p in problems
                    if "numbers not in source" in p.lower()
                    or ("quote not found in source" in p.lower() and len(p) > 60)
                ]
                if hard:
                    log("grounding failed:", hard)
                    continue
            verdict = check_faithful(post, source)
            if not verdict.get("faithful"):
                unsupported = verdict.get("unsupported") or []
                hard_unsupported = [
                    u for u in unsupported
                    if any(k in u.lower() for k in ("not in the source", "unsupported", "wrong", "missing", "not supported"))
                ]
                if hard_unsupported:
                    log("checker: unsupported claims:", hard_unsupported)
                    continue

            try:
                if float(verdict.get("hook_score", 0)) < 5:
                    log("rejected: weak hook", verdict.get("hook_score"))
                    continue
            except (TypeError, ValueError):
                pass
            post["source_title"] = title
            post["confidence"] = "Grounded in Wikipedia source text"
            post["sources"] = [f"Wikipedia: {title}"]
            post["issues"] = []
            add_hashtags(post)
            return post
        except Exception as e:
            log("attempt failed:", e)

    raise RuntimeError("could not build a source-grounded post after 12 attempts")


# ----------------------------------------------------------------- image
NEGATIVE_SD = (
    "deformed, distorted face, ugly, bad anatomy, extra fingers, mutated hands, poorly drawn hands, poorly drawn face, "
    "blurry, lowres, text, watermark, logo, cartoon, anime, painting, 3d render, oversaturated, duplicate, cropped, crowd, "
    "modern clothing, modern buildings, factory, cars, electric lights, straw hat"
)
FACELESS = os.environ.get("FACELESS", "1") == "1"
IMAGE_QA = os.environ.get("IMAGE_QA", "1") == "1"
QA_MODELS = _models("IMAGE_QA_MODEL", "")      # empty = automatic: free vision models
QA_PROMPT = (
    "You are a strict quality reviewer for AI-generated vintage photographs. Reject the image if you see ANY of: "
    "a distorted, melted or asymmetric face; fused, extra or missing fingers or malformed hands; extra or missing limbs; "
    "two heads or duplicated people; garbled letters only if readable writing is clearly visible in the picture (a picture with no writing is fine); impossible or merged objects; a cartoon, anime or painted look. "
    'Return ONLY JSON: {"ok": true or false, "score": 1-10, "problems": ["..."]}. Score 8 or more only if it looks like a clean real photograph.'
)
_QA_OFF = [False]


def _small_model(model):
    return any(t in model.lower() for t in ("dreamshaper", "lcm", "sd15", "stable-diffusion"))


def _gen_size(model):
    if _small_model(model):  # SD-1.5 models break above ~768 px; the server may return a slightly smaller size
        try:
            w, h = os.environ.get("GEN_SIZE", "512x640").lower().split("x")
            return int(w), int(h)
        except ValueError:
            return 512, 640
    return GEN_W, GEN_H


def _shorten(text, n):
    return " ".join(str(text or "").replace("\n", " ").split()[:n]).rstrip(".,;")


def build_image_prompt(post, variant=0):
    """Large models: FLUX-style order. Small SD-1.5 models: short keyword prompt (CLIP reads only ~60 words)."""
    if _small_model(IMAGE_MODELS[0]):
        era = _shorten(f"{post.get('year') or ''} {post.get('country') or ''}", 4)
        subject = _shorten(post.get("image_subject") or post.get("image_prompt"), 16)
        scene = _shorten(post.get("image_scene"), 10)
        comp = _shorten(post.get("image_composition") or "medium close-up, eye level", 6)
        light = _shorten(post.get("image_lighting") or "soft window light", 5)
        if variant == 1:  # objects only: far less distortion than people
            return (f"{era}, close-up still life of old paper documents, coins and a pen on a worn wooden table, {scene}, {light}, "
                    "vintage documentary photo, 35mm film, muted colors, film grain, sharp focus")[:500].lstrip(", ")
        return (f"{era}, {subject}, {scene}, {comp}, {light}, vintage documentary photo, 35mm film, "
                "muted colors, film grain, sharp focus")[:500].lstrip(", ")

    def part(key):
        return str(post.get(key) or "").strip().rstrip(".")

    subject, scene = part("image_subject"), part("image_scene")
    if not subject:
        return (part("image_prompt") + ". " + STYLE_SUFFIX)[:1500]
    composition = part("image_composition") or "medium close-up at eye level"
    lighting = part("image_lighting") or "soft window light from the left"
    return (
        f"{subject}. {scene}. {STYLE_SUFFIX}. {composition}, vertical 4:5 frame, main subject in the upper two thirds, "
        f"the lower third is plain empty table, floor or soft shadow. {lighting}. "
        "Sharp focus, natural skin texture, relaxed natural hands, fine film grain, photorealistic, high detail. "
        "No text, no extra people, no watermarks."
    )[:1500]


def _fetch_candidate(prompt, tag):
    last = None
    for i in range(3):
        model = IMAGE_MODELS[(tag + i) % len(IMAGE_MODELS)]
        gw, gh = _gen_size(model)
        neg = NEGATIVE_SD if _small_model(model) else NEGATIVE_PROMPT
        seed = random.randint(1, 10**8)
        params = f"model={quote(model, safe='')}&width={gw}&height={gh}&seed={seed}&nologo=true"
        if i < 2:  # last try drops the negative prompt in case the model rejects it
            params += f"&negative_prompt={quote(neg)}"
        try:
            r = requests.get(f"{API}/image/{quote(prompt)}?{params}", headers={"Authorization": f"Bearer {KEY}"}, timeout=240)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                log(f"image candidate ok (model {model}, {img.size[0]}x{img.size[1]})")
                return ImageOps.fit(img, (W, H), method=Image.LANCZOS, centering=(0.5, 0.4)), _small_model(model)
            last = f"HTTP {r.status_code} {r.text[:160]}"
        except Exception as e:  # noqa: BLE001
            last = e
        log(f"image call failed ({i + 1}/3, model {model}): {last}")
        time.sleep(4 * (i + 1))
    raise RuntimeError(f"image generation failed: {last}")


def _finish(img, upscaled):
    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.05)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.4 if upscaled else 1.2, percent=80 if upscaled else 65, threshold=3))
    if upscaled:  # a little film grain hides the softness of an upscaled 512 px image
        img = Image.blend(img, Image.effect_noise((W, H), 30).convert("RGB"), 0.05)
    return img


_QA_JUDGE = []


def vision_check(img):
    """Ask free vision models whether the image is distorted. Retries once if they are all busy. None = unavailable."""
    if not QA_MODELS:
        return None
    try:
        small = img.copy()
        small.thumbnail((640, 800))
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": QA_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}]
        for round_ in range(2):
            now = time.time()
            order = list(QA_MODELS)
            if _QA_JUDGE and _QA_JUDGE[0] in order:  # one judge for all candidates, so the scores are comparable
                order.remove(_QA_JUDGE[0])
                order.insert(0, _QA_JUDGE[0])
            if round_ == 0:  # models that were busy a moment ago go last, they usually still are
                order.sort(key=lambda m: _BUSY.get(m, 0) > now)
            for model in order:
                if round_ == 0 and _BUSY.get(model, 0) > now and any(_BUSY.get(x, 0) <= now for x in order):
                    continue
                try:
                    result = extract_json(or_call(messages, model, temperature=0, max_tokens=400))
                    _QA_JUDGE[:] = [model]
                    return result
                except Exception as e:  # noqa: BLE001
                    log(f"[image QA {model}] unavailable: {str(e)[:120]}")
            if round_ == 0:
                time.sleep(8)
    except Exception as e:  # noqa: BLE001
        log("image QA error:", e)
    return None


def generate_image(post):
    """Up to N candidates (people first, then object still-lifes); keep the first that passes QA, else the best scored."""
    best, best_score, best_up = None, -1.0, False
    qa_failures = 0
    for n in range(max(1, int(os.environ.get("IMAGE_CANDIDATES", "5") or 5))):
        prompt = build_image_prompt(post, variant=1 if n >= 2 else 0)
        try:
            img, up = _fetch_candidate(prompt, n)
        except RuntimeError as e:
            log(e)
            continue
        if not IMAGE_QA:
            return _finish(img, up)
        verdict = None if qa_failures >= 2 else vision_check(img)
        if verdict is None:
            if IMAGE_QA and qa_failures < 2:
                qa_failures += 1
                log(f"image QA unavailable for candidate #{n + 1}, kept as unrated")
            score = 5.0
        else:
            ok = bool(verdict.get("ok"))
            try:
                score = float(verdict.get("score", 8 if ok else 3))
            except (TypeError, ValueError):
                score = 8.0 if ok else 3.0
            log(f"image QA #{n + 1}: ok={ok} score={score} problems={verdict.get('problems')}")
            if ok and score >= 6:
                return _finish(img, up)
        if score > best_score:
            best, best_score, best_up = img, score, up
    if best is not None:
        log(f"no image passed QA; using the best candidate (score {best_score})")
        return _finish(best, best_up)
    raise RuntimeError("image generation failed (no candidate)")


def load_font(size):
    path = FONT_PATH if FONT_PATH.exists() else FALLBACK_FONT
    return ImageFont.truetype(str(path), size)


def parse_segments(line):
    return [(t, RED if red else WHITE) for t, red in _runs(line) if t]


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def _spaced(draw, cx, y, text, font, fill, spacing=3, shadow=True):
    total = sum(font.getlength(c) + spacing for c in text) - spacing
    x = cx - total / 2
    for c in text:
        if shadow:
            draw.text((x + 2, y + 3), c, font=font, fill=(0, 0, 0))
        draw.text((x, y), c, font=font, fill=fill)
        x += font.getlength(c) + spacing


def overlay_text(photo, headline_lines, subhook):
    """Full-bleed photo, dark gradient at the bottom, big headline with red keywords, small subline, watermarks."""
    img = photo.convert("RGB")
    if img.size != (W, H):
        img = ImageOps.fit(img, (W, H), method=Image.LANCZOS)

    grad = Image.new("L", (1, H))
    px = grad.load()
    for y in range(H):
        f = y / H
        if f < 0.50:
            a = 0.0
        elif f < 0.72:
            a = 0.90 * _smooth((f - 0.50) / 0.22)
        else:
            a = 0.90 + 0.08 * ((f - 0.72) / 0.28)
        px[0, y] = int(255 * a)
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), grad.resize((W, H)))

    lines = [parse_segments(l.upper()) for l in headline_lines if l.strip()]
    max_w, max_h = W - 120, int(H * 0.29)
    size = 108
    while size > 40:
        font = load_font(size)
        lh = int(size * 1.08)
        widths = [sum(font.getlength(t) for t, _ in segs) for segs in lines]
        total = lh * len(lines) + (int(size * 0.62) if subhook.strip() else 0)
        if max(widths) <= max_w and total <= max_h:
            break
        size -= 4

    draw = ImageDraw.Draw(img)
    y = H - 104 - total
    for segs, width in zip(lines, widths):
        x = (W - width) / 2
        for text, color in segs:
            draw.text((x + 3, y + 4), text, font=font, fill=(0, 0, 0))
            draw.text((x, y), text, font=font, fill=color)
            x += font.getlength(text)
        y += lh
    sub = subhook.upper().strip()
    if sub:
        _spaced(draw, W / 2, y + int(size * 0.10), sub, load_font(max(26, int(size * 0.34))), WHITE, spacing=3)

    # watermarks: page name (bottom-left) and the AI label (bottom-right)
    if PAGE_NAME:
        wf = load_font(24)
        draw.ellipse([30, H - 46, 46, H - 30], fill=GOLD)
        x = 58
        for c in PAGE_NAME.upper():
            draw.text((x + 1, H - 46), c, font=wf, fill=(0, 0, 0))
            draw.text((x, H - 47), c, font=wf, fill=(238, 238, 238))
            x += wf.getlength(c) + 2
    if WATERMARK:
        af = load_font(20)
        label = WATERMARK.upper()
        aw = sum(af.getlength(c) + 1 for c in label)
        draw.text((W - aw - 30, H - 44), label, font=af, fill=(205, 205, 205))
    return img


# ----------------------------------------------------------------- telegram
def tg(method, **kwargs):
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", timeout=120, **kwargs)
    if not r.ok:
        raise RuntimeError(f"telegram {method} failed: {r.status_code} {r.text[:300]}")
    return r.json()


def send_to_telegram(img, post):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=94, subsampling=0)
    buf.seek(0)
    # 1) image alone, 2) full caption as a normal message (no 1024-char caption limit)
    tg("sendPhoto", data={"chat_id": TG_CHAT}, files={"photo": ("post.jpg", buf)})
    tg("sendMessage", data={"chat_id": TG_CHAT, "text": post["caption"].strip()[:4000]})
    if SEND_NOTES:
        notes = (
            f"Fact check notes\n"
            f"Confidence: {post.get('confidence')}\n"
            f"Fact: {post.get('fact')}\n"
            f"Sources to verify: {', '.join(post.get('sources') or [])}\n"
        )
        if post.get("issues"):
            notes += "Checker remarks: " + "; ".join(map(str, post["issues"])) + "\n"
        notes += "Image rights: AI-generated recreation, no real people or logos."
        tg("sendMessage", data={"chat_id": TG_CHAT, "text": notes[:4000]})


# ----------------------------------------------------------------- facebook
def send_to_facebook(img, post):
    """Publish image + caption together as ONE photo post on the Page."""
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=94, subsampling=0)
    buf.seek(0)
    caption = post["caption"].strip()
    if FB_DISCLOSURE:
        caption += "\n\n" + FB_DISCLOSURE
    r = requests.post(
        f"https://graph.facebook.com/{FB_VERSION}/{FB_PAGE_ID}/photos",
        data={"caption": caption, "published": "true", "access_token": FB_PAGE_TOKEN},
        files={"source": ("post.jpg", buf, "image/jpeg")},
        timeout=180,
    )
    if not r.ok:
        raise RuntimeError(f"facebook failed: HTTP {r.status_code} {r.text[:400]}")
    data = r.json()
    log("facebook post id:", data.get("post_id") or data.get("id"))


def facebook_due():
    if not (FB_PAGE_ID and FB_PAGE_TOKEN):
        return False
    return datetime.now(timezone.utc).hour % FB_EVERY_HOURS == 0


# ----------------------------------------------------------------- main
def main():
    missing = [n for n, v in (("POLLINATIONS_API_KEY", KEY), ("TELEGRAM_BOT_TOKEN", TG_TOKEN), ("TELEGRAM_CHAT_ID", TG_CHAT),
                              ("OPENROUTER_API_KEYS", ",".join(OR_KEYS))) if not v]
    if missing:
        sys.exit(f"missing env vars: {', '.join(missing)}")

    load_keystate()
    setup_models()
    try:
        run()
    finally:
        log(f"OpenRouter keys usable now: {len(keys_usable())}/{len(OR_KEYS)}")
        save_keystate()


def run():
    history = load_history()
    recent = [h["topic"] for h in history[-40:]]
    used = {h.get("source") for h in history if h.get("source")}
    try:
        post = build_post(recent, used)
    except RuntimeError as e:
        log("NO POST THIS RUN (nothing was sent):", e)
        return
    log("topic:", post["topic"])

    try:
        img = generate_image(post)
    except RuntimeError as e:
        log("NO POST THIS RUN (image failed, nothing was sent):", e)
        return
    img = overlay_text(img, post["headline_lines"], post.get("subhook", "") if SHOW_SUBHOOK else "")
    send_to_telegram(img, post)
    if facebook_due():
        try:
            send_to_facebook(img, post)
        except Exception as e:  # noqa: BLE001  (a Facebook problem must not break the Telegram run)
            log("FACEBOOK ERROR:", e)
    else:
        log("facebook skipped this hour (not configured or not due)")

    history.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "topic": post["topic"],
            "source": post.get("source_title"),
            "year": post.get("year"),
            "country": post.get("country"),
        }
    )
    save_history(history)
    log("done")


if __name__ == "__main__":
    main()
