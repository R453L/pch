#!/usr/bin/env python3
"""Pocket Change History - hourly Telegram post bot (Pollinations only)."""
import io
import json
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
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
WRITER_MODELS = _models("WRITER_MODEL", "mistral,qwen,llama")
CHECKER_MODELS = _models("CHECKER_MODEL", "mistral,qwen,llama")
IMAGE_MODELS = _models("IMAGE_MODEL", "flux")  # comma list = fallback order, e.g. "zimage,flux"
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
  image_subject: who or what is in focus. ONE or TWO anonymous adults with age range, period-correct clothing for the exact year and country, what they are doing, and one key object (a plain blank paper slip, a ledger, coins in a hand, a till drawer).
  image_scene: the place, era, time of day and simple props.
  image_composition: shot type and framing, for example "medium close-up at eye level". The main subject sits in the upper two thirds. The lower third of the frame is an empty table top, floor, counter or soft shadow.
  image_lighting: one concrete light source and direction, for example "soft window light from the left". Do not write "cinematic lighting".
- ANTI-DISTORTION: at most two people, both large and clear in the foreground. Faces in profile, three-quarter view or looking down. No crowds, no tiny background faces. Hands relaxed and simple (holding one paper, resting on a table). Avoid mirrors, reflections, glasses, clocks, complex machinery, patterned fabrics and anything with writing on it.
- The scene contains NO signs, posters, labels, newspapers, banknotes or any writing. Documents are plain blank paper.
- No real people, no real brand names or logos, no real banknote or coin designs.

HEADLINE RULES:
- headline_lines: 2 or 3 ALL-CAPS lines that together read like ONE punchy sentence with a number and a year, 10-18 words in total, each line MAX 34 characters. Example: ["IN [1936], BRITISH HOMES PAID ABOUT", "[2.4D] PER ELECTRICITY UNIT"].
- It must read like a curiosity hook, NOT a title or a summary.
- Red highlight: wrap the 1 to 3 most surprising words (a number, a price, a year) in [square brackets]. The brackets must hug the words: put a currency symbol INSIDE ("[$2,000,000]", never "$[2,000,000]") and punctuation OUTSIDE ("[1930],", never "[1930,]"). Never put a whole line in brackets. Do not use parentheses.
- subhook: 3-6 words, ALL CAPS, a truthful teaser shown under the headline (for example "ELECTRICITY WASN'T CHEAP EVERYWHERE").
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
- Plain text only. NO em dashes (use commas, periods or colons), no emojis, no markdown, no hashtags inside the text.

TOPIC RULE:
- The story must be about money, prices, wages, jobs, banks, taxes, trade, currency or business history (currency and banking history ARE allowed).
- NEVER choose topics about executions, crime and punishment, violence, war atrocities, disasters, tragedies or anything graphic or sensitive.

