"""Writes the hook (main post) and the details (self-reply) with Claude."""
from __future__ import annotations

import json
import os
import re

import anthropic

STYLE_EXAMPLE = """\
【メイン投稿(フック)の例】
伊勢志摩に行くなら、ここは絶対泊まらないで、、

【返信(詳細)の例】
TAOYA南志摩(三重・志摩)
太平洋を望むオーシャンリゾートで、食事も温泉もオールインクルーシブ。黄金色に染まる海を一望できるインフィニティ温泉が名物で、全室オーシャンビュー。近鉄・鵜方駅から車で約20分、送迎バスもあり(要予約)。"""

SYSTEM = """あなたは楽天トラベルの宿を紹介するThreadsアカウントの中の人です。
日本語で、スマホで流し読みされても刺さる文章を書きます。
事実は必ず渡された宿データにあることだけを使い、データにないこと(送迎、全室〇〇、受賞歴、価格の安さ等)を作らないでください。"""

PROMPT = """次の宿データから、Threads投稿用の文章を作ってください。

{style}

# ルール
- hook: メイン投稿の1行。20〜35文字程度。思わずタップしたくなる「フック」。
  逆張り(「絶対泊まらないで、、」=帰りたくなくなる)、問いかけ、地域名+驚き、比較など毎回パターンを変える。
  宿名は出さない。地域名は入れてよい。絵文字・ハッシュタグは使わない。誇張しすぎて嘘にならないこと。
  最近使ったフック(重複させない): {recent_hooks}
- area: 「都道府県(県・府・都を省略)・市町村や温泉地名」の短い表記。例: 三重・志摩 / 神奈川・箱根
- body: 宿の魅力を2〜3文 + アクセスを1文。合計{max_chars}文字以内。です・ます調ではなく体言止め・言い切り中心。
  口コミ点数や件数はbodyに書かない(後で自動で追加します)。
- 出力はJSONのみ: {{"hook": "...", "area": "...", "body": "..."}}

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


def write_post(h: dict, cfg: dict, recent_hooks: list[str],
               client: anthropic.Anthropic | None = None) -> dict:
    client = client or anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = PROMPT.format(
        style=STYLE_EXAMPLE,
        recent_hooks=" / ".join(recent_hooks[-10:]) or "なし",
        max_chars=cfg["max_details_chars"],
        facts=hotel_facts(h),
    )
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
    return {"hook": out["hook"].strip(), "area": out["area"].strip(), "body": out["body"].strip()}


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
