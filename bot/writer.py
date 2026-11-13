"""Writes the hook (main post) and the details (self-reply) with Claude."""
from __future__ import annotations

import json
import os
import random
import re

import anthropic


# The page's own post (the style everything must match).
STYLE_EXAMPLE = """\
TAOYA南志摩(三重・志摩)
太平洋を望むオーシャンリゾートで、食事も温泉もオールインクルーシブ。黄金色に染まる海を一望できるインフィニティ温泉が名物で、全室オーシャンビュー。近鉄・鵜方駅から車で約20分、送迎バスもあり(要予約)。"""

DEFAULT_HOOKS = ["{dest}に行くなら、ここは絶対泊まらないで、、"]
DEFAULT_EXAMPLES = ["伊勢志摩に行くなら、ここは絶対泊まらないで、、"]

# Words that make a post read as AI-written. Any of these -> rewrite.
BANNED = ["楽しめ", "ゆったり", "贅沢", "至福", "魅力", "癒し", "堪能", "極上", "非日常",
          "心ゆくまで", "ひととき", "特別な", "ぜひ", "是非", "おすすめ", "オススメ", "最高",
          "体験", "叶え", "満喫", "誘", "！", "!", "？", "?", "♪", "✨", "〜♪", "でしょう", "ませんか"]

SYSTEM = """あなたは旅行好きの個人がやっているThreadsアカウントの中の人です。
宿の紹介文を、下の例文とまったく同じ温度感・リズムで書きます。広告っぽさ、AIっぽさは厳禁。
事実は渡された宿データにあることだけを使い、データにないこと(送迎、全室〇〇、受賞歴、安さ等)は絶対に作らない。"""

PROMPT = """宿データをもとに、次の例文と同じ書き方で紹介文を書いてください。

【例文(これが手本。文体・長さ・リズムをそのまま真似る)】
{style}

# 書き方のルール
- body は3文。
  1文目: どんな宿か + 一番の売り(「〜で、〜。」の形)
  2文目: 名物の設備・お風呂・食事・部屋の特徴を具体的に(「〜が名物で、〜。」のような形)
  3文目: アクセス(駅名・車で何分・送迎など、データにある場合のみ)
- 体言止め・言い切り。です・ます調は使わない。
- 具体的な名詞を使う(「露天風呂付き客室」「源泉かけ流し」「海鮮会席」など)。ふわっとした褒め言葉は使わない。
- 次の言葉は使わない: {banned}
- 感嘆符・疑問符・絵文字・ハッシュタグは使わない。
- body は{max_chars}文字以内。口コミ点数・件数・宿名は body に書かない(自動で付けます)。
- area: 「都道府県(県・府・都は省略)・市町村や温泉地名」。例: 三重・志摩 / 神奈川・箱根 / 熊本・天草
- destination: 旅行先として一般的に呼ばれる地名を1つ(2〜6文字)。例: 伊勢志摩 / 箱根 / 天草 / 草津 / 京都
- hook: メイン投稿の1行。下の「フックの例」とまったく同じノリ(友達にLINEするようなタメ口、語尾は「、、」「、、、」、たまに🔥)で、この宿用に新しく1つ書く。
  - 地名(destination か、近くの有名スポット)を入れる。宿名は入れない。
  - 15〜30文字。ハッシュタグ・「？」・「！」は使わない。🔥以外の絵文字は使わない。
  - 宿の実際の特徴に合わせる(温泉がない宿を「温泉宿」と呼ばない、ビジネスホテルを「豪華」と言わない)。
  - 最近使ったフックと同じ言い回しにしない: {recent_hooks}

# フックの例(この人の実際の投稿)
{hook_examples}

出力はJSONのみ: {{"destination": "...", "area": "...", "body": "...", "hook": "..."}}

# 宿データ
{facts}
"""

FACT_KEYS = [
    "hotelName", "address1", "address2", "hotelSpecial", "access", "nearestStation",
    "hotelMinCharge", "reviewAverage", "serviceAverage", "roomAverage", "bathAverage",
    "breakfastAverage", "dinnerAverage", "aboutBath", "bathType", "bathQuality",
    "aboutMealPlace", "hotelFacilities", "roomFacilities", "aboutLeisure",
    "checkinTime", "checkoutTime", "privilege", "otherInformation", "userReview",
    "parkingInformation",
]


def hotel_facts(h: dict) -> str:
    facts = {}
    for k in FACT_KEYS:
        v = h.get(k)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, list):  # e.g. [{"item": "露天風呂"}, ...]
            v = "、".join(str(x.get("item", x)) if isinstance(x, dict) else str(x) for x in v)
        facts[k] = str(v)[:400]
    return json.dumps(facts, ensure_ascii=False, indent=1)


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"Model did not return JSON: {text[:200]}")
    return json.loads(m.group(0))