Return ONLY one JSON object, no markdown fences, with keys:
skip, topic, fact, year, country, headline_lines (array), subhook, image_subject, image_scene, image_composition, image_lighting, caption, hashtags (array of exactly 2 relevant topical hashtags like "#MoneyHistory"), evidence (array described above)."""

CHECKER_SYSTEM = """You are a strict fact checker. You receive SOURCE TEXT (from Wikipedia) and a DRAFT social post.
Decide whether every factual claim in the draft's headline and caption is supported by the SOURCE TEXT.
Flag ONLY hard factual problems: a number, date, amount, name or event that is not in the source; wrong cause and effect; "first/only/most/saved/ended" style claims the source does not make; numbers attached to the wrong thing; anachronisms.
Do NOT flag rhetorical questions, metaphors, transitions, mood, short summaries of what the source says, or the closing reflection. Wording like "simple" or "brutal" is not a factual claim unless it changes the facts.
Ignore the atmosphere of the paragraph that starts with "Picture" or "Imagine", but flag any number, date or name there that is not in the SOURCE TEXT.
Do NOT use outside knowledge to approve a claim. If the source does not say it, it is unsupported.
Return ONLY one JSON object, no markdown fences:
{"faithful": true or false, "unsupported": ["..."], "hook_score": 1-10}
hook_score rates how strongly the headline would stop a Facebook scroller (10 = jaw-dropping specific number and contrast, 1 = vague textbook summary)."""

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


def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON found")
    return json.loads(text[start : end + 1])


def chat(system, user, model, temperature=0.9, retries=3):
    last = None
    for i in range(retries):
        try:
            r = requests.post(
                f"{API}/v1/chat/completions",
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                },
                timeout=120,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"text call failed ({i + 1}/{retries}): {e}")
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"text generation failed: {last}")


# ----------------------------------------------------------------- content
def random_draw():
    return {
        "category": random.choice(CATEGORIES),
        "era": random.choice(ERAS),
        "country": random.choices(COUNTRIES, COUNTRY_WEIGHTS)[0],
        "angle": random.choice(ANGLES),
    }


def chat_json(system, user, models, temperature, tries=2):
    last = None
    for model in models:
        for i in range(tries):
            try:
                raw = chat(system, user, model, temperature)
            except Exception as e:  # noqa: BLE001
                last = e
                log(f"[{model}] call failed: {e}")
                break  # go to next model
            if raw and ("enough credits" in raw or "needs paid Pollen" in raw):
                last = RuntimeError("model needs paid Pollen credits")
                log(f"[{model}] needs paid Pollen credits, skipping this model")
                break
            try:
                return extract_json(raw)
            except ValueError as e:
                last = e
                snippet = (raw or "").strip().replace("\n", " ")[:300]
                log(f"[{model}] bad JSON ({i + 1}/{tries}): {e} | reply was: {snippet!r}")
    raise RuntimeError(f"no model returned valid JSON: {last}")


# ---- free evidence: Wikipedia (no API key, no cost)
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = os.environ.get("WIKI_UA", "PocketChangeHistoryBot/1.0 (educational money-history page; GitHub Actions)")
SHOW_SOURCE = os.environ.get("SHOW_SOURCE", "0") == "1"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODELS = _models("OPENROUTER_MODEL", "")
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
SENSITIVE_TITLE = re.compile(
    r"racis|nudity|murder|assassin|massacre|genocide|rape|sexual|porn|suicide|execution|terror|war crime|holocaust|slavery|rasputin|"
    r"abuse|torture|lynch|nazi|fascis|communis|genital|prostitut|drug|cocaine|heroin|opium",
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
        if len(text) >= 1500 and len(YEAR_RE.findall(text)) >= 3 and money >= 20:
            return t, text
        log(f"skipped article {t!r}: not money-focused enough ({money} money words)")
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
    user = (
        f"SOURCE ARTICLE TITLE: {title}\n\nSOURCE TEXT:\n{source}\n\n"
        f"Do NOT repeat or closely resemble any of these recent topics: {json.dumps(recent)}\n"
        "Write the post using ONLY the SOURCE TEXT. Return the JSON object now."
    )
    return chat_json(WRITER_SYSTEM, user, WRITER_MODELS, 0.7)


def check_faithful(post, source):
    draft = {"headline_lines": post["headline_lines"], "caption": post["caption"]}
    user = f"SOURCE TEXT:\n{source}\n\nDRAFT:\n{json.dumps(draft, ensure_ascii=False)}"
    return chat_json(CHECKER_SYSTEM, user, CHECKER_MODELS, 0.1)


def second_opinion(post, source):
    """Optional independent check with a free OpenRouter model (different model family = fewer shared mistakes)."""
    if not (OPENROUTER_KEY and OPENROUTER_MODELS):
        return None
    draft = {"headline_lines": post["headline_lines"], "caption": post["caption"]}
    user = f"SOURCE TEXT:\n{source}\n\nDRAFT:\n{json.dumps(draft, ensure_ascii=False)}"
    for model in OPENROUTER_MODELS:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"},
                json={"model": model, "temperature": 0.1,
                      "messages": [{"role": "system", "content": CHECKER_SYSTEM}, {"role": "user", "content": user}]},
                timeout=120,
            )
            r.raise_for_status()
            return extract_json(r.json()["choices"][0]["message"]["content"])
        except Exception as e:  # noqa: BLE001
            log(f"[openrouter {model}] second opinion unavailable: {e}")
    return None


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


def rewrap_headline(lines, width=34):
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
    if len(lines) == 1 or len(lines) > 4 or any(len(re.sub(r"[\[\]]", "", l)) > 36 for l in lines):
        lines = rewrap_headline(lines)
    if not 2 <= len(lines) <= 4:
        return f"headline has {len(lines)} lines after wrapping"
    post["headline_lines"] = lines
    words = len(" ".join(lines).split())
    if not 7 <= words <= 22:
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


def build_post(recent, used):
    for attempt in range(1, 13):
        try:
            title, source = pick_source(used)
            used.add(title)
            log(f"attempt {attempt}: source article: {title}")
            post = write_post(title, source, recent)
            if post.get("skip"):
                log("writer skipped:", post.get("reason"))
                continue
            for key in ("headline_lines", "subhook", "caption", "topic"):
                if not post.get(key):
                    raise ValueError(f"missing {key}")
            if not (post.get("image_subject") or post.get("image_prompt")):
                raise ValueError("missing image fields")
            problem = validate_post(post)
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
            second = second_opinion(post, source)
            if second is not None and not second.get("faithful", True):
                log("second opinion: unsupported claims:", second.get("unsupported"))
                continue
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
def build_image_prompt(post):
    """FLUX-friendly order: subject, scene, style, composition, lighting, quality."""

    def part(key):
        return str(post.get(key) or "").strip().rstrip(".")

    subject, scene = part("image_subject"), part("image_scene")
    if not subject:  # older style single prompt
        return (part("image_prompt") + ". " + STYLE_SUFFIX)[:1500]
    composition = part("image_composition") or "medium close-up at eye level"
    lighting = part("image_lighting") or "soft window light from the left"
    return (
        f"{subject}. {scene}. {STYLE_SUFFIX}. {composition}, vertical 4:5 frame, main subject in the upper two thirds, "
        f"the lower third is plain empty table, floor or soft shadow. {lighting}. "
        "Sharp focus, natural skin texture, relaxed natural hands, fine film grain, photorealistic, high detail. "
        "No text, no extra people, no watermarks."
    )[:1500]


def generate_image(prompt):
    last = None
    for i in range(6):
        model = IMAGE_MODELS[i % len(IMAGE_MODELS)]
        seed = random.randint(1, 10**8)
        params = f"model={quote(model)}&width={GEN_W}&height={GEN_H}&seed={seed}&nologo=true"
        if i % 3 != 2:  # every third try drops the negative prompt in case a model rejects it
            params += f"&negative_prompt={quote(NEGATIVE_PROMPT)}"
        url = f"{API}/image/{quote(prompt)}?{params}"
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {KEY}"}, timeout=240)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                img = ImageOps.fit(img, (W, H), method=Image.LANCZOS, centering=(0.5, 0.4))
                img = ImageEnhance.Contrast(img).enhance(1.06)
                img = ImageEnhance.Color(img).enhance(1.05)
                log(f"image ok with model {model}")
                return img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=65, threshold=3))
            last = f"HTTP {r.status_code} {r.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last = e
        log(f"image call failed ({i + 1}/6, model {model}): {last}")
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"image generation failed: {last}")


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
        total = lh * len(lines) + int(size * 0.62)
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
    missing = [n for n, v in (("POLLINATIONS_API_KEY", KEY), ("TELEGRAM_BOT_TOKEN", TG_TOKEN), ("TELEGRAM_CHAT_ID", TG_CHAT)) if not v]
    if missing:
        sys.exit(f"missing env vars: {', '.join(missing)}")

    history = load_history()
    recent = [h["topic"] for h in history[-40:]]
    used = {h.get("source") for h in history if h.get("source")}
    try:
        post = build_post(recent, used)
    except RuntimeError as e:
        log("NO POST THIS RUN (nothing was sent):", e)
        return
    log("topic:", post["topic"])

    img = generate_image(build_image_prompt(post))
    img = overlay_text(img, post["headline_lines"], post["subhook"])
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
