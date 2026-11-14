"""Picks the listing photos and makes sure Threads can use them.

Threads needs public JPEG/PNG URLs, 320–1440px wide. Rakuten's own photo URLs are
used as-is when they qualify; otherwise (or when force_rehost=True) the photo is
resized, committed to this repo's media/ folder and served from GitHub.
"""
from __future__ import annotations

import io
import os
import subprocess
import time
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "media"


def listing_photos(h: dict) -> list[str]:
    urls = [h.get("hotelImageUrl"), h.get("roomImageUrl")]
    return list(dict.fromkeys(u for u in urls if u))


def _fits(img: Image.Image, fmt: str | None) -> bool:
    return fmt in ("JPEG", "PNG") and 320 <= img.width <= 1440 and img.height / img.width <= 10


def _normalize(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    if img.width < 640:
        s = 640 / img.width
    elif img.width > 1440:
        s = 1440 / img.width
    else:
        return img
    return img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)


def _media_base_url() -> str | None:
    if os.environ.get("MEDIA_BASE_URL"):
        return os.environ["MEDIA_BASE_URL"].rstrip("/")
    repo, branch = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/media" if repo else None


def _git_push(paths: list[Path]) -> None:
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, check=True)  # noqa: E731
    run("add", *[str(p) for p in paths])
    # Same photo already saved by an earlier run -> nothing new to commit, it's already online.
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    run("commit", "-m", "media for next post")
    try:
        run("push")
    except subprocess.CalledProcessError:  # repo moved on meanwhile (e.g. you edited a file)
        run("pull", "--rebase", "--autostash")
        run("push")
    time.sleep(10)  # let raw.githubusercontent.com catch up


def prepare(h: dict, force_rehost: bool = False, dry_run: bool = False) -> list[str]:
    ready, to_push = [], []
    for i, url in enumerate(listing_photos(h)):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content))
            fmt = img.format
        except Exception as e:  # broken/missing photo: skip it
            print(f"  skip photo {url}: {e}")
            continue
        if _fits(img, fmt) and not force_rehost:
            ready.append(url)
            continue
        base = _media_base_url()
        if not base:
            print(f"  photo {url} needs re-hosting but no MEDIA_BASE_URL/GITHUB_REPOSITORY; skipping")
            continue
        MEDIA.mkdir(exist_ok=True)
        path = MEDIA / f"{h['hotelNo']}_{i}.jpg"
        _normalize(img).save(path, "JPEG", quality=90)
        to_push.append(path)
        ready.append(f"{base}/{path.name}")
    if to_push and not dry_run:
        _git_push(to_push)
    return ready
