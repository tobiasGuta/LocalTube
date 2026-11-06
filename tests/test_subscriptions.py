"""v0.12 local subscriptions; no live YouTube or Google account access."""
from __future__ import annotations

import json
from fastapi.testclient import TestClient

import app as localtube
import discovery
import learning_ranker as ml
import subscriptions as subs


A = "UC" + "A" * 22
B = "UC" + "B" * 22
VID1 = "a" * 11
VID2 = "b" * 11
VID3 = "c" * 11


def subscribe(client, channel_id=A, video_id=VID1, name="Creator"):
    return client.post("/api/subscriptions", json={
        "video_id": video_id, "channel_id": channel_id,
        "channel_name": name,
    })


def test_channel_id_is_stable_not_handle_or_display_name():
    assert discovery.verified_channel_id({"channel_id": A}) == A
    assert discovery.verified_channel_id(
        {"channel_url": "https://www.youtube.com/channel/" + A + "/videos"}
    ) == A
    assert discovery.verified_channel_id({"uploader_id": B}) == B
    assert discovery.verified_channel_id({"channel_id": "@same_name"}) is None
    assert discovery.verified_channel_id({"channel_id": "UCshort"}) is None
    assert discovery.verified_channel_id({
        "channel_url": "https://youtube.com/@similar_creator"}) is None
    v = discovery.public_video({
        "id": VID1, "title": "Actual channel upload",
        "channel": "Any Channel", "channel_id": A,
        "upload_date": "20261102",
        "duration": 100,
    })
    assert v["channel_id"] == A
    assert v["upload_date"] == "20261102"
    assert discovery.public_video({"id": "invalid", "title": "X"}) is None


def test_subscribe_unsubscribe_are_local_idempotent_and_id_keyed(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "id.db")
    monkeypatch.setattr(localtube, "resolve_video_channel", lambda _: (
        (_ for _ in ()).throw(AssertionError("Do not access remote video"))))
    with TestClient(localtube.app) as client:
        assert client.get("/api/subscriptions").json() == []
        first = subscribe(client, A, VID1, "Same Display")
        assert first.status_code == 200, first.text
        stamp = first.json()["subscribed_at"]
        again = subscribe(client, A, VID1, "Creator renamed")
        assert again.status_code == 200
        assert again.json()["subscribed_at"] == stamp
        assert again.json()["name"] == "Creator renamed"
        assert subscribe(client, B, VID2, "Creator renamed").status_code == 200
        result = client.get("/api/subscriptions").json()
        assert len(result) == 2  # Never merge same-name channels.
        assert {v["channel_id"] for v in result} == {A, B}
        assert client.get("/api/videos").json() == []  # Subscribe != watch/save.
        assert client.delete("/api/subscriptions/" + A).status_code == 204
        assert client.delete("/api/subscriptions/" + A).status_code == 404
        assert client.delete("/api/subscriptions/not-a-channel").status_code == 422
        assert [v["channel_id"] for v in client.get("/api/subscriptions").json()] == [B]


def test_on_demand_resolution_for_old_video_caches_channel_id(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "old.db")
    resolutions = []
    def resolve(vid):
        resolutions.append(vid)
        return {"channel_id": A, "name": "Verified YouTube Creator"}
    monkeypatch.setattr(localtube, "resolve_video_channel", resolve)
    with TestClient(localtube.app) as client:
        assert client.post("/api/videos", json={
            "url": VID1, "title": "Unidentified old video", "tags": "learning"
        }).status_code == 201
        saved = client.get("/api/videos").json()[0]
        assert saved["channel_id"] == ""
        assert client.post("/api/subscriptions", json={
            "video_id": VID1
        }).status_code == 200
        assert resolutions == [VID1]
        new = client.get("/api/videos").json()[0]
        assert new["channel_id"] == A
        assert new["plays"] == 0 and new["rating"] == 0
        assert new["watched_seconds"] == 0
        assert client.get("/api/learning/status").json()["examples"] == 0
        # Once validated, the stable ID can be reused without re-resolving.
        assert subscribe(client, A, VID1, "Verified YouTube Creator").status_code == 200
        assert resolutions == [VID1]


def test_failed_resolution_does_not_subscribe(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "fail.db")
    monkeypatch.setattr(localtube, "resolve_video_channel", lambda _: (
        (_ for _ in ()).throw(RuntimeError("Unavailable"))))
    with TestClient(localtube.app) as client:
        response = client.post("/api/subscriptions", json={"video_id": VID1})
        assert response.status_code == 502
        assert client.get("/api/subscriptions").json() == []
        assert subscribe(client, "not-an-id").status_code == 422
        assert subscribe(client, A, video_id="not-a-video").status_code == 422


def test_subscriptions_feed_uses_real_channel_uploads_not_name_search(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "feed.db")
    calls = []
    def fake_channel(cid, limit=6):
        calls.append((cid, limit))
        if cid == A:
            return [
                {"id": VID1, "title": "Independent channel upload",
                 "channel": "Old channel name", "channel_id": A,
                 "upload_date": "20261104"},
                {"id": VID2, "title": "Previously dismissed",
                 "channel": "Old", "channel_id": A,
                 "upload_date": "20261106"},
            ]
        return [
            {"id": VID3, "title": "Another creator upload",
             "channel": "Different creator", "channel_id": B,
             "upload_date": "20261105"},
        ]
    monkeypatch.setattr(localtube, "channel_uploads", fake_channel)
    monkeypatch.setattr(localtube, "search_youtube", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(AssertionError("Never text search subscriptions"))))
    with TestClient(localtube.app) as client:
        assert subscribe(client, A, VID1, "Alpha creator").status_code == 200
        assert subscribe(client, B, VID3, "Beta creator").status_code == 200
        client.post("/api/videos", json={"url": VID2, "title": "Ignore", "tags": "x"})
        client.put("/api/videos/" + VID2 + "/feedback", json={"rating": -1})
        first = client.get("/api/subscriptions/feed")
        assert first.status_code == 200, first.text
        data = first.json()
        assert data["failed_channels"] == []
        assert [x["id"] for x in data["videos"]] == [VID3, VID1]
        assert all(x["id"] != VID2 for x in data["videos"])
        assert data["videos"][0]["channel"] == "Beta creator"
        assert len(calls) == 2
        client.get("/api/subscriptions/feed")
        assert len(calls) == 2  # 15-minute local cache.
        client.get("/api/subscriptions/feed?refresh=true")
        assert len(calls) == 4


