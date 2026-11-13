# Spotify → YouTube Downloader

A small command-line tool that downloads songs from Spotify links by finding the matching video on YouTube and saving it as **MP3** or **MP4**.

No Spotify API key or login is required — track metadata is scraped from the public Spotify web page, the best match is searched on YouTube with `pytubefix`, and audio/video is saved locally.

---

## Features

- 🎵 **Single tracks** — `https://open.spotify.com/track/...`
- 📃 **Playlists and albums** — `https://open.spotify.com/playlist/...`, `https://open.spotify.com/album/...`
- 🔗 **Flexible links** — plain URLs, `spotify:` URIs (`spotify:track:<id>`), localized `/intl-xx/` URLs, and `spotify.link` share links
- 🎧 **MP3 or MP4 output**
- 📁 **Choose the download folder** (defaults to `~/Spotify_Downloads`)
- ⏭ **Resume-friendly** — already downloaded tracks are skipped, so re-running a playlist only retries what is missing
- 🔒 **No Spotify account / API credentials**
- 🔁 Sequential downloads (deliberately not parallel — see [Limitations](#limitations))
- 🧩 Works around YouTube's newer stream protections by preferring progressive streams and trying several client identities
- 🎯 Search results are scored by title/artist similarity to avoid covers, live versions and remixes

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.9+ | Tested on Python 3.11 |
| Google Chrome | Used by Selenium to scrape playlists/albums. Selenium Manager downloads the matching driver automatically. |
| Node.js | Needed by `pytubefix` to generate PoTokens for YouTube. |
| Internet access | Spotify (page scraping) + YouTube (search and download). |

Python packages (installed in the next step):

- [`pytubefix`](https://github.com/JuanBindez/pytubefix) — YouTube search/download
- [`moviepy`](https://github.com/Zulko/moviepy) 2.x — MP4 → MP3 conversion (bundles FFmpeg)
- `requests`, `beautifulsoup4` — Spotify page scraping
- `selenium` — playlist/album scraping

---

## Installation

```bash
# 1. (Optional but recommended) create a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. Install dependencies
pip install pytubefix moviepy requests beautifulsoup4 selenium
```

Make sure Chrome and Node.js are installed and available on your `PATH`:

```bash
chrome --version
node --version
```

> Tested on Windows 11 with Python 3.11, `pytubefix` 11.2.1, `moviepy` 2.1.2 and Google Chrome. Both MP3 and MP4 downloads, a full 50-track public playlist, and an album were verified end to end.

---

## Usage

```bash
python sptifyyoutubedonloader.py
```

The program asks for:

1. **Download folder** — leave blank to use `~/Spotify_Downloads`.
2. **Spotify link** — a track, playlist, or album URL.
3. **Format** — `MP3` or `MP4`.

Example session:

```
🎧 Spotify → YouTube Downloader (V2 Final)

📁 Enter download folder (leave blank for default):
> D:\Music
📂 Files will be saved to:
D:\Music

Paste Spotify link (Ctrl+C or 'q' to exit):
> https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
Format (MP3 / MP4): mp3

📡 Processing...
✅ Found 50 track(s)

[1/50]
🎵 KAROL G, Judeline, rusowsky - BbY WOW
...
📁 Files saved to: D:\Music
✅ 50 downloaded, ❌ 0 failed
```

Files are named `Artist - Title.mp3` / `Artist - Title.mp4`. Characters that are invalid in Windows filenames (`\ / : * ? " < > |`), control characters, trailing dots/spaces and reserved names (e.g. `CON`) are sanitized, and overly long names are shortened.

If a file already exists and is not empty, that track is skipped, so re-running a link only downloads what is still missing. Delete the file if you want to download it again.

Once a link is processed, the prompt returns so you can paste the next link. Enter `q`, `quit`, or `exit` (or press `Ctrl+C`) to stop.

### Supported link types

| Link | Behaviour |
|---|---|
| `/track/<id>` | Fast scrape of the track's title/artist |
| `/playlist/<id>` | Headless Chrome scrolls the whole playlist and collects every row |
| `/album/<id>` | Same as playlists |
| `/intl-xx/track/<id>` | Localized URLs are normalized to the plain form |
| `spotify:track:<id>` | Spotify URIs are converted to web URLs |
| `spotify.link/...` | Share links are resolved to the real Spotify URL first |

---

## How it works

1. **Metadata** — the Spotify page is fetched and the title/artist are read from embedded JSON-LD and Open Graph / `music:` meta tags.
2. **Playlists/albums** — headless Chrome opens the page and incrementally scrolls the virtualised track list from the top, collecting each row's title and artists.
3. **YouTube search** — a query like `Artist Title official audio` is searched; the first few results are scored for title/artist closeness, with a bonus for official-audio/Topic channels and a penalty for live/cover/remix versions.
4. **Download** — `pytubefix` tries several client identities (`WEB`, `MWEB`, `ANDROID`, `IOS`, `TV_EMBED`, `ANDROID_VR`) until it gets a *progressive* stream (video + audio in one file). Progressive streams avoid the SABR/PoToken errors that affect separate DASH audio/video streams.
5. **Convert** — for `MP3`, the downloaded MP4 is converted with MoviePy and the temporary file is deleted (even if conversion fails).

If a track fails, the batch continues. Re-run the same link afterwards to retry only the missing tracks.

---

## Limitations

- **MP4 quality is limited to whatever progressive stream YouTube offers** (usually 360p). Higher resolutions use separate video-only DASH streams, which are much more likely to fail with `SABR Maximum reload attempts reached`.
- **YouTube bot detection.** Heavy use can trigger SABR lockouts or empty stream lists. Downloads are sequential for this reason; if many tracks fail, wait and retry later.
- **Spotify login-only content** (private playlists, some region-restricted tracks) cannot be scraped without logging in.
- **Scraping is fragile.** Spotify or YouTube markup changes can break track/playlist extraction until the selectors are updated.
- **Matching is best-effort.** Search results are scored, but an occasional wrong version (live, remix, cover) can still be downloaded.
- **Sequential by design.** Downloads run one at a time; parallel requests make YouTube lock the session faster.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Run `pip install pytubefix moviepy requests beautifulsoup4 selenium` in the same Python environment you run the script with. |
| `Could not start Chrome` | Install Google Chrome. Selenium Manager normally fetches the driver automatically; if it fails, update Chrome and `selenium`. |
| `SABR Maximum reload attempts reached` | YouTube blocked that stream. Retry later; the script tries multiple clients and skips the track instead of aborting the batch. |
| `No progressive (non-SABR) stream available` | Same as above — the video only offers protected DASH streams right now. |
| MP3 conversion error / missing FFmpeg | Reinstall `moviepy` (`pip install -U moviepy`); it ships with `imageio-ffmpeg`. |
| Track found but artist is empty | Spotify changed its markup; update `scrape_track()` fallbacks. |
| The wrong version (live/cover/remix) was downloaded | Delete that file and retry. The search scorer prefers official audio, but matches are still best-effort. |
| A track was skipped unexpectedly | A non-empty file with that name already exists. Delete it to re-download. |
| Garbled emoji / `UnicodeEncodeError` on Windows | Already handled by forcing UTF-8 output; if it persists, run in Windows Terminal or set `PYTHONIOENCODING=utf-8`. |
| Browser window doesn't appear | Playlist scraping runs headless by design; tracks are still collected. |

---

## Project structure

```
spotify-music-downloader/
└── sptifyyoutubedonloader.py   # the whole program
```

Main functions:

| Function | Purpose |
|---|---|
| `normalize_spotify_url(value)` | Converts URLs/URIs (including `intl-` and `spotify:`) to a canonical Spotify URL |
| `resolve_spotify_link(value)` | Normalizes a link, following `spotify.link` share links when needed |
| `scrape_track(url)` | Title/artist from a Spotify track page |
| `scrape_playlist_browser(url)` | All tracks from a playlist/album using headless Chrome |
| `find_youtube(title, artist)` | Best-matching YouTube search result |
| `_best_match(results, title, artist)` | Scores search results to avoid covers/live/remixes |
| `sanitize(name)` | Safe, length-limited filename |
| `get_progressive_stream(url)` | Tries client identities until a progressive stream is found |
| `download(track, fmt)` | Downloads and (for MP3) converts one track; returns success |
| `process(url, fmt)` | Resolves tracks and downloads them sequentially |
| `main()` | Interactive CLI |

---

## Disclaimer

This tool is for personal use only. Downloading copyrighted material may violate Spotify's and YouTube's Terms of Service and local copyright law. Use it only for content you have the rights to download.
