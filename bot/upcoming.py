"""Upcoming-hotels list (data/queue.csv) so you can add your own a.r10.to short links.

Each line:  hotelNo,hotel_name,hotel_page,short_link
Paste your short link after the last comma. Lines without one still get posted,
using the normal long affiliate link.

    python -m bot.upcoming       # top the list up to `queue_size` hotels
"""
from __future__ import annotations

import csv
import io
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "data" / "queue.csv"
FIELDS = ["hotelNo", "hotel_name", "hotel_page", "short_link", "source"]
SHORT_LINK = re.compile(r"^https?://\S+$")


def hotel_page(hotel_no: int | str) -> str:
    """Plain Rakuten Travel page — what you paste into Rakuten's link tool."""
    return f"https://travel.rakuten.co.jp/HOTEL/{hotel_no}/{hotel_no}.html"


def load(path: Path | None = None) -> list[dict]:
    path = path or QUEUE
    if not path.exists():
        return []
    rows = []
    for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))):
        no = (row.get("hotelNo") or "").strip()
        if not no.isdigit():
            continue
        rows.append({k: (row.get(k) or "").strip() for k in FIELDS})
    return rows


def save(rows: list[dict], path: Path | None = None) -> None:
    path = path or QUEUE
    buf = io.StringIO()
    # short_link is the LAST column, so you just paste after the final comma.
    cols = ["hotelNo", "hotel_name", "source", "hotel_page", "short_link"]
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "hotel_name": r["hotel_name"].replace(",", "、")})
    path.parent.mkdir(exist_ok=True)
    path.write_text(buf.getvalue(), encoding="utf-8")


def short_link(row: dict) -> str | None:
    link = row.get("short_link", "").strip()
    return link if SHORT_LINK.match(link) else None


def next_up(rows: list[dict]) -> list[dict]:
    """Posting order: hotels you've added a short link to first, then the rest."""
    return [r for r in rows if short_link(r)] + [r for r in rows if not short_link(r)]


def remove(rows: list[dict], hotel_no: int | str) -> list[dict]:
    return [r for r in rows if r["hotelNo"] != str(hotel_no)]


def fill(client, cfg: dict, exclude: set[int], rows: list[dict]) -> list[dict]:
    from .rakuten import pick_hotel

    target = int(cfg.get("queue_size", 35))
    taken = exclude | {int(r["hotelNo"]) for r in rows}
    misses = 0
    while len(rows) < target and misses < 5:
        h = pick_hotel(client, cfg, taken)
        if not h:
            misses += 1
            continue
        no = int(h["hotelNo"])
        taken.add(no)
        rows.append({"hotelNo": str(no), "hotel_name": h["hotelName"], "source": h.get("_source", ""),
                     "hotel_page": hotel_page(no), "short_link": ""})
        print(f"  queued {h['hotelName']} ({no})")
    return rows


def main() -> int:
    from .main import load_history, recently_posted
    from .rakuten import RakutenClient

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    rows = load()
    before = len(rows)
    rows = fill(RakutenClient(), cfg, recently_posted(load_history(), cfg["repost_after_days"]), rows)
    save(rows)
    print(f"Queue: {before} -> {len(rows)} hotels "
          f"({sum(1 for r in rows if short_link(r))} with short links)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
