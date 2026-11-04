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
    assert parser.scripts == ["/static/app.js"]  # No remote JavaScript.
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
    assert 'https://www.youtube-nocookie.com/embed/' in js
    assert "16/9" in CSS.read_text(encoding="utf-8")


def test_javascript_is_syntactically_valid_if_node_is_available():
    if not shutil.which("node"):
        return
    subprocess.run(["node", "--check", str(JS)], check=True)


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
