"""Static UI contract tests, so a redesign cannot silently remove core controls."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "static" / "index.html"
JS = ROOT / "static" / "app.js"
CSS = ROOT / "static" / "style.css"


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.scripts = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "script":
            self.scripts.append(values.get("src"))
        if tag == "img":
            self.images.append(values.get("src"))


def test_video_first_layout_has_required_controls():
    parser = PageParser()
    parser.feed(HTML.read_text(encoding="utf-8"))
    assert len(parser.ids) == len(set(parser.ids))
    assert {
        "video-search", "query-input", "sidebar", "show-thumbnails", "video-grid",
        "player-dialog", "player-wrap", "add-dialog", "add-form",
        "interests-dialog", "topic-form", "topic-chips", "library-search",
        "page-title", "global-message",
    }.issubset(parser.ids)
    assert parser.scripts == ["/static/player.js", "/static/app.js"]  # No remote JavaScript.
    assert parser.images == []  # No Google images on initial HTML load.


def test_js_only_uses_known_elements_and_opt_in_thumbnails():
    import re
    parser = PageParser()
    parser.feed(HTML.read_text(encoding="utf-8"))
    js = JS.read_text(encoding="utf-8")
    element_references = set(re.findall(r'\$\("([\w-]+)"\)', js))
    assert element_references.issubset(parser.ids)
    assert 'thumbnails: false' in js  # Opt-in default.
    assert '"https://i.ytimg.com/vi/"' in js
    assert 'if (state.thumbnails && validId(video.id))' in js
    player_js = (ROOT / 'static' / 'player.js').read_text(encoding='utf-8')
    assert 'https://www.youtube-nocookie.com/embed/' in player_js
    assert 'https://www.youtube.com/iframe_api' in player_js
    assert 'id="watch-status"' in HTML.read_text(encoding='utf-8')
    assert "16/9" in CSS.read_text(encoding="utf-8")


def test_javascript_is_syntactically_valid_if_node_is_available():
    if not shutil.which("node"):
        return
    subprocess.run(["node", "--check", str(JS)], check=True)
    subprocess.run(["node", "--check", str(ROOT / "static" / "player.js")], check=True)


def test_one_click_like_controls_in_cards_and_player():
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    # Both controls are present; players receive a descriptive button label.
    assert 'id="player-like"' in html
    assert 'id="player-like-label"' in html
    assert 'aria-pressed="false"' in html
    assert "bottom.append(avatar, content, likeButton(video), menu)" in js
    assert 'button.setAttribute("aria-pressed", String(liked))' in js
    assert 'button.setAttribute("aria-label", (liked ? "Unlike " : "Like ") + video.title)' in js
    assert '"player-like").addEventListener("click"' in js
    assert "await ensureSaved(video);" in js
    assert 'method: "PUT", body: JSON.stringify({rating})' in js
    assert "state.pendingLikes.has(video.id)" in js
    assert ".card-like.is-liked" in css
    # Like updates must not recreate the iframe or reload suggestions.
    rate_method = js.split("async function rateVideo(video, rating) {", 1)[1].split(
        "async function dismissVideo(video) {", 1
    )[0]
    assert "await loadView(" not in rate_method
    assert 'player-wrap").replaceChildren' not in rate_method
    assert "renderGrid();" in rate_method


def test_tracking_controls_are_local_and_not_loaded_before_play():
    html = HTML.read_text(encoding="utf-8")
    player = (ROOT / "static" / "player.js").read_text(encoding="utf-8")
    app = JS.read_text(encoding="utf-8")
    assert 'data-view="history"' in html
    assert 'id="watch-stats"' in html
    assert 'id="watch-status"' in html
    assert "window.LocalTubePlayer.open(video, saved)" in app
    assert "window.LocalTubePlayer.close()" in app
    assert "/api/stats" in app
    # v0.7 adds ranker/refine query parameters first, then the refresh flag.
    assert '(refresh ? "&refresh=true" : "")' in app
    assert 'script.src = "https://www.youtube.com/iframe_api"' in player
    assert 'https://www.youtube-nocookie.com/embed/' in player
    assert "video.watched_seconds" in app
    assert "resume_position" in app
    assert "sample(session)" in player
    assert 'method: "PUT"' in player
    assert "model_validator" not in player


def test_v07_model_selector_and_explanation_menu():
    html = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert 'id="ranking-model"' in html
    assert 'id="refined-search"' in html
    assert 'localtube-refined-search' in js
    assert '"&refine=" + String(state.refine)' in js
    assert 'value="content"' in html
    assert 'value="baseline"' in html
    assert "ranker: \"content\"" in js
    assert 'encodeURIComponent(state.ranker)' in js
    assert 'menuAction("Why this video?"' in js
    assert "score_breakdown" in js
    assert ".ranking-choice" in css
    # This feature only adds same-origin requests; scripts remain local on home.
    parser = PageParser()
    parser.feed(html)
    assert parser.scripts == ["/static/player.js", "/static/app.js"]


def test_feed_balance_slider_persists_and_preserves_baseline():
    html = HTML.read_text(encoding="utf-8")
    script = JS.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert 'id="feed-balance-control"' in html
    assert 'id="feed-balance"' in html
    assert 'id="feed-balance-value"' in html
    assert 'min="0" max="100" step="5" value="65"' in html
    assert 'aria-describedby="feed-balance-description"' in html
    assert 'localtube-feed-balance' in script
    assert '"&balance=" + String(state.balance)' in script
    assert 'state.ranker === "baseline"' in script
    assert '"feed-balance").addEventListener("change"' in script
    assert '.feed-balance-control[hidden]{display:none}' in css
    assert 'v0.8 Content' in html