def _destination(h: dict, suggested: str) -> str:
    d = (suggested or "").strip()
    if 2 <= len(d) <= 8 and not re.search(r"[\s、。・,()（）!?！？]", d):
        return d
    src = h.get("_source", "")
    if src.startswith("kw:"):
        return src[3:].split()[0]
    pref = h.get("address1", "")
    return pref[:-1] if pref.endswith(("県", "府")) or (pref.endswith("都") and pref != "京都") else pref


def good_hook(hook: str, h: dict, recent_hooks: list[str]) -> bool:
    """Hook must look like the page's own: short, casual ending, no hotel name, not a repeat."""
    return (8 <= len(hook) <= 40
            and hook.rstrip().endswith(("、、", "🔥"))
            and not re.search(r"[#＃?？!！\n]", hook)
            and h.get("hotelName", "@@") not in hook
            and hook not in recent_hooks[-30:])


def find_banned(text: str) -> list[str]:
    return [w for w in BANNED if w in text]


def write_post(h: dict, cfg: dict, recent_hooks: list[str],
               client: anthropic.Anthropic | None = None,
               rng: random.Random | None = None) -> dict:
    client = client or anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    rng = rng or random.Random()
    prompt = PROMPT.format(
        style=STYLE_EXAMPLE,
        banned="、".join(w for w in BANNED if len(w) > 1),
        max_chars=cfg["max_details_chars"],
        facts=hotel_facts(h),
        recent_hooks=" / ".join(recent_hooks[-8:]) or "なし",
        hook_examples="\n".join(cfg.get("hook_examples") or DEFAULT_EXAMPLES),
    )
    out, extra = None, ""
    for attempt in range(4):
        msg = client.messages.create(
            # Generous limit: newer models may "think" before answering, which uses tokens too.
            model=cfg["model"], max_tokens=4000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt + extra}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", "") == "text")
        try:
            cand = _parse_json(text)
            body = cand["body"].strip()
        except (ValueError, KeyError, json.JSONDecodeError):
            kinds = [getattr(b, "type", "?") for b in msg.content]
            print(f"  writer attempt {attempt + 1}: no usable JSON "
                  f"(stop_reason={getattr(msg, 'stop_reason', '?')}, blocks={kinds}); retrying")
            extra = '\n\n必ず {"destination":...,"area":...,"body":...,"hook":...} のJSONだけを出力してください。'
            continue
        out = cand
        bad = find_banned(body)
        too_long = len(body) > cfg["max_details_chars"] + 20
        if not bad and not too_long:
            break
        print(f"  writer attempt {attempt + 1}: rewriting (banned={bad}, length={len(body)})")
        extra = (f"\n\n前回の文章: {body}\n"
                 f"直すこと: " + (f"「{'」「'.join(bad)}」を使わない。" if bad else "")
                 + (f"{cfg['max_details_chars']}文字以内にする。" if too_long else "")
                 + "例文と同じ素っ気ないくらいの言い切りで書き直す。")
    if out is None:
        raise ValueError("Model never returned usable JSON")

    dest = _destination(h, out.get("destination", ""))
    hook = (out.get("hook") or "").strip()
    if not good_hook(hook, h, recent_hooks):
        if hook:
            print(f"  hook rejected ({hook!r}); using a backup template")
        templates = cfg.get("hook_templates") or DEFAULT_HOOKS
        fresh = [t for t in templates if t.format(dest=dest) not in recent_hooks[-8:]] or templates
        hook = rng.choice(fresh).format(dest=dest)
    return {"hook": hook, "area": out["area"].strip(), "body": out["body"].strip()}


def compose_reply(h: dict, written: dict, body: str | None = None) -> str:
    """Details comment in the page's format: name(area) / body / reviews / link #PR."""
    body = written["body"] if body is None else body
    avg = h.get("reviewAverage")
    cnt = h.get("reviewCount")
    reviews = f"口コミ{float(avg):.2f}({int(cnt)}件)。" if avg and cnt else ""
    return (f"{h['hotelName']}({written['area']})\n"
            f"{body}{reviews}\n"
            f"{h.get('_link') or h['hotelInformationUrl']} #PR")


def shorten(body: str) -> str:
    """Drop the last sentence (used if Threads rejects the reply as too long)."""
    sentences = [s for s in re.split(r"(?<=。)", body) if s]
    return "".join(sentences[:-1]) if len(sentences) > 1 else body[: max(40, len(body) * 2 // 3)]
