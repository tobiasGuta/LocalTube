const el = (id) => document.getElementById(id);
const state = { mode: "recommend", videos: [] };

async function request(path, options = {}) {
  const res = await fetch(path, Object.assign({}, options, {
    headers: Object.assign({"Content-Type":"application/json"}, options.headers || {})
  }));
  if (!res.ok) {
    let detail = "Request failed";
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw Error(detail);
  }
  return res.status === 204 ? null : res.json();
}
function status(message, error = false) {
  el("message").textContent = message;
  el("message").style.color = error ? "#ff9ea8" : "#a6c6ff";
}
function button(label, className, callback, pressed) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.className = className;
  if (pressed !== undefined) b.setAttribute("aria-pressed", String(pressed));
  b.addEventListener("click", callback);
  return b;
}
async function refresh() {
  try {
    const result = await Promise.all([
      request("/api/videos"),
      request(state.mode === "recommend" ? "/api/recommendations" : "/api/videos")
    ]);
    el("count").textContent = result[0].length;
    state.videos = result[1];
    render();
  } catch (e) { status(e.message, true); }
}
function render() {
  const term = el("search").value.toLowerCase().trim();
  const videos = state.videos.filter(v => (v.title + " " + v.tags.join(" ")).toLowerCase().includes(term));
  const grid = el("videos");
  grid.replaceChildren();
  el("empty").hidden = videos.length > 0;
  el("empty").textContent = term ? "No matching saved videos." :
    "No videos saved yet. Add some links and topic tags to get started.";
  for (const v of videos) {
    const card = document.createElement("article"); card.className = "card";
    const art = document.createElement("div"); art.className = "art";
    const icon = document.createElement("span"); icon.textContent = "▶"; art.append(icon);
    const body = document.createElement("div"); body.className = "body";
    const h3 = document.createElement("h3"); h3.textContent = v.title; body.append(h3);
    if (state.mode === "recommend" && v.reason) {
      const reason = document.createElement("p"); reason.className = "reason";
      reason.textContent = v.reason; body.append(reason);
    }
    const tags = document.createElement("div"); tags.className = "tags";
    v.tags.forEach(t => {
      const span = document.createElement("span"); span.className = "tag";
      span.textContent = t; tags.append(span);
    });
    body.append(tags);
    const meta = document.createElement("p"); meta.className = "meta";
    meta.textContent = v.plays + " play clicks · " +
      (v.rating === 1 ? "Liked" : v.rating === -1 ? "Not interested" : "No feedback");
    body.append(meta);
    const actions = document.createElement("div"); actions.className = "actions";
    actions.append(button("▶ Play", "watch", () => play(v)));
    actions.append(button("♡ Like", v.rating === 1 ? "active" : "", () => rate(v, v.rating === 1 ? 0 : 1), v.rating === 1));
    actions.append(button("− Not interested", v.rating === -1 ? "active" : "", () => rate(v, v.rating === -1 ? 0 : -1), v.rating === -1));
    actions.append(button("Remove", "", () => remove(v)));
    card.append(art, body, actions); grid.append(card);
  }
}
async function rate(v, rating) {
  try {
    await request("/api/videos/" + v.id + "/feedback", {method:"PUT", body:JSON.stringify({rating})});
    await refresh();
  } catch (e) { status(e.message, true); }
}
async function remove(v) {
  if (!confirm("Remove " + v.title + " from your library?")) return;
  try { await request("/api/videos/" + v.id, {method:"DELETE"}); await refresh(); }
  catch (e) { status(e.message, true); }
}
async function play(v) {
  try {
    await request("/api/videos/" + v.id + "/play", {method:"POST"});
    el("player-title").textContent = v.title;
    const frame = document.createElement("iframe");
    frame.title = v.title;
    frame.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(v.id) + "?autoplay=1&rel=0";
    frame.allow = "accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture; web-share";
    frame.referrerPolicy = "strict-origin-when-cross-origin";
    frame.allowFullscreen = true;
    el("player-wrap").replaceChildren(frame);
    el("player-dialog").showModal();
    await refresh();
  } catch (e) { status(e.message, true); }
}
el("close-player").addEventListener("click", () => el("player-dialog").close());
el("player-dialog").addEventListener("close", () => el("player-wrap").replaceChildren());
el("player-dialog").addEventListener("click", (event) => {
  if (event.target === el("player-dialog")) el("player-dialog").close();
});
el("search").addEventListener("input", render);
for (const entry of [["recommend-tab","recommend"], ["library-tab","library"]]) {
  el(entry[0]).addEventListener("click", async () => {
    state.mode = entry[1];
    el("recommend-tab").classList.toggle("selected", state.mode === "recommend");
    el("library-tab").classList.toggle("selected", state.mode === "library");
    await refresh();
  });
}
el("add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const b = form.querySelector("button");
  b.disabled = true;
  try {
    await request("/api/videos", {
      method:"POST", body:JSON.stringify(Object.fromEntries(new FormData(form)))
    });
    form.reset();
    status("Added! Play or rate some videos to customize recommendations.");
    await refresh();
  } catch (e) { status(e.message, true); }
  finally { b.disabled = false; }
});
refresh();
