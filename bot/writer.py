"""Writes the hook (main post) and the details (self-reply) with Claude."""
from __future__ import annotations

import json
import os
import random
import re

import anthropic

BODY_EXAMPLE = """\
【返信(詳細)の例】
TAOYA南志摩(三重・志摩)
太平洋を望むオーシャンリゾートで、食事も温泉もオールインクルーシブ。黄金色に染まる海を一望できるインフィニティ温泉が名物で、全室オーシャンビュー。近鉄・鵜方駅から車で約20分、送迎バスもあり(要予約)。"""

# --- Hook styles -------------------------------------------------------------
# "casual": sounds like a real person typing on their phone (what goes viral).
# "classic": the original, more informative AI-style hook. Used ~1 in 4 posts
# (config.yaml: casual_hook_ratio) so the feed doesn't look too uniform.

HOOK_RULES_CASUAL = """- hook: メイン投稿の1行。**12〜22文字**。20代の旅好きがスマホで友達にLINEするような、くだけた本音の一言。
  実際にバズった例(この温度感・長さ・語尾をまねる):
    伊勢志摩に行くなら、ここは絶対泊まらないで、、
    横浜に行くならここ泊まって、、
    秩父にあるこの宿エモすぎる、、
    埼玉のコスパ最強の宿見つけた、、
    岡山の温泉宿あつすぎるって、、
    ここ大分の宿おしゃれすぎるって、、
  型: 「地域名 + の/にある + この宿/温泉宿/旅館 + 感情ワード」が基本。
  感情ワードの例(毎回変える): エモすぎる / アツい・あつすぎる / おしゃれすぎる / 最強 / 優勝 / 神 / ガチで良い /
    やばい / 天国 / 好きすぎる / 見つけた / 泊まって / 行ってほしい / 帰りたくなくなる / 沼る
  - 語尾は「、、」で終える(「って、、」「すぎる、、」「見つけた、、」など)。「？」「！」「。」は使わない。
  - 設備や特徴を並べない(露天・サウナ等は入れても1語まで)。説明しない、感想だけ。
  - 漢字を少しひらがなにする(熱い→あつい 等)など、ちょっと雑な方が人間っぽい。
  - 「コスパ」「安い」はhotelMinChargeが安い(目安1万円以下)ときだけ。それ以外の事実も盛らない。
  - 宿名・絵文字・ハッシュタグ・「」は使わない。
  - 最近のフックと同じ感情ワード・同じ言い回しは避ける: {recent_hooks}"""

HOOK_RULES_CLASSIC = """- hook: メイン投稿の1行。20〜35文字程度。思わずタップしたくなる「フック」。
  逆張り(「絶対泊まらないで、、」=帰りたくなくなる)、問いかけ、地域名+驚き、比較など毎回パターンを変える。
  宿名は出さない。地域名は入れてよい。絵文字・ハッシュタグは使わない。誇張しすぎて嘘にならないこと。
  最近使ったフック(重複させない): {recent_hooks}"""

CASUAL_ENDINGS = ["、、"] * 6 + ["、、、"] * 2 + [""]   # mostly 、、 with a little randomness

SYSTEM = """あなたは楽天トラベルの宿を紹介するThreadsアカウントの中の人です。
日本語で、スマホで流し読みされても刺さる文章を書きます。
事実は必ず渡された宿データにあることだけを使い、データにないこと(送迎、全室〇〇、受賞歴、価格の安さ等)を作らないでください。"""

PROMPT = """次の宿データから、Threads投稿用の文章を作ってください。

{style}

# ルール
{hook_rules}
- area: 「都道府県(県・府・都を省略)・市町村や温泉地名」の短い表記。例: 三重・志摩 / 神奈川・箱根
- body: 宿の魅力を2〜3文 + アクセスを1文。合計{max_chars}文字以内。です・ます調ではなく体言止め・言い切り中心。
  口コミ点数や件数はbodyに書かない(後で自動で追加します)。
- 出力はJSONのみ: {{"hook": "...", "area": "...", "body": "..."}}

# 宿データ
{facts}
"""


def casualize(hook: str, rng: random.Random = random) -> str:
    """Force the casual look: no ?/!/。 at the end, finish with 、、 (sometimes 、、、 or nothing)."""
    hook = hook.strip().strip("「」")
    hook = re.sub(r"[?？!！。…、,.\s]+$", "", hook)
    hook = hook.replace("？", "、").replace("?", "、")
    return hook + rng.choice(CASUAL_ENDINGS)


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


def write_post(h: dict, cfg: dict, recent_hooks: list[str],
               client: anthropic.Anthropic | None = None,
               rng: random.Random = random) -> dict:
    client = client or anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    style = "casual" if rng.random() < cfg.get("casual_hook_ratio", 0.75) else "classic"
    rules = HOOK_RULES_CASUAL if style == "casual" else HOOK_RULES_CLASSIC
    prompt = PROMPT.format(
        style=BODY_EXAMPLE,
        hook_rules=rules.format(recent_hooks=" / ".join(recent_hooks[-10:]) or "なし"),
        max_chars=cfg["max_details_chars"],
        facts=hotel_facts(h),
    )
    print(f"  hook style: {style}")
    last_err = None
    for attempt in range(3):
        msg = client.messages.create(
            # Generous limit: newer models may "think" before answering, which uses tokens too.
            model=cfg["model"], max_tokens=4000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt if attempt == 0 else
                       prompt + "\n\n必ず {\"hook\":...,\"area\":...,\"body\":...} のJSONだけを出力してください。"}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", "") == "text")
        try:
            out = _parse_json(text)
            break
        except (ValueError, json.JSONDecodeError) as e:
            kinds = [getattr(b, "type", "?") for b in msg.content]
            print(f"  writer attempt {attempt + 1}: no usable JSON "
                  f"(stop_reason={getattr(msg, 'stop_reason', '?')}, blocks={kinds}); retrying")
            last_err = e
    else:
        raise ValueError(f"Model never returned usable JSON: {last_err}")
    hook = out["hook"].strip()
    if style == "casual":
        hook = casualize(hook, rng)
    return {"hook": hook, "area": out["area"].strip(), "body": out["body"].strip(),
            "hook_style": style}


def compose_reply(h: dict, written: dict, body: str | None = None) -> str:
    """Details comment in the page's format: name(area) / body / reviews / link #PR."""
    body = written["body"] if body is None else body
    avg = h.get("reviewAverage")
    cnt = h.get("reviewCount")
    reviews = f"口コミ{float(avg):.2f}({int(cnt)}件)。" if avg and cnt else ""
    return (f"{h['hotelName']}({written['area']})\n"
            f"{body}{reviews}\n"
            f"{h['hotelInformationUrl']} #PR")


def shorten(body: str) -> str:
    """Drop the last sentence (used if Threads rejects the reply as too long)."""
    sentences = [s for s in re.split(r"(?<=。)", body) if s]
    return "".join(sentences[:-1]) if len(sentences) > 1 else body[: max(40, len(body) * 2 // 3)]
