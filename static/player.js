"use strict";
/* LocalTube v0.6 player: YouTube's official IFrame API loads ONLY on Play.
   No Google scripts on the homepage. All progress writes go to localhost. */
(() => {
  let apiPromise = null;
  let current = null;
  let nextSessionId = 0;
  const INTERVAL_MS = 5000;

  const $ = (id) => document.getElementById(id);
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  const clamp = (n, low, high) => Math.max(low, Math.min(high, n));

  function status(message) { $("watch-status").textContent = message; }

  function loadIframeAPI() {
    if (window.YT && window.YT.Player) return Promise.resolve(window.YT);
    if (apiPromise) return apiPromise;
    apiPromise = new Promise((resolve, reject) => {
      let settled = false;
      const previous = window.onYouTubeIframeAPIReady;
      const done = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        if (error) reject(error);
        else resolve(window.YT);
      };
      window.onYouTubeIframeAPIReady = () => {
        if (typeof previous === "function") previous();
        done(null);
      };
      const script = document.createElement("script");
      script.src = "https://www.youtube.com/iframe_api";
      script.async = true;
      script.onerror = () => done(new Error("YouTube API script unavailable"));
      const timeout = setTimeout(() => done(new Error("YouTube API timed out")), 8000);
      document.head.append(script);
    }).catch(err => {
      apiPromise = null;  // User can retry loading on a future video.
      throw err;
    });
    return apiPromise;
  }

  function makeFrame(video, position, useApi) {
    const frame = document.createElement("iframe");
    frame.id = "localtube-player-frame";
    frame.title = video.title;
    const args = new URLSearchParams({
      autoplay: "1", rel: "0", playsinline: "1",
      ...(useApi ? {enablejsapi: "1", origin: window.location.origin} : {}),
      ...(position >= 5 ? {start: String(Math.floor(position))} : {})
    });
    frame.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(video.id) + "?" + args;
    frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    frame.allowFullscreen = true;
    frame.referrerPolicy = "strict-origin-when-cross-origin";
    return frame;
  }

  function sample(session, ended = false) {
    if (!session.active || !session.player) return;
    let position, duration;
    try {
      position = session.player.getCurrentTime();
      duration = session.player.getDuration();
    } catch (_) { return; }
    if (!finite(position) || position < 0) return;
    duration = finite(duration) && duration > 0 && duration < 86400
      ? duration : (session.duration || 0);
    position = clamp(position, 0, duration > 0 ? duration : 86400);
    const now = performance.now();
    let delta = 0;
    if (session.playing && finite(session.lastPosition) && finite(session.lastWall)) {
      const movement = position - session.lastPosition;
      const wall = (now - session.lastWall) / 1000;
      // Ignore skipping, replay seeks, and browser suspension.
      if (movement > 0 && movement <= wall + 1.8 && wall <= 20) {
        delta = clamp(movement, 0, Math.min(15, wall + 1.8));
      }
    }
    session.lastPosition = position;
    session.lastWall = now;
    session.duration = duration;
    if (ended && session.completed) return;
    if (ended) session.completed = true;
    const payload = {
      position_seconds: ended ? duration : position,
      duration_seconds: duration,
      watched_delta: delta,
      ended: Boolean(ended && duration > 0),
      ...(session.sessionId ? {session_id: session.sessionId,
        sequence: ++session.sequence} : {})
    };
    if (ended && duration <= 0) return;
    session.pending = session.pending
      .catch(() => {})
      .then(async () => {
        const res = await fetch("/api/videos/" + encodeURIComponent(session.video.id) + "/progress", {
          method: "PUT", headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload), keepalive: true
        });
        if (!res.ok) throw new Error("Progress save failed");
        return res.json();
      })
      .then(data => {
        if (current === session && session.active) {
          session.progress = data;
          if (ended) status("Finished! Watch progress saved locally.");
        }
      })
      .catch(() => {
        if (current === session && session.active) status("Progress couldn't be saved. Playback continues.");
      });
  }

  function onState(session, event) {
    if (!session.active) return;
    const YT = window.YT;
    if (!YT) return;
    const code = event.data;
    if (code === YT.PlayerState.PLAYING) {
      session.playing = true;
      try { session.lastPosition = event.target.getCurrentTime(); } catch (_) { session.lastPosition = null; }
      session.lastWall = performance.now();
      if (!session.timer) session.timer = setInterval(() => sample(session), INTERVAL_MS);
      status("Tracking playing time locally. Pauses and skips don't count as watched time.");
    } else if (code === YT.PlayerState.PAUSED || code === YT.PlayerState.BUFFERING
               || code === YT.PlayerState.ENDED) {
      if (session.playing || code === YT.PlayerState.ENDED) sample(session, code === YT.PlayerState.ENDED);
      session.playing = false;
      clearInterval(session.timer);
      session.timer = null;
    }
  }

  function fallback(session, position) {
    if (!session.active || session !== current) return;
    session.player = null;
    $("player-wrap").replaceChildren(makeFrame(session.video, position, false));
    status("Video playback is available, but progress tracking is unavailable (player API blocked).");
  }

  async function open(video, saved, sessionId = null) {
    close();
    const position = Number(saved?.resume_position) || 0;
    const duration = Number(saved?.duration_seconds) || 0;
    const resume = position >= 5 && (!duration || position < duration - 12) ? position : 0;
    const session = {
      id: ++nextSessionId, video, active: true, player: null, timer: null,
      playing: false, lastWall: null, lastPosition: null,
      pending: Promise.resolve(), progress: saved,
      duration, completed: false, sessionId, sequence: 0
    };
    current = session;
    $("player-dialog").showModal();
    $("player-wrap").replaceChildren();
    status(resume > 0 ? "Resuming from " + Math.floor(resume) + " seconds…" : "Loading video player…");

    try {
      const YT = await loadIframeAPI();
      if (!session.active || session !== current) return;
      const frame = makeFrame(video, resume, true);
      $("player-wrap").replaceChildren(frame);
      session.player = new YT.Player(frame, {
        events: {
          onReady: (event) => {
            if (!session.active) return;
            event.target.playVideo();
          },
          onStateChange: (event) => onState(session, event),
          onError: () => { if (session.active) status("YouTube can't play this video in an embed."); }
        }
      });
    } catch (_) {
      fallback(session, resume);
    }
  }

  function close() {
    if (!current) return Promise.resolve();
    const session = current;
    if (session.playing) sample(session);
    session.active = false;
    session.playing = false;
    clearInterval(session.timer);
    session.timer = null;
    current = null;
    try { if (session.player && session.player.destroy) session.player.destroy(); } catch (_) {}
    return session.pending.catch(() => {});
  }

  // On page teardown, preserve the latest approximate position when possible.
  window.addEventListener("pagehide", () => {
    if (current && current.playing) {
      const session = current;
      // A final queued request may be skipped if the browser is closing; regular
      // heartbeat saves limit how much progress can be lost.
      sample(session);
    }
  });

  window.LocalTubePlayer = {open, close};
})();
