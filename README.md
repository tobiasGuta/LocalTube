# LocalTube

**Your videos, your algorithm.** A local-first YouTube video library and topic-based recommender, built for Fedora and designed for later browser access from phones and TVs.

## Start on Fedora

```bash
git clone https://github.com/tobiasGuta/LocalTube.git
cd LocalTube
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Stop with Ctrl+C. Your private SQLite database is created at `data/localtube.db` and ignored by Git. Python 3.10+ required.

## Version 0.1

- Save YouTube URLs with your own titles and topic tags, without signing into Google.
- Search your saved video library; record play clicks and rate videos as liked/not interesting.
- See explainable, tag-based recommendations from **your own saved videos**.
- Only load YouTube's privacy-enhanced video embed after you click Play.

**Limits:** This does **not** automatically discover new YouTube videos yet. Play clicks are not proof of completed watches. Embedded playback still contacts Google's servers; privacy-enhanced mode is not anonymity. The homepage itself does not load Google resources.

## Security

This prototype has **no login or access controls**. Keep it on `127.0.0.1` for now, rather than binding to `0.0.0.0` or port-forwarding. Cross-device access and Tailscale are planned *after* authentication and security hardening. The app uses a local SQLite DB; no LocalTube tracking or analytics.

## Roadmap

1. Local library and recommendation MVP (current).
2. Optional video discovery from sources you select, with privacy controls.
3. Cross-device web interface, authentication and Tailscale access.
4. Import/export and dedicated server deployment.

## Test

```bash
python -m pip install pytest httpx
python -m pytest -q
```

Licensed under the MIT License.
