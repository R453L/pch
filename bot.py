#!/usr/bin/env python3
"""Pocket Change History - hourly Telegram post bot (Pollinations only)."""
import io
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFont

API = "https://gen.pollinations.ai"
KEY = os.environ.get("POLLINATIONS_API_KEY", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TEXT_MODEL = os.environ.get("TEXT_MODEL", "openai")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "flux")
SEND_NOTES = os.environ.get("SEND_NOTES", "1") == "1"
WATERMARK = os.environ.get("WATERMARK", "AI-generated illustration")

HERE = Path(__file__).parent
HISTORY = HERE / "history.json"
FONT_PATH = HERE / "fonts" / "Anton-Regular.ttf"
FALLBACK_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1080, 1350

WHITE, RED, GOLD = (255, 255, 255), (226, 38, 38), (240, 190, 90)

CATEGORIES = [
    "old prices of everyday items (bread, milk, cars, houses, movie tickets)",
    "old wages and strange old jobs",
    "how a famous company actually started",
    "early banks, currency, coins, banknotes",
    "first credit cards, ATMs, cash registers, vending machines",
    "old shops, markets, department stores, general stores",
    "early advertising and old product pricing",
    "strange taxes and forgotten laws about money or trade",
    "old money customs and payment methods",
    "things that disappeared from everyday economic life",
]
ERAS = ["1880s", "1900s", "1910s", "1920s", "1930s", "1940s", "1950s", "1960s", "1970s", "1980s"]
COUNTRIES = ["USA", "UK", "Canada", "Australia", "Ireland", "Germany", "France", "Japan", "India", "Brazil", "Mexico", "Italy"]
COUNTRY_WEIGHTS = [5, 5, 2, 2, 1, 1, 1, 1, 1, 1, 1, 1]
ANGLES = ["a price", "a wage", "a job", "an object", "a law", "a company origin", "a payment method", "a shop type"]
BANNED = (
    "vending machines, the first credit card, the first ATM, Coca-Cola, the Ford Model T, "
    "Henry Ford's $5 day, Great Depression bread lines, the Monopoly game, the first cash register, "
    "McDonald's, Apple, Amazon"
)

WRITER_SYSTEM = f"""You are an expert Economic & Business Historian, fact checker and viral Facebook history copywriter.
You create ORIGINAL posts about the history of money, prices, wages, jobs, banks, companies and everyday economic life.

FACT RULES (most important):
- NEVER invent facts. Only use facts you are highly confident are well documented.
- Every number needs a year and a country. Never present inflation-adjusted figures as original prices.
- For "first ever" claims, say "one of the earliest" if disputed. Company origin myths must be treated carefully.
- If you cannot find a solid, well-documented fact for the requested draw, return {{"skip": true, "reason": "..."}}.
- Never choose these topics: {BANNED}.

IMAGE RULES:
- image_prompt describes ONLY the scene: anonymous people, shopfronts, counters, objects, machines, with generic period signage.
- No real people, no real brand names or logos, no real banknote or coin designs, no readable text in the scene.
- One strong focal point, close or medium shot, dramatic natural light, a tangible curiosity object large in frame, a clear human moment.
- Keep the main subject in the top 65% of the frame; the bottom 30% must be calm and dark-friendly.

HEADLINE RULES:
- headline_lines: 3 or 4 short ALL-CAPS lines, 8-18 words total. Wrap the 1-4 most surprising words (numbers, years) in [square brackets] for red highlight. Do not put the whole headline in brackets.
- subhook: 3-6 words, ALL CAPS, truthful (e.g. "THE REASON IS WILD").
- The headline must be truthful and supported by the fact.

CAPTION RULES:
- 100-150 words, natural American English, conversational, documentary, slightly mysterious. The first sentence creates curiosity.
- Explain what is shown, when, where, why it existed, context, and why it is interesting today.
- Do not repeat the headline word-for-word. No filler. No unsupported phrases like "Experts believe" or "Everyone used".
- Use precise wording ("In parts of Britain...", "By the 1920s...", "According to surviving records...").
- Plain text only, no emojis, no hashtags, no markdown.

Return ONLY one JSON object, no markdown fences, with keys:
skip, topic, fact, year, country, headline_lines (array), subhook, image_prompt, caption, sources (array of 2-4 source types/names), confidence (Confirmed|Probable|Disputed)."""

