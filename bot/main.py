"""One run = find one hotel, write it up, post it to Threads, reply with details + PR link.

    python -m bot.main            # post for real
    DRY_RUN=1 python -m bot.main  # preview only (still uses Rakuten + Claude, never posts)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import images, upcoming
from .rakuten import RakutenClient, good_enough, pick_hotel
from .threads import ThreadsClient, ThreadsError
from .writer import compose_reply, shorten, write_post

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "data" / "posted.json"
JST = timezone(timedelta(hours=9))


def load_history() -> list[dict]:
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else []


def recently_posted(history: list[dict], days: int) -> set[int]:
    cutoff = datetime.now(JST) - timedelta(days=days)
    return {int(h["hotelNo"]) for h in history
            if datetime.fromisoformat(h["posted_at"]) >= cutoff}


def next_from_queue(client: RakutenClient, cfg: dict, queue: list[dict]) -> dict | None:
    """Take the next hotel from data/queue.csv (short-linked ones first). Drops dead entries."""
    for row in upcoming.next_up(queue):
        found = client.details([int(row["hotelNo"])])
        if found and good_enough(found[0], cfg):
            h = found[0]
            h["_source"] = row.get("source") or "queue"
            h["_link"] = upcoming.short_link(row)
            return h
        print(f"  {row['hotel_name']} ({row['hotelNo']}) no longer qualifies; removing from list")
        if os.environ.get("DRY_RUN", "").lower() not in ("1", "true", "yes"):
            upcoming.save(upcoming.remove(upcoming.load(), row["hotelNo"]))
    return None


def summary(md: str) -> None:
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md + "\n")


def run() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    dry = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    history = load_history()

    client = RakutenClient()
    queue = upcoming.load()
    hotel = next_from_queue(client, cfg, queue)
    if not hotel:  # list empty -> find one live, like before
        skip = recently_posted(history, cfg["repost_after_days"]) | {int(r["hotelNo"]) for r in queue}
        hotel = pick_hotel(client, cfg, skip)
    if not hotel:
        summary("No suitable hotel found this run (try adding keywords in config.yaml).")
        return 1
    print(f"Picked {hotel['hotelName']} ({hotel['hotelNo']}) via {hotel['_source']}"
          f" — link: {'your short link' if hotel.get('_link') else 'long affiliate link'}")

    written = write_post(hotel, cfg, [h.get("hook", "") for h in history])
    reply_text = compose_reply(hotel, written)
    photos = images.prepare(hotel, dry_run=dry)
    if not photos:
        summary(f"No usable photos for {hotel['hotelName']}; skipped.")
        return 1

    preview = (f"### {'[DRY RUN] ' if dry else ''}{hotel['hotelName']}\n\n"
               f"**Post:** {written['hook']}\n\n**Photos:** " + " ".join(photos) +
               f"\n\n**Reply:**\n```\n{reply_text}\n```\n")
    if dry:
        summary(preview)
        return 0

    th = ThreadsClient()
    alts = [f"{hotel['hotelName']}の写真"] * len(photos)
    try:
        post_id = th.post_images(written["hook"], photos, alts)
    except ThreadsError as e:
        print(f"Post with Rakuten photo URLs failed ({e}); retrying with re-hosted copies")
        photos = images.prepare(hotel, force_rehost=True)
        post_id = th.post_images(written["hook"], photos, alts)

    reply_id, body = None, written["body"]
    for _ in range(4):
        try:
            reply_id = th.reply(post_id, compose_reply(hotel, written, body))
            break
        except ThreadsError as e:
            print(f"Reply failed ({e}); shortening and retrying")
            body = shorten(body)
    if not reply_id:
        summary(preview + "\n**⚠ Main post went up but the reply with the link failed — add it by hand.**")

    history.append({
        "hotelNo": int(hotel["hotelNo"]), "hotelName": hotel["hotelName"],
        "hook": written["hook"], "post_id": post_id, "reply_id": reply_id,
        "permalink": th.permalink(post_id), "posted_at": datetime.now(JST).isoformat(),
    })
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1))
    upcoming.save(upcoming.remove(upcoming.load(), hotel["hotelNo"]))
    summary(preview + f"\nPosted: {history[-1]['permalink']}")
    return 0 if reply_id else 2


if __name__ == "__main__":
    sys.exit(run())
