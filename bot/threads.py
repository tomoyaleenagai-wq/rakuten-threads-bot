"""Threads Graph API: carousel post + reply to yourself."""
from __future__ import annotations

import os
import time

import requests

GRAPH = "https://graph.threads.net/v1.0"


class ThreadsError(RuntimeError):
    pass


class ThreadsClient:
    def __init__(self, token: str | None = None, user_id: str | None = None,
                 session: requests.Session | None = None, wait: float = 30):
        self.token = token or os.environ["THREADS_ACCESS_TOKEN"]
        self.user_id = user_id or os.environ.get("THREADS_USER_ID") or "me"
        self.http = session or requests.Session()
        self.wait = wait  # Meta recommends ~30s between container creation and publish

    def _post(self, path: str, **params) -> dict:
        params["access_token"] = self.token
        r = self.http.post(f"{GRAPH}/{path}", data=params, timeout=60)
        data = r.json() if r.content else {}
        if r.status_code >= 400 or "error" in data:
            raise ThreadsError(f"{path} -> {r.status_code}: {data.get('error', data)}")
        return data

    def _status(self, container_id: str) -> str:
        r = self.http.get(f"{GRAPH}/{container_id}",
                          params={"fields": "status,error_message", "access_token": self.token},
                          timeout=30)
        d = r.json()
        if d.get("status") == "ERROR":
            raise ThreadsError(f"container {container_id} failed: {d.get('error_message')}")
        return d.get("status", "")

    def _wait_ready(self, container_id: str) -> None:
        time.sleep(self.wait)
        for _ in range(10):
            if self._status(container_id) in ("FINISHED", ""):
                return
            time.sleep(10)

    def _publish(self, creation_id: str) -> str:
        self._wait_ready(creation_id)
        return self._post(f"{self.user_id}/threads_publish", creation_id=creation_id)["id"]

    def post_images(self, text: str, image_urls: list[str], alt_texts: list[str] | None = None) -> str:
        """Post 1 image or a 2–20 image carousel. Returns the published post id."""
        alt_texts = alt_texts or [""] * len(image_urls)
        if len(image_urls) == 1:
            cid = self._post(f"{self.user_id}/threads", media_type="IMAGE",
                             image_url=image_urls[0], text=text, alt_text=alt_texts[0])["id"]
            return self._publish(cid)
        children = []
        for url, alt in zip(image_urls, alt_texts):
            children.append(self._post(f"{self.user_id}/threads", media_type="IMAGE",
                                       image_url=url, is_carousel_item="true", alt_text=alt)["id"])
        for c in children:
            self._status(c)  # surface image fetch errors early
        cid = self._post(f"{self.user_id}/threads", media_type="CAROUSEL",
                         children=",".join(children), text=text)["id"]
        return self._publish(cid)

    def reply(self, to_id: str, text: str) -> str:
        cid = self._post(f"{self.user_id}/threads", media_type="TEXT", text=text,
                         reply_to_id=to_id)["id"]
        return self._publish(cid)

    def permalink(self, media_id: str) -> str:
        r = self.http.get(f"{GRAPH}/{media_id}",
                          params={"fields": "permalink", "access_token": self.token}, timeout=30)
        return r.json().get("permalink", "")
