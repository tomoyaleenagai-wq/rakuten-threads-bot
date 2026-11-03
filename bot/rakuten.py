"""Rakuten Travel (Rakuten Web Service) client: finds a hotel worth posting."""
from __future__ import annotations

import os
import random
import time
from typing import Any

import requests

BASE = "https://openapi.rakuten.co.jp/engine/api/Travel"
KEYWORD_SEARCH = f"{BASE}/KeywordHotelSearch/20260731"
SIMPLE_SEARCH = f"{BASE}/SimpleHotelSearch/20260731"
RANKING = f"{BASE}/HotelRanking/20170426"


class RakutenClient:
    def __init__(self, app_id: str | None = None, access_key: str | None = None,
                 affiliate_id: str | None = None, session: requests.Session | None = None):
        self.app_id = app_id or os.environ["RAKUTEN_APP_ID"]
        self.access_key = access_key or os.environ["RAKUTEN_ACCESS_KEY"]
        self.affiliate_id = affiliate_id or os.environ["RAKUTEN_AFFILIATE_ID"]
        self.http = session or requests.Session()
        # If the Rakuten app restricts "Allowed websites", present that site as the origin.
        site = os.environ.get("RAKUTEN_ALLOWED_SITE", "").strip()
        if site and hasattr(self.http, "headers"):
            self.http.headers.update({"Referer": site, "Origin": site.rstrip("/")})

    # -- low level -------------------------------------------------------
    def _get(self, url: str, **params: Any) -> dict:
        params = {
            "applicationId": self.app_id,
            "accessKey": self.access_key,
            "affiliateId": self.affiliate_id,
            "format": "json",
            **params,
        }
        for attempt in range(4):
            r = self.http.get(url, params=params, timeout=30)
            if r.status_code == 404:  # Rakuten returns 404 for "no results"
                return {}
            if r.status_code in (429, 500, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Rakuten API {r.status_code}: {r.text[:300]}")
            time.sleep(1.1)  # stay under 1 req/sec
            return r.json()
        return {}

    # -- parsing ---------------------------------------------------------
    @staticmethod
    def extract_hotels(data: dict) -> list[dict]:
        """Flatten Rakuten's nested hotel records (formatVersion 1 or 2) into dicts."""
        out = []
        for entry in data.get("hotels", []) or []:
            parts = entry.get("hotel", []) if isinstance(entry, dict) else entry
            if isinstance(parts, dict):
                parts = [parts]
            merged: dict = {}
            for part in parts or []:
                if not isinstance(part, dict):
                    continue
                for v in part.values():
                    if isinstance(v, dict):
                        merged.update(v)
                    elif isinstance(v, list):  # formatVersion=1 wraps sections in lists
                        for x in v:
                            if isinstance(x, dict):
                                merged.update(x)
            if merged.get("hotelNo"):
                out.append(merged)
        return out

    @staticmethod
    def _walk_hotel_nos(obj: Any) -> list[int]:
        found = []
        if isinstance(obj, dict):
            if "hotelNo" in obj:
                found.append(int(obj["hotelNo"]))
            for v in obj.values():
                found += RakutenClient._walk_hotel_nos(v)
        elif isinstance(obj, list):
            for v in obj:
                found += RakutenClient._walk_hotel_nos(v)
        return found

    # -- searches --------------------------------------------------------
    def keyword_search(self, keyword: str, page: int = 1) -> list[dict]:
        data = self._get(KEYWORD_SEARCH, keyword=keyword, hits=30, page=page,
                         responseType="middle", hotelThumbnailSize=3)
        return self.extract_hotels(data)

    def ranking_hotel_nos(self, genre: str) -> list[int]:
        data = self._get(RANKING, genre=genre)
        return list(dict.fromkeys(self._walk_hotel_nos(data)))

    def details(self, hotel_nos: list[int]) -> list[dict]:
        """Full ('large') records for up to 15 hotels at once."""
        res = []
        for i in range(0, len(hotel_nos), 15):
            chunk = ",".join(str(n) for n in hotel_nos[i:i + 15])
            data = self._get(SIMPLE_SEARCH, hotelNo=chunk, responseType="large",
                             hotelThumbnailSize=3)
            res += self.extract_hotels(data)
        return res


def good_enough(h: dict, cfg: dict) -> bool:
    try:
        avg = float(h.get("reviewAverage") or 0)
        cnt = int(h.get("reviewCount") or 0)
    except (TypeError, ValueError):
        return False
    return (avg >= cfg["min_review_average"] and cnt >= cfg["min_review_count"]
            and bool(h.get("hotelImageUrl")) and bool(h.get("hotelInformationUrl")))


def pick_hotel(client: RakutenClient, cfg: dict, exclude: set[int],
               rng: random.Random | None = None) -> dict | None:
    """Try random keyword searches (and rankings) until a fresh, well-reviewed hotel turns up."""
    rng = rng or random.Random()
    sources: list[tuple[str, str]] = [("kw", k) for k in cfg["search_keywords"]]
    if cfg.get("use_rankings"):
        sources += [("rank", g) for g in cfg.get("ranking_genres", [])]
    rng.shuffle(sources)

    for kind, value in sources[:8]:
        if kind == "kw":
            pool = client.keyword_search(value, page=rng.randint(1, 2))
            pool = [h for h in pool if int(h["hotelNo"]) not in exclude and good_enough(h, cfg)]
            nos = [int(h["hotelNo"]) for h in pool]
        else:
            nos = [n for n in client.ranking_hotel_nos(value) if n not in exclude]
        if not nos:
            continue
        rng.shuffle(nos)
        for h in client.details(nos[:15]):
            if good_enough(h, cfg):
                h["_source"] = f"{kind}:{value}"
                return h
    return None
