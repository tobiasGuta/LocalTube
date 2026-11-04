/* Public YouTube discovery, separate from the local library UI. */
const explore = { results: [], view: "feed", loading: false, topics: [] };
const discoveryEl = (id) => document.getElementById(id);
function discoveryStatus(message, isError = false) {
  const node = discoveryEl("discover-status");
  node.textContent = message;
  node.style.color = isError ? "#ff9ea8" : "#b4c6e8";
}
function cardButton(label, onClick, cssClass = "") {
  const node = document.createElement("button");
  node.type = "button";
  node.textContent = label;
  node.className = cssClass;
  node.addEventListener("click", onClick);
  return node;
}
async function loadInterests() {
  explore.topics = await request("/api/topics");
  const node = discoveryEl("topic-chips");
  node.replaceChildren();
  for (const topic of explore.topics) {
    const chip = cardButton(topic + " ×", async () => {
      try {
        await request("/api/topics/" + encodeURIComponent(topic), { method: "DELETE" });
        await loadInterests();
        await loadFeed();
      } catch (err) { discoveryStatus(err.message, true); }
    });
    chip.title = "Remove interest: " + topic;
    node.append(chip);
  }
}
function showDiscoverResults() {
  const grid = discoveryEl("discover-results");
  grid.replaceChildren();
  const thumbnails = discoveryEl("show-thumbnails").checked;
  for (const video of explore.results) {
    const card = document.createElement("article");
    card.className = "card discover-card";
    const art = document.createElement("div");
    art.className = "art";
    if (thumbnails) {
      const thumb = document.createElement("img");
      thumb.src = "https://i.ytimg.com/vi/" + encodeURIComponent(video.id) + "/hqdefault.jpg";
      thumb.alt = "";
      thumb.loading = "lazy";
      thumb.referrerPolicy = "no-referrer";
      art.append(thumb);
    }
    const icon = document.createElement("span");
    icon.textContent = "▶";
    art.append(icon);
    const body = document.createElement("div");
    body.className = "body";
    const title = document.createElement("h3");
    title.textContent = video.title;
    body.append(title);
    const channel = document.createElement("p");
    channel.className = "meta";
    channel.textContent = video.channel || "Unknown creator";
    body.append(channel);
    const reason = document.createElement("p");
    reason.className = "reason";
    reason.textContent = video.reason || "Suggested from your interests";
    body.append(reason);
    const controls = document.createElement("div");
    controls.className = "actions";
    controls.append(cardButton("▶ Play", () => saveDiscoveryVideo(video, "play"), "watch"));
    controls.append(cardButton("Save", () => saveDiscoveryVideo(video, "save")));
    controls.append(cardButton("Not interested", () => saveDiscoveryVideo(video, "hide")));
    card.append(art, body, controls);
    grid.append(card);
  }
}
async function loadFeed() {
  if (explore.loading) return;
  explore.loading = true;
  explore.view = "feed";
  discoveryEl("back-to-feed").hidden = true;
  discoveryStatus("Finding videos based on your interests...");
  try {
    const feed = await request("/api/feed");
    explore.results = feed.videos;
    showDiscoverResults();
    discoveryStatus(feed.videos.length
      ? "Showing " + feed.videos.length + " new videos for " + feed.topics.join(" and ") + "."
      : "No new videos yet. Add an interest or play a saved video to start discovering.");
  } catch (err) {
    explore.results = [];
    showDiscoverResults();
    discoveryStatus(err.message + " Your saved library still works.", true);
  } finally { explore.loading = false; }
}
async function searchDiscover(query) {
  if (explore.loading) return;
  explore.loading = true;
  discoveryStatus('Searching YouTube for "' + query + '"...');
  try {
    explore.results = await request("/api/discover?q=" + encodeURIComponent(query));
    explore.view = "search";
    discoveryEl("back-to-feed").hidden = false;
    showDiscoverResults();
    discoveryStatus(explore.results.length
      ? "Found " + explore.results.length + " videos. Play, save, or dismiss any result."
      : "No results found. Try another search.");
  } catch (err) {
    discoveryStatus(err.message, true);
  } finally { explore.loading = false; }
}
async function saveDiscoveryVideo(video, action) {
  try {
    const tags = (video.tags || []).map(t => t.slice(0, 30)).join(",");
    try {
      await request("/api/videos", {
        method: "POST", body: JSON.stringify({ url: video.id, title: video.title, tags })
      });
    } catch (err) {
      if (!/already saved/i.test(err.message)) throw err;
    }
    if (action === "play") await play(video);
    if (action === "hide") {
      await request("/api/videos/" + video.id + "/feedback", {
        method: "PUT", body: JSON.stringify({ rating: -1 })
      });
    }
    await refresh();
    explore.results = explore.results.filter(v => v.id !== video.id);
    showDiscoverResults();
    discoveryStatus(action === "hide" ? "Got it. We will avoid that video." :
      action === "save" ? "Saved to your library." : "Play recorded locally.");
  } catch (err) { discoveryStatus(err.message, true); }
}
discoveryEl("topic-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const value = discoveryEl("topic-input").value.trim();
  try {
    await request("/api/topics", {
      method: "POST", body: JSON.stringify({ topic: value })
    });
    form.reset();
    await loadInterests();
    await loadFeed();
  } catch (err) { discoveryStatus(err.message, true); }
});
discoveryEl("video-search").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = discoveryEl("query-input").value.trim();
  if (query.length >= 2) await searchDiscover(query);
});
discoveryEl("show-thumbnails").addEventListener("change", showDiscoverResults);
discoveryEl("reload-feed").addEventListener("click", loadFeed);
discoveryEl("back-to-feed").addEventListener("click", loadFeed);
(async () => {
  try { await loadInterests(); await loadFeed(); }
  catch (err) { discoveryStatus(err.message, true); }
})();
