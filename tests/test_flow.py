"""Offline tests: fake Rakuten / Claude / Threads responses, real bot logic."""
import io
import json
import random
import types
from unittest import mock

import pytest
from PIL import Image

from bot import images, writer
from bot.rakuten import RakutenClient, pick_hotel
from bot.threads import ThreadsClient, ThreadsError

CFG = {"search_keywords": ["伊勢志摩 温泉"], "use_rankings": False, "min_review_average": 4.2,
       "min_review_count": 50, "max_details_chars": 170, "model": "x"}

HOTEL_BASIC = {"hotelNo": 186513, "hotelName": "TAOYA南志摩", "reviewAverage": 4.22,
               "reviewCount": 307, "address1": "三重県", "address2": "志摩市浜島町",
               "access": "近鉄鵜方駅より車で約20分", "hotelSpecial": "太平洋を望むオールインクルーシブ",
               "hotelImageUrl": "https://img.example/h.jpg", "roomImageUrl": "https://img.example/r.jpg",
               "hotelInformationUrl": "https://hb.afl.rakuten.co.jp/hgc/abc/?pc=x"}
LOW = dict(HOTEL_BASIC, hotelNo=1, reviewAverage=3.1)


def fv1(*hotels, extra=None):
    """Rakuten formatVersion=1 nesting: hotels[].hotel[] of single-key sections."""
    return {"hotels": [{"hotel": [{"hotelBasicInfo": h}, {"hotelRatingInfo": {"bathAverage": 4.6}},
                                  *([{"hotelFacilitiesInfo": extra}] if extra else [])]}
                       for h in hotels]}


class FakeResp:
    def __init__(self, data, status=200, content=None):
        self._d, self.status_code = data, status
        self.content = content if content is not None else json.dumps(data).encode()
        self.text = self.content.decode(errors="ignore")

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


def test_extract_both_format_versions():
    assert RakutenClient.extract_hotels(fv1(HOTEL_BASIC))[0]["bathAverage"] == 4.6
    v2 = {"hotels": [[{"hotelBasicInfo": HOTEL_BASIC}, {"hotelRatingInfo": {"roomAverage": 4.5}}]]}
    h = RakutenClient.extract_hotels(v2)[0]
    assert h["hotelName"] == "TAOYA南志摩" and h["roomAverage"] == 4.5


def test_pick_skips_low_rated_and_already_posted(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def get(url, params, timeout):
        calls.append((url, params))
        if "KeywordHotelSearch" in url:
            return FakeResp(fv1(LOW, HOTEL_BASIC, dict(HOTEL_BASIC, hotelNo=999)))
        nos = params["hotelNo"].split(",")
        return FakeResp(fv1(*[h for h in (HOTEL_BASIC,) if str(h["hotelNo"]) in nos],
                            extra={"bathType": "露天風呂"}))

    c = RakutenClient("id", "key", "aff", session=types.SimpleNamespace(get=get))
    h = pick_hotel(c, CFG, exclude={999}, rng=random.Random(0))
    assert h["hotelNo"] == 186513 and h["bathType"] == "露天風呂"
    assert calls[0][1]["accessKey"] == "key" and calls[0][1]["affiliateId"] == "aff"
    assert "1" not in calls[-1][1]["hotelNo"].split(",")  # low-rated never fetched


def test_writer_and_reply_format():
    fake = mock.Mock()
    fake.messages.create.return_value = types.SimpleNamespace(content=[types.SimpleNamespace(
        type="text", text='```json\n{"hook":"伊勢志摩に行くなら、ここは絶対泊まらないで、、",'
                          '"area":"三重・志摩","body":"太平洋を望むオーシャンリゾート。近鉄鵜方駅から車で約20分。"}\n```')])
    w = writer.write_post(HOTEL_BASIC, CFG, ["前のフック"], client=fake)
    reply = writer.compose_reply(HOTEL_BASIC, w)
    assert reply.splitlines()[0] == "TAOYA南志摩(三重・志摩)"
    assert "口コミ4.22(307件)。" in reply and reply.endswith("?pc=x #PR")
    prompt = fake.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "前のフック" in prompt and "近鉄鵜方駅" in prompt
    assert writer.shorten("一文目。二文目。三文目。") == "一文目。二文目。"


def _jpeg(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), "blue").save(b, "JPEG")
    return b.getvalue()


def test_images_keep_good_urls_and_rehost_small(monkeypatch, tmp_path):
    monkeypatch.setattr(images, "MEDIA", tmp_path)
    monkeypatch.setenv("MEDIA_BASE_URL", "https://raw.example/media")
    sizes = {"https://img.example/h.jpg": (800, 600), "https://img.example/r.jpg": (200, 150)}
    monkeypatch.setattr(images.requests, "get",
                        lambda u, timeout: FakeResp(None, content=_jpeg(*sizes[u])))
    out = images.prepare(HOTEL_BASIC, dry_run=True)
    assert out == ["https://img.example/h.jpg", "https://raw.example/media/186513_1.jpg"]
    assert Image.open(tmp_path / "186513_1.jpg").width == 640


def test_threads_carousel_then_reply(monkeypatch):
    posts = []

    def post(url, data, timeout):
        posts.append((url.rsplit("/", 1)[-1], dict(data)))
        return FakeResp({"id": f"id{len(posts)}"})

    sess = types.SimpleNamespace(post=post, get=lambda *a, **k: FakeResp({"status": "FINISHED"}))
    th = ThreadsClient("tok", "42", session=sess, wait=0)
    pid = th.post_images("hook", ["u1", "u2"])
    th.reply(pid, "details")
    kinds = [(p, d.get("media_type")) for p, d in posts]
    assert kinds == [("threads", "IMAGE"), ("threads", "IMAGE"), ("threads", "CAROUSEL"),
                     ("threads_publish", None), ("threads", "TEXT"), ("threads_publish", None)]
    assert posts[2][1]["children"] == "id1,id2" and posts[4][1]["reply_to_id"] == pid


def test_threads_error_raised():
    sess = types.SimpleNamespace(post=lambda *a, **k: FakeResp({"error": {"message": "too long"}}, 400))
    with pytest.raises(ThreadsError):
        ThreadsClient("t", "1", session=sess, wait=0).reply("x", "y")


def test_main_end_to_end(monkeypatch, tmp_path):
    from bot import main
    monkeypatch.setattr(main, "HISTORY", tmp_path / "posted.json")
    monkeypatch.setattr(main, "RakutenClient", lambda: None)
    monkeypatch.setattr(main, "pick_hotel", lambda c, cfg, ex: dict(HOTEL_BASIC, _source="kw"))
    monkeypatch.setattr(main, "write_post", lambda h, cfg, rec: {"hook": "H", "area": "三重・志摩", "body": "一。二。"})
    monkeypatch.setattr(main.images, "prepare", lambda h, **k: ["u1", "u2"])
    fake = mock.Mock()
    fake.post_images.return_value = "P1"
    fake.reply.side_effect = [ThreadsError("too long"), "R1"]  # first reply too long -> shortened
    fake.permalink.return_value = "https://threads.net/@x/post/1"
    monkeypatch.setattr(main, "ThreadsClient", lambda: fake)
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert main.run() == 0
    second_reply = fake.reply.call_args_list[1].args[1]
    assert "一。口コミ" in second_reply and "二。" not in second_reply
    hist = json.loads((tmp_path / "posted.json").read_text())
    assert hist[0]["post_id"] == "P1" and hist[0]["reply_id"] == "R1"
    monkeypatch.setenv("DRY_RUN", "1")
    fake.reset_mock()
    assert main.run() == 0 and not fake.post_images.called