def test_partial_network_failures_still_display_other_subscriptions(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "partial.db")
    def fetch(cid, limit=6):
        if cid == A:
            raise RuntimeError("YouTube blocked this channel")
        return [{"id": VID3, "title": "Available channel", "channel_id": B}]
    monkeypatch.setattr(localtube, "channel_uploads", fetch)
    with TestClient(localtube.app) as client:
        subscribe(client, A)
        subscribe(client, B, VID3)
        res = client.get("/api/subscriptions/feed")
        assert res.status_code == 200
        assert res.json()["failed_channels"] == [A]
        assert [v["id"] for v in res.json()["videos"]] == [VID3]


def test_subscription_ml_feature_is_explicit_and_not_a_training_label(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(localtube, "get_model", lambda: object())
    calls = []
    def channel(cid, limit=6):
        calls.append(cid)
        return [
            {"id": VID1, "title": "Subscribed creator video",
             "channel_id": cid, "channel": "Creator One"},
            {"id": VID2, "title": "Other upload",
             "channel_id": cid, "channel": "Creator One"},
        ]
    monkeypatch.setattr(localtube, "channel_uploads", channel)
    monkeypatch.setattr(localtube, "search_youtube", lambda *_a, **_k: (
        (_ for _ in ()).throw(AssertionError("No topic to search"))))
    with TestClient(localtube.app) as client:
        assert subscribe(client, A, VID1).status_code == 200
        response = client.get("/api/feed?ranker=ml&refine=false")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["ranker"] == "ml"
        assert [v["id"] for v in result["videos"]] == [VID1, VID2]
        assert all(v["score_breakdown"]["subscription_prior"] == 1.6
                   for v in result["videos"])
        assert all(v["impression_token"] for v in result["videos"])
        assert result["ml_learning"]["examples"] == 0
        assert len(calls) == 1
        # Explicit subscription alone is NEVER a positive training example.
        with localtube.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM ml_outcomes").fetchone()[0] == 0
        raw = client.get("/api/feed?ranker=watch&refine=false")
        assert raw.status_code == 200
        assert raw.json() == {"topics": [], "videos": []}
        # Exact unsubscribe means future feed no longer draws from the channel.
        assert client.delete("/api/subscriptions/" + A).status_code == 204
        assert client.get("/api/feed?ranker=ml&refine=false").json() == {
            "topics": [], "videos": []}


def test_subscriber_feature_trains_and_old_model_weights_migrate(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "migration.db")
    localtube.init_db()
    with localtube.connect() as db:
        assert "subscription_prior" in ml.FEATURES
        positive = {"subscription_prior": 1.6, "semantic_positive": 2.5}
        negative = {"subscription_prior": 0.0, "semantic_positive": 0}
        for index in range(10):
            vid = f"q{index:010d}"
            token = f"tokenforlearning{index:04d}"
            data = positive if index < 6 else negative
            db.execute("""INSERT INTO ml_impressions(
                token,video_id,created_at,seen_at,features_json)
                VALUES(?,?,?,?,?)""", (token, vid, "2026-11-06T10:00:00+00:00",
                "2026-11-06T10:00:00+00:00", json.dumps(data)))
            db.execute("""INSERT INTO ml_outcomes(
                video_id,token,label,source,updated_at)
                VALUES(?,?,?,?,?)""", (vid, token, int(index < 6), "like",
                                      "2026-11-06T10:00:00+00:00"))
        old = [0.2] * (len(ml.FEATURES)-1)
        db.execute("""INSERT INTO ml_model(
            id,weights_json,bias,positives,negatives,trained,updated_at)
            VALUES(1,?,?,?,?,?,?)""", (json.dumps(old), 0.1, 6, 4, 1,
                                       "2026-11-06T10:00:00+00:00"))
    localtube.init_db()  # Migration retrains from existing labeled snapshots.
    with localtube.connect() as db:
        new = db.execute("SELECT weights_json FROM ml_model").fetchone()
        weights = json.loads(new["weights_json"])
        assert len(weights) == len(ml.FEATURES)
        assert ml.status(db)["examples"] == 10
        assert ml.status(db)["trained"]
        assert weights[ml.FEATURES.index("subscription_prior")] > 0


def test_subscription_limit_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(localtube, "DB_PATH", tmp_path / "limit.db")
    localtube.init_db()
    with localtube.connect() as db:
        for index in range(subs.MAX_SUBSCRIPTIONS):
            cid = "UC" + f"{index:022d}"
            subs.add(db, cid, "Creator " + str(index))
        try:
            subs.add(db, "UC" + "Z"*22, "Over limit")
            assert False, "Expected subscription limit"
        except ValueError as exc:
            assert "Maximum local subscriptions" in str(exc)
        assert len(subs.listing(db)) == subs.MAX_SUBSCRIPTIONS