CHECKER_SYSTEM = """You are a strict, skeptical history fact checker. You receive a draft social post.
Check every date, number, country, name and claim against what is reliably documented.
Be harsh: viral myths, rounded-up numbers and invented details must be flagged.
Return ONLY one JSON object, no markdown fences:
{"verdict": "confirmed|probable|disputed|unverifiable", "issues": ["..."], "fixed_headline_lines": null or array, "fixed_caption": null or string}
Use fixed_* only when a small correction makes the post accurate. Use "disputed" or "unverifiable" if the core fact is doubtful."""

STYLE_SUFFIX = (
    ", vertical 4:5 documentary photograph, muted colorized archive look, faded earthy tones, soft film grain, "
    "slightly desaturated, authentic period details, natural dramatic window light, shallow depth of field, "
    "realistic faces and anatomy, imperfect vintage exposure, subject in the upper two thirds, "
    "empty dark calm area at the bottom, no text, no letters, no logos, no watermark"
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


def chat(system, user, temperature=0.9, retries=3):
    last = None
    for i in range(retries):
        try:
            r = requests.post(
                f"{API}/v1/chat/completions",
                headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                json={
                    "model": TEXT_MODEL,
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


def write_post(draw, recent):
    user = (
        "RANDOM DRAW (follow it):\n"
        f"- Category: {draw['category']}\n- Era: {draw['era']}\n- Country: {draw['country']}\n- Angle: {draw['angle']}\n\n"
        "Find ONE specific, well-documented fact that fits this draw. "
        "If nothing fits, you may shift the country or era slightly, but stay close to the draw.\n"
        f"Do NOT repeat or closely resemble any of these recent topics: {json.dumps(recent)}\n"
        "Return the JSON object now."
    )
    return extract_json(chat(WRITER_SYSTEM, user, temperature=0.9))


def check_post(post):
    draft = {k: post.get(k) for k in ("topic", "fact", "year", "country", "headline_lines", "caption")}
    return extract_json(chat(CHECKER_SYSTEM, json.dumps(draft, ensure_ascii=False), temperature=0.2))


def build_post(recent):
    for attempt in range(1, 6):
        draw = random_draw()
        log(f"attempt {attempt}: {draw}")
        try:
            post = write_post(draw, recent)
            if post.get("skip"):
                log("writer skipped:", post.get("reason"))
                continue
            for key in ("headline_lines", "subhook", "image_prompt", "caption", "topic"):
                if not post.get(key):
                    raise ValueError(f"missing {key}")
            verdict = check_post(post)
            log("verdict:", verdict.get("verdict"), verdict.get("issues"))
            if verdict.get("verdict") not in ("confirmed", "probable"):
                continue
            if verdict.get("fixed_headline_lines"):
                post["headline_lines"] = verdict["fixed_headline_lines"]
            if verdict.get("fixed_caption"):
                post["caption"] = verdict["fixed_caption"]
            post["confidence"] = verdict["verdict"].capitalize()
            post["issues"] = verdict.get("issues") or []
            post["draw"] = draw
            return post
        except Exception as e:  # noqa: BLE001
            log("attempt failed:", e)
    raise RuntimeError("could not build a verified post after 5 attempts")


# ----------------------------------------------------------------- image
def generate_image(prompt):
    full = (prompt.strip().rstrip(".") + STYLE_SUFFIX)[:1500]
    last = None
    for i in range(4):
        seed = random.randint(1, 10**8)
        url = f"{API}/image/{quote(full)}?model={IMAGE_MODEL}&width={W}&height={H}&seed={seed}&nologo=true"
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {KEY}"}, timeout=240)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                return img.resize((W, H), Image.LANCZOS) if img.size != (W, H) else img
            last = f"HTTP {r.status_code} {r.text[:200]}"
        except Exception as e:  # noqa: BLE001
            last = e
        log(f"image call failed ({i + 1}/4): {last}")
        time.sleep(6 * (i + 1))
    raise RuntimeError(f"image generation failed: {last}")


def load_font(size):
    path = FONT_PATH if FONT_PATH.exists() else FALLBACK_FONT
    return ImageFont.truetype(str(path), size)


def parse_segments(line):
    segs = []
    for part in re.split(r"(\[[^\]]+\])", line.strip()):
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            segs.append((part[1:-1], RED))
        else:
            segs.append((part, WHITE))
    return segs


def overlay_text(img, headline_lines, subhook):
    img = img.convert("RGB")
    # bottom gradient
    grad_h = int(H * 0.46)
    grad = Image.new("L", (W, grad_h))
    gd = ImageDraw.Draw(grad)
    for y in range(grad_h):
        t = y / (grad_h - 1)
        gd.line([(0, y), (W, y)], fill=int(245 * (t**1.35)))
    black = Image.new("RGB", (W, grad_h), (0, 0, 0))
    img.paste(black, (0, H - grad_h), grad)

    lines = [parse_segments(l.upper()) for l in headline_lines if l.strip()]
    max_w, max_h = W - 120, int(H * 0.34)
    size = 112
    while size > 36:
        font = load_font(size)
        sub_font = load_font(int(size * 0.5))
        lh = int(size * 1.1)
        widths = [sum(font.getlength(t) for t, _ in segs) for segs in lines]
        total = lh * len(lines) + int(size * 0.5) + int(size * 0.42)
        if max(widths) <= max_w and total <= max_h:
            break
        size -= 4

    draw = ImageDraw.Draw(img)
    y = H - 80 - total
    for segs, width in zip(lines, widths):
        x = (W - width) / 2
        for text, color in segs:
            draw.text((x + 3, y + 4), text, font=font, fill=(0, 0, 0))  # shadow
            draw.text((x, y), text, font=font, fill=color)
            x += font.getlength(text)
        y += lh
    sub = subhook.upper().strip()
    sw = sub_font.getlength(sub)
    sy = y + int(size * 0.12)
    draw.text(((W - sw) / 2 + 2, sy + 3), sub, font=sub_font, fill=(0, 0, 0))
    draw.text(((W - sw) / 2, sy), sub, font=sub_font, fill=GOLD)

    if WATERMARK:
        wm_font = load_font(22)
        ww = wm_font.getlength(WATERMARK)
        draw.text((W - ww - 28, H - 40), WATERMARK, font=wm_font, fill=(215, 215, 215))
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
    caption = post["caption"].strip()
    if len(caption) <= 1024:
        tg("sendPhoto", data={"chat_id": TG_CHAT, "caption": caption}, files={"photo": ("post.jpg", buf)})
    else:
        tg("sendPhoto", data={"chat_id": TG_CHAT}, files={"photo": ("post.jpg", buf)})
        tg("sendMessage", data={"chat_id": TG_CHAT, "text": caption[:4000]})
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


# ----------------------------------------------------------------- main
def main():
    missing = [n for n, v in (("POLLINATIONS_API_KEY", KEY), ("TELEGRAM_BOT_TOKEN", TG_TOKEN), ("TELEGRAM_CHAT_ID", TG_CHAT)) if not v]
    if missing:
        sys.exit(f"missing env vars: {', '.join(missing)}")

    history = load_history()
    recent = [h["topic"] for h in history[-40:]]
    post = build_post(recent)
    log("topic:", post["topic"])

    img = generate_image(post["image_prompt"])
    img = overlay_text(img, post["headline_lines"], post["subhook"])
    send_to_telegram(img, post)

    history.append(
        {
            "ts": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "topic": post["topic"],
            "year": post.get("year"),
            "country": post.get("country"),
        }
    )
    save_history(history)
    log("done")


if __name__ == "__main__":
    main()
