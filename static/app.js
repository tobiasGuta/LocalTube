"use strict";
// All user preferences and video activity stay in LocalTube's local database.
// Only the thumbnail display preference is stored in this browser.
const $ = (id) => document.getElementById(id);
const state = {
  view: "home", results: [], saved: [], topics: [], filter: "All",
  thumbnails: false, query: "", requestToken: 0
};
try { state.thumbnails = localStorage.getItem("localtube-thumbnails") === "on"; } catch (_) {}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})}
  });
  if (!response.ok) {
    let detail = "Request failed (" + response.status + ")";
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : detail;
    } catch (_) {}
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function notice(text, error = false) {
  $("global-message").textContent = text || "";
  $("global-message").classList.toggle("error", error);
}

function make(tag, cssClass, text) {
  const node = document.createElement(tag);
  if (cssClass) node.className = cssClass;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function action(label, handler, cssClass) {
  const button = make("button", cssClass || "", label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function validId(id) {
  return typeof id === "string" && /^[A-Za-z0-9_-]{11}$/.test(id);
}

function formatDuration(duration) {
  if (typeof duration !== "number" || !Number.isFinite(duration) || duration < 0) return "";
  const seconds = Math.floor(duration);
  const hr = Math.floor(seconds / 3600);
  const min = Math.floor((seconds % 3600) / 60);
  const sec = String(seconds % 60).padStart(2, "0");
  return hr ? hr + ":" + String(min).padStart(2, "0") + ":" + sec : min + ":" + sec;
}

function channelOf(video) {
  return video.channel || video.uploader || "Saved video";
}

function playIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const triangle = document.createElementNS("http://www.w3.org/2000/svg", "path");
  triangle.setAttribute("d", "M8 5v14l11-7z");
  triangle.setAttribute("fill", "currentColor");
  triangle.setAttribute("stroke", "none");
  svg.append(triangle);
  return svg;
}

function renderCard(video) {
  const saved = state.saved.find(item => item.id === video.id);
  const card = make("article", "video-card");
  const thumbnail = action("", () => play(video), "thumbnail");
  thumbnail.setAttribute("aria-label", "Play " + video.title);
  const placeholder = make("span", "thumb-abstract");
  placeholder.append(playIcon());
  thumbnail.append(placeholder);

  if (state.thumbnails && validId(video.id)) {
    const image = make("img");
    image.src = "https://i.ytimg.com/vi/" + encodeURIComponent(video.id) + "/hqdefault.jpg";
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    image.referrerPolicy = "no-referrer";
    image.addEventListener("error", () => image.remove());
    thumbnail.append(image);
  }

  const hoverPlay = make("span", "thumb-play");
  hoverPlay.append(playIcon());
  thumbnail.append(hoverPlay);
  const duration = formatDuration(video.duration);
  if (duration) thumbnail.append(make("span", "duration", duration));

  const bottom = make("div", "video-card-body");
  const avatar = make("div", "avatar", channelOf(video).trim().slice(0, 1).toUpperCase() || "L");
  avatar.setAttribute("aria-hidden", "true");
  const content = make("div", "card-text");
  const title = action(video.title, () => play(video), "video-title");
  title.title = video.title;
  const channel = make("p", "channel-name", channelOf(video));
  const details = make("p", "video-details");
  if (video.plays !== undefined) {
    details.textContent = video.plays + " play clicks" + (video.rating === 1 ? " · Liked" : "");
  } else {
    details.textContent = video.reason || "Suggested for you";
  }
  content.append(title, channel, details);

  const menu = make("details", "video-actions");
  const summary = make("summary", "menu-summary", "⋮");
  summary.setAttribute("aria-label", "More actions for " + video.title);
  summary.title = "More actions";
  const menuItems = make("div", "card-menu");
  function menuAction(label, callback, destructive = false) {
    const item = action(label, () => {
      menu.open = false;
      callback();
    }, destructive ? "destructive" : "");
    menuItems.append(item);
  }
  if (!saved) {
    menuAction("Save to library", () => saveVideo(video));
  } else {
    menuAction(saved.rating === 1 ? "Remove like" : "Like video", () => rateVideo(video, saved.rating === 1 ? 0 : 1));
  }
  menuAction("Not interested", () => dismissVideo(video));
  if (saved) menuAction("Remove from library", () => removeVideo(video), true);
  menu.append(summary, menuItems);
  bottom.append(avatar, content, menu);
  card.append(thumbnail, bottom);
  return card;
}

function filteredResults() {
  let videos;
  if (state.view === "library") videos = state.saved;
  else if (state.view === "liked") videos = state.saved.filter(v => v.rating === 1);
  else videos = state.results;

  if (state.filter !== "All") {
    const filter = state.filter.toLowerCase();
    videos = videos.filter(v => (v.tags || []).some(tag => tag.toLowerCase() === filter));
  }
  if ((state.view === "library" || state.view === "liked") && $("library-search").value.trim()) {
    const term = $("library-search").value.trim().toLowerCase();
    videos = videos.filter(v => (v.title + " " + (v.tags || []).join(" ")).toLowerCase().includes(term));
  }
  return videos;
}

function renderFilters() {
  const root = $("filter-chips");
  root.replaceChildren();
  if (state.view === "search") {
    root.hidden = true;
    return;
  }
  root.hidden = false;
  const collection = state.view === "library" || state.view === "liked"
    ? state.saved : state.results;
  const items = ["All"];
  const available = new Set(collection.flatMap(v => v.tags || []).map(t => t.toLowerCase()));
  for (const topic of state.topics) {
    if (available.has(topic.toLowerCase()) && !items.some(t => t.toLowerCase() === topic.toLowerCase())) items.push(topic);
  }
  for (const topic of available) {
    if (!items.some(t => t.toLowerCase() === topic)) items.push(topic);
    if (items.length >= 13) break;
  }
  if (!items.includes(state.filter)) state.filter = "All";
  for (const topic of items) {
    const item = action(topic === "All" ? "All" : topic.charAt(0).toUpperCase() + topic.slice(1), () => {
      state.filter = topic;
      renderFilters();
      renderGrid();
    }, "filter-chip" + (state.filter === topic ? " selected" : ""));
    item.setAttribute("aria-pressed", String(topic === state.filter));
    root.append(item);
  }
}

function renderGrid() {
  const root = $("video-grid");
  root.replaceChildren();
  const videos = filteredResults();
  for (const video of videos) root.append(renderCard(video));
  $("empty-state").hidden = videos.length !== 0;
  if (videos.length) return;
  const empty = {
    home: ["Build your personal feed", "Add an interest or play saved videos, and LocalTube will find related suggestions.", "Choose interests"],
    explore: ["Nothing to explore yet", "Choose an interest or search for something you want to watch.", "Choose interests"],
    search: ["No videos found", "Try a different search phrase, or search for a broader topic.", "Go home"],
    library: ["Your library is empty", "Save a video to make it easy to find again.", "Add video"],
    liked: ["No liked videos yet", "Use the three-dot menu on any saved video to give it a like.", "Go home"]
  }[state.view];
  $("empty-title").textContent = state.filter !== "All" ? "No videos in " + state.filter : empty[0];
  $("empty-description").textContent = state.filter !== "All" ? "Try selecting All or another topic." : empty[1];
  $("empty-action").textContent = state.filter !== "All" ? "Show all" : empty[2];
}

function renderShell() {
  for (const nav of document.querySelectorAll(".nav-item[data-view]")) {
    const isSelected = nav.dataset.view === state.view;
    nav.classList.toggle("selected", isSelected);
    if (isSelected) nav.setAttribute("aria-current", "page");
    else nav.removeAttribute("aria-current");
  }
  const settings = {
    home: ["Recommended", "Videos chosen from your interests and local viewing activity."],
    explore: ["Explore videos", "Discover something new without signing in to Google."],
    search: ["Search results", 'Results for "' + state.query + '"'],
    library: ["Your library", "Saved videos are kept on your Fedora computer."],
    liked: ["Liked videos", "The videos you have marked as favorites."]
  }[state.view];
  $("page-title").textContent = settings[0];
  $("page-description").textContent = settings[1];
  $("library-search-wrap").hidden = state.view !== "library" && state.view !== "liked";
  $("back-to-feed").hidden = state.view !== "search";
  $("reload-feed").hidden = state.view === "search" || state.view === "library" || state.view === "liked";
  renderFilters();
  renderGrid();
}

async function loadSaved() {
  state.saved = await api("/api/videos");
  $("library-count").textContent = state.saved.length;
}

async function loadTopics() {
  state.topics = await api("/api/topics");
  const container = $("topic-chips");
  container.replaceChildren();
  for (const topic of state.topics) {
    const chip = action(topic + " ×", async () => {
      try {
        await api("/api/topics/" + encodeURIComponent(topic), {method: "DELETE"});
        await loadTopics();
        if (state.view === "home" || state.view === "explore") await loadView(state.view);
        else renderShell();
      } catch (err) { $("interest-message").textContent = err.message; }
    });
    chip.setAttribute("aria-label", "Remove interest " + topic);
    container.append(chip);
  }
  if (!state.topics.length) container.append(make("p", "dialog-description", "No interests added yet. Your saved video tags also help recommendations."));
}

async function loadView(view) {
  state.view = view;
  state.filter = "All";
  state.query = "";
  const token = ++state.requestToken;
  closeMobileSidebar();
  notice("");
  if (view === "library" || view === "liked") {
    try {
      await loadSaved();
      if (token === state.requestToken) renderShell();
    } catch (err) { if (token === state.requestToken) notice(err.message, true); }
    return;
  }
  state.results = [];
  notice("Finding videos for you…");
  renderShell();
  try {
    const feed = await api("/api/feed");
    if (token !== state.requestToken) return;
    state.results = feed.videos;
    notice(feed.topics.length ? "" : "Add an interest or play a saved video to start discovering.");
    renderShell();
  } catch (err) {
    if (token !== state.requestToken) return;
    state.results = [];
    renderShell();
    notice(err.message + " Your saved library still works.", true);
  }
}

async function searchVideos(query) {
  const token = ++state.requestToken;
  state.view = "search";
  state.query = query;
  state.filter = "All";
  state.results = [];
  closeMobileSidebar();
  renderShell();
  notice("Searching YouTube…");
  try {
    const results = await api("/api/discover?q=" + encodeURIComponent(query));
    if (token !== state.requestToken) return;
    state.results = results;
    renderShell();
    notice(results.length ? "" : "No results. Try a broader search.");
  } catch (err) {
    if (token !== state.requestToken) return;
    renderShell();
    notice(err.message + " Your local library is still available.", true);
  }
}

async function ensureSaved(video) {
  if (state.saved.some(v => v.id === video.id)) return;
  const tags = (video.tags || []).filter(tag => typeof tag === "string").slice(0, 8).map(t => t.slice(0, 30)).join(", ");
  try {
    await api("/api/videos", {method: "POST", body: JSON.stringify({
      url: video.id, title: video.title.slice(0, 160), tags
    })});
  } catch (err) {
    if (err.status !== 409) throw err;
  }
  await loadSaved();
}

async function saveVideo(video) {
  try {
    await ensureSaved(video);
    notice("Saved to your local library.");
    renderShell();
  } catch (err) { notice(err.message, true); }
}

async function rateVideo(video, rating) {
  try {
    await ensureSaved(video);
    await api("/api/videos/" + encodeURIComponent(video.id) + "/feedback", {
      method: "PUT", body: JSON.stringify({rating})
    });
    await loadSaved();
    notice(rating === 1 ? "Liked. Your recommendations will learn from this." : "Like removed.");
    renderShell();
  } catch (err) { notice(err.message, true); }
}

async function dismissVideo(video) {
  try {
    await ensureSaved(video);
    await api("/api/videos/" + encodeURIComponent(video.id) + "/feedback", {
      method: "PUT", body: JSON.stringify({rating: -1})
    });
    await loadSaved();
    if (state.view === "home" || state.view === "explore" || state.view === "search") {
      state.results = state.results.filter(v => v.id !== video.id);
    }
    renderShell();
    notice("Not interested — this video won't appear in your recommendation feed.");
  } catch (err) { notice(err.message, true); }
}

async function removeVideo(video) {
  if (!confirm('Remove "' + video.title + '" from your local library?')) return;
  try {
    await api("/api/videos/" + encodeURIComponent(video.id), {method: "DELETE"});
    await loadSaved();
    renderShell();
    notice("Removed from library.");
  } catch (err) { notice(err.message, true); }
}

async function play(video) {
  if (!validId(video.id)) return notice("Invalid video ID.", true);
  try {
    await ensureSaved(video);
    await api("/api/videos/" + encodeURIComponent(video.id) + "/play", {method: "POST"});
    await loadSaved();
    $("player-title").textContent = video.title;
    const frame = make("iframe");
    frame.title = video.title;
    frame.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(video.id) + "?autoplay=1&rel=0";
    frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    frame.allowFullscreen = true;
    frame.referrerPolicy = "strict-origin-when-cross-origin";
    $("player-wrap").replaceChildren(frame);
    $("player-dialog").showModal();
    renderGrid();
  } catch (err) { notice(err.message, true); }
}

function closeMobileSidebar() {
  document.body.classList.remove("mobile-sidebar-open");
  $("sidebar-toggle").setAttribute("aria-expanded", "false");
}

$("sidebar-toggle").addEventListener("click", () => {
  if (window.matchMedia("(max-width: 760px)").matches) {
    const open = document.body.classList.toggle("mobile-sidebar-open");
    $("sidebar-toggle").setAttribute("aria-expanded", String(open));
  } else {
    const collapsed = document.body.classList.toggle("sidebar-collapsed");
    $("sidebar-toggle").setAttribute("aria-expanded", String(!collapsed));
  }
});
$("sidebar-backdrop").addEventListener("click", closeMobileSidebar);
document.querySelectorAll(".nav-item[data-view]").forEach(nav => nav.addEventListener("click", () => loadView(nav.dataset.view)));
$("reload-feed").addEventListener("click", () => loadView(state.view === "explore" ? "explore" : "home"));
$("back-to-feed").addEventListener("click", () => loadView("home"));
$("video-search").addEventListener("submit", (event) => {
  event.preventDefault();
  const query = $("query-input").value.trim();
  if (query.length >= 2) searchVideos(query);
});
$("library-search").addEventListener("input", renderGrid);
$("show-thumbnails").checked = state.thumbnails;
$("show-thumbnails").addEventListener("change", (event) => {
  state.thumbnails = event.currentTarget.checked;
  try { localStorage.setItem("localtube-thumbnails", state.thumbnails ? "on" : "off"); } catch (_) {}
  renderGrid();
  notice(state.thumbnails ? "Thumbnails enabled. Image requests will now reach Google." : "Thumbnails disabled. No thumbnail requests will be made.");
});
$("empty-action").addEventListener("click", () => {
  if (state.filter !== "All") {
    state.filter = "All";
    renderShell();
  } else if (state.view === "library") {
    $("add-dialog").showModal();
  } else if (state.view === "liked" || state.view === "search") {
    loadView("home");
  } else {
    $("interests-dialog").showModal();
  }
});
$("open-add").addEventListener("click", () => $("add-dialog").showModal());
$("close-add").addEventListener("click", () => $("add-dialog").close());
$("add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  $("add-message").textContent = "";
  try {
    await api("/api/videos", {
      method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(form)))
    });
    form.reset();
    $("add-dialog").close();
    await loadSaved();
    await loadView("library");
    notice("Video saved to your library.");
  } catch (err) {
    $("add-message").textContent = err.message;
  } finally { submit.disabled = false; }
});
$("open-interests").addEventListener("click", () => {
  closeMobileSidebar();
  $("interests-dialog").showModal();
});
$("close-interests").addEventListener("click", () => $("interests-dialog").close());
$("topic-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  $("interest-message").textContent = "";
  try {
    await api("/api/topics", {method: "POST", body: JSON.stringify({
      topic: $("topic-input").value.trim()
    })});
    form.reset();
    await loadTopics();
    if (state.view === "home" || state.view === "explore") await loadView(state.view);
    else renderShell();
  } catch (err) {
    $("interest-message").textContent = err.message;
  } finally { submit.disabled = false; }
});
$("close-player").addEventListener("click", () => $("player-dialog").close());
$("player-dialog").addEventListener("close", () => $("player-wrap").replaceChildren());
for (const id of ["player-dialog", "add-dialog", "interests-dialog"]) {
  $(id).addEventListener("click", (event) => {
    if (event.target === $(id)) $(id).close();
  });
}
document.addEventListener("keydown", (event) => {
  const focused = document.activeElement;
  const typing = focused && (focused.tagName === "INPUT" || focused.tagName === "TEXTAREA" || focused.isContentEditable);
  if (event.key === "/" && !typing && !document.querySelector("dialog[open]")) {
    event.preventDefault();
    $("query-input").focus();
  }
});
(async () => {
  try {
    await Promise.all([loadSaved(), loadTopics()]);
    await loadView("home");
  } catch (err) {
    notice("LocalTube could not load: " + err.message, true);
  }
})();
