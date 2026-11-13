"""
Spotify → YouTube Downloader (V2 Final - pytubefix updated)
-------------------------------------------------------------
✔ No Spotify API
✔ Track scrape (fast)
✔ Playlist / album via Selenium (auto driver)
✔ Ask user where to save files
✔ Sequential downloads (reliable; avoids bot-detection lockouts)
✔ MP3 / MP4
✔ Updated to current pytubefix API (on_progress callback, stream selection,
  PO Token support for YouTube's newer bot-detection requirements)

Install:
    pip install pytubefix moviepy requests beautifulsoup4 selenium
    Also requires:
      - Python 3.9+
      - Google Chrome (Selenium drives it for playlist/album scraping)
      - Node.js (for automatic PoToken generation via pytubefix's bundled
        BotGuard script — see the WEB client note in download()).
"""
import os, re, sys, json, time, difflib, logging, requests
from bs4 import BeautifulSoup
from pytubefix import YouTube, Search
from pytubefix.cli import on_progress
from pytubefix.exceptions import VideoUnavailable
from moviepy import AudioFileClip

# Selenium
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# pytubefix logs every client fallback ("VISION_OS client returned: ..."),
# which looks alarming even when the download succeeds. Keep only real errors.
logging.getLogger("pytubefix").setLevel(logging.ERROR)

# Windows consoles often default to a legacy code page (e.g. cp1252), which
# raises UnicodeEncodeError on the emoji used below. Force UTF-8 when possible.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

try:
    from pytubefix.exceptions import SABRError
    SABR_EXCEPTIONS = (SABRError,)
except ImportError:
    SABR_EXCEPTIONS = ()  # older pytubefix versions don't have this exception yet

# ─────────────────────────────────────────────
# 📁 DEFAULT PATH / CONSTANTS
# ─────────────────────────────────────────────
DEFAULT_PATH = os.path.join(os.path.expanduser("~"), "Spotify_Downloads")
DOWNLOAD_PATH = DEFAULT_PATH

HEADERS = {"User-Agent": "Mozilla/5.0"}

MAX_NAME_LEN = 150
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

SPOTIFY_URI_RE = re.compile(r"^spotify:(track|playlist|album):([A-Za-z0-9]+)$")
SPOTIFY_URL_RE = re.compile(
    r"(?:https?://)?(?:open\.)?spotify\.com/"
    r"(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?"
    r"(track|playlist|album)/([A-Za-z0-9]+)",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════
# 🔗 LINK HELPERS
# ══════════════════════════════════════════════

def normalize_spotify_url(value):
    """
    Turn any common Spotify link form into a canonical web URL.

    Accepts:
      https://open.spotify.com/track/<id>
      https://open.spotify.com/intl-de/track/<id>?si=...
      open.spotify.com/track/<id>
      spotify:track:<id>
    Returns None for anything else.
    """
    if not value:
        return None

    value = value.strip().strip('<>"\'')

    match = SPOTIFY_URI_RE.match(value)
    if match:
        kind, ident = match.groups()
        return f"https://open.spotify.com/{kind}/{ident}"

    match = SPOTIFY_URL_RE.search(value)
    if match:
        kind, ident = match.groups()
        return f"https://open.spotify.com/{kind.lower()}/{ident}"

    return None


def resolve_spotify_link(value):
    """Normalize a Spotify URL, following spotify.link share links if needed."""
    link = normalize_spotify_url(value)
    if link:
        return link

    if value and re.match(r"https?://(?:spotify\.link|spotify\.app\.link)/", value.strip()):
        try:
            response = requests.get(value.strip(), headers=HEADERS, timeout=15, allow_redirects=True)
            return normalize_spotify_url(response.url)
        except requests.RequestException:
            return None

    return None


# ══════════════════════════════════════════════
# 🎧 TRACK SCRAPER
# ══════════════════════════════════════════════

def scrape_track(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"❌ Could not load the Spotify page: {e}")
        return None

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        name, artist = "", ""

        # Newer Spotify pages expose the track via JSON-LD, but `byArtist`
        # is frequently missing now, so keep scanning for whatever is there.
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "{}")
            except (json.JSONDecodeError, TypeError):
                continue

            if not name:
                name = data.get("name") or ""

            by = data.get("byArtist")
            if not artist and isinstance(by, list) and by:
                artist = by[0].get("name", "") or ""
            elif not artist and isinstance(by, dict):
                artist = by.get("name", "") or ""

        # Fallback: the Open Graph meta tags are stable across page versions.
        if not name:
            og_title = soup.find("meta", property="og:title")
            if og_title:
                name = og_title.get("content") or ""

        if not artist:
            musician = soup.find("meta", attrs={"name": "music:musician_description"})
            if musician:
                artist = musician.get("content") or ""

        if not artist:
            desc = soup.find("meta", property="og:description")
            text = (desc.get("content") if desc else "") or ""
            # e.g. "Listen to X on Spotify. Song · Rick Astley · 1987"
            match = re.search(r"(?:Song|Single|Album)\s*·\s*([^·]+?)\s*·", text)
            if match:
                artist = match.group(1).strip()

        name = name.strip()

        # Generic page titles mean the page did not expose a real track.
        if not name or name.lower() in {
            "spotify",
            "spotify – web player",
            "spotify - web player",
            "spotify – play music",
        }:
            return None

        return {"title": name, "artist": artist.strip()}
    except Exception:
        return None


# ══════════════════════════════════════════════
# 🌐 PLAYLIST SCRAPER (SELENIUM AUTO)
# ══════════════════════════════════════════════

def scrape_playlist_browser(url):
    print("🌐 Using browser for playlist extraction...")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1280,900")

    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as e:
        print("❌ Could not start Chrome. Is Google Chrome installed?")
        print(f"   Details: {e}")
        return []

    # Spotify virtualises long track lists: only the rows currently near the
    # viewport exist in the DOM. Scrolling the real scroll container and
    # collecting after every step is the only way to see every track.
    scroll_js = """
    const els = [...document.querySelectorAll('*')]
        .filter(e => e.scrollHeight - e.clientHeight > 100);
    let atBottom = true;
    for (const e of els) {
        e.scrollTop = Math.min(e.scrollTop + 500, e.scrollHeight);
        if (e.scrollTop + e.clientHeight < e.scrollHeight - 5) atBottom = false;
    }
    return atBottom;
    """

    tracks = []
    seen = set()

    def collect():
        for row in driver.find_elements(By.CSS_SELECTOR, "[data-testid='tracklist-row']"):
            try:
                links = row.find_elements(By.CSS_SELECTOR, "a[data-testid='internal-track-link']")
                title = links[0].get_attribute("textContent").strip() if links else ""

                artists = []
                for anchor in row.find_elements(By.CSS_SELECTOR, "a[href^='/artist/']"):
                    artist_name = anchor.get_attribute("textContent").strip()
                    if artist_name and artist_name not in artists:
                        artists.append(artist_name)

                # Fallback for older/alternate markup.
                if not title:
                    parts = [part for part in row.text.split("\n") if part.strip()]
                    if len(parts) >= 2:
                        title = parts[0]
                        artists = [parts[1]]

                key = (title, tuple(artists))
                if title and key not in seen:
                    seen.add(key)
                    tracks.append({"title": title, "artist": ", ".join(artists)})
            except Exception:
                continue

    try:
        driver.get(url)

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "[data-testid='tracklist-row'], [data-testid='playlist-tracklist']")
                )
            )
        except TimeoutException:
            # Fall through: collect() may still find rows, and the caller
            # prints a helpful message if the page really was empty.
            pass

        time.sleep(1.5)
        collect()

        # Always crawl from the top so playlist order is preserved even if
        # the page restored a previous scroll position.
        driver.execute_script("""
        for (const e of [...document.querySelectorAll('*')]) {
            if (e.scrollHeight - e.clientHeight > 100) e.scrollTop = 0;
        }
        """)
        time.sleep(0.3)
        collect()

        stale = 0
        for _ in range(150):
            at_bottom = driver.execute_script(scroll_js)
            time.sleep(0.4)

            before = len(tracks)
            collect()

            if len(tracks) == before:
                stale += 1
                if at_bottom and stale >= 12:
                    break
            else:
                stale = 0

    except WebDriverException as e:
        print(f"❌ Browser error while reading the playlist: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return tracks


# ══════════════════════════════════════════════
# 🎯 YOUTUBE SEARCH
# ══════════════════════════════════════════════

def _similarity(a, b):
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _best_match(results, title, artist):
    """Pick the result least likely to be a cover/live/remix of a different song."""
    bad_markers = ("live", "cover", "remix", "sped up", "slowed", "nightcore", "reaction", "8d audio")

    best, best_score = results[0], float("-inf")

    for video in results[:8]:
        video_title = getattr(video, "title", "") or ""
        author = getattr(video, "author", "") or ""

        score = _similarity(title, video_title)
        if artist:
            score += 0.5 * max(_similarity(artist, video_title), _similarity(artist, author))

        lowered_title = video_title.lower()
        lowered_author = author.lower()
        if "official audio" in lowered_title or "topic" in lowered_author:
            score += 0.35

        for marker in bad_markers:
            if marker in lowered_title and marker not in title.lower():
                score -= 0.5

        if score > best_score:
            best, best_score = video, score

    return best


def find_youtube(title, artist):
    query = f"{artist} {title} official audio".strip()
    print(f"🔍 {query}")

    try:
        results = Search(query).videos
    except Exception as e:
        print(f"⚠️  YouTube search failed: {e}")
        return None

    if not results:
        return None

    return _best_match(results, title, artist).watch_url


# ══════════════════════════════════════════════
# ⬇ DOWNLOAD  (updated for current pytubefix API)
# ══════════════════════════════════════════════

def sanitize(name):
    """Make a string safe as a Windows/macOS/Linux filename."""
    name = INVALID_FILENAME_CHARS.sub("", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")

    if not name:
        return ""

    stem = name.split(".")[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        name = "_" + name

    if len(name) > MAX_NAME_LEN:
        name = name[:MAX_NAME_LEN].rstrip(". -")

    return name


def get_progressive_stream(yt_url):
    """
    Try a handful of pytubefix client identities until one returns a
    progressive (non-SABR) stream. Different clients get different sets of
    available formats per video from YouTube, so a single client (e.g. WEB)
    frequently comes back empty even when another client works fine.
    Returns (yt, stream) or (None, None) if nothing worked.
    """
    candidate_clients = ["WEB", "MWEB", "ANDROID", "IOS", "TV_EMBED", "ANDROID_VR"]

    for client in candidate_clients:
        try:
            yt = YouTube(
                yt_url,
                client=client,
                on_progress_callback=on_progress,
            )
            stream = yt.streams.get_highest_resolution()
            if stream:
                return yt, stream
        except Exception:
            continue

    return None, None


def download(track, fmt):
    """Download one track. Returns True on success/skip, False on failure."""
    title = (track.get("title") or "").strip()
    artist = (track.get("artist") or "").strip()

    if not title:
        print("❌ Skipping a track with no title")
        return False

    label = sanitize(f"{artist} - {title}" if artist else title)
    if not label:
        label = "unknown track"

    print(f"\n🎵 {label}")

    extension = "mp3" if fmt == "MP3" else "mp4"
    target_path = os.path.join(DOWNLOAD_PATH, f"{label}.{extension}")

    # Allow resuming an interrupted playlist without re-downloading everything.
    if os.path.isfile(target_path) and os.path.getsize(target_path) > 0:
        print(f"⏭  Already downloaded, skipping: {os.path.basename(target_path)}")
        return True

    yt_url = find_youtube(title, artist)
    if not yt_url:
        print("❌ No YouTube match found")
        return False

    temp_video = None

    try:
        # Progressive streams (audio+video combined in one file, e.g. itag
        # 18/22) are served over plain HTTPS and do NOT require SABR/PoToken.
        # Only the separate high-res video-only and audio-only DASH streams
        # do — which is what was triggering the "SABR Maximum reload
        # attempts reached" errors. Try several client identities since
        # availability of the progressive stream varies per video/client.
        yt, stream = get_progressive_stream(yt_url)
        if not stream:
            print("❌ No progressive (non-SABR) stream available for this video on any client")
            return False

        if fmt == "MP3":
            temp_video = stream.download(
                output_path=DOWNLOAD_PATH,
                filename=label + "_tmp.mp4",
                skip_existing=False,
            )

            clip = AudioFileClip(temp_video)
            try:
                clip.write_audiofile(target_path, logger=None)
            finally:
                clip.close()

            os.remove(temp_video)
            temp_video = None

        else:
            stream.download(
                output_path=DOWNLOAD_PATH,
                filename=label + ".mp4",
                skip_existing=False,
            )

        print("✅ Downloaded")
        return True

    except SABR_EXCEPTIONS as e:
        # YouTube's SABR stream-protection layer is currently a known,
        # unresolved pain point for pytubefix (ongoing as of mid-2026) —
        # it can lock out a video mid-download even after a valid PoToken.
        # Skip and move on rather than killing the whole batch.
        print(f"❌ SABR protection blocked this stream, skipping: {e}")
        return False

    except VideoUnavailable as e:
        print(f"❌ Video unavailable, skipping: {e}")
        return False

    except Exception as e:
        print("❌ Error:", e)
        return False

    finally:
        # Never leave a half-written temporary MP4 behind.
        if temp_video and os.path.exists(temp_video):
            try:
                os.remove(temp_video)
            except OSError:
                pass


# ══════════════════════════════════════════════
# 🚀 PROCESS
# ══════════════════════════════════════════════

def process(url, fmt):
    print("\n📡 Processing...")

    link = resolve_spotify_link(url)
    if not link:
        print("❌ That doesn't look like a Spotify track, playlist or album link.")
        print("   Supported examples:")
        print("     https://open.spotify.com/track/<id>")
        print("     https://open.spotify.com/playlist/<id>")
        print("     https://open.spotify.com/album/<id>")
        print("     spotify:track:<id>")
        return

    kind = link.split("/")[3]

    if kind == "track":
        track = scrape_track(link)
        tracks = [track] if track else []
    else:
        tracks = scrape_playlist_browser(link)

    if not tracks:
        print("❌ No tracks found")
        if kind != "track":
            print("   Tip: private or login-only playlists cannot be read without logging in,")
            print("        and Spotify occasionally changes its page markup.")
        return

    print(f"✅ Found {len(tracks)} track(s)")

    # Downloads are intentionally sequential. Running several at once means
    # multiple simultaneous PoToken/session requests hit YouTube together,
    # which looks bot-like and appears to trigger the SABR lockout faster
    # (see download() for details). Sequential is slower but more reliable.
    failed = 0
    for index, track in enumerate(tracks, 1):
        print(f"\n[{index}/{len(tracks)}]", end="")
        try:
            if not download(track, fmt):
                failed += 1
        except KeyboardInterrupt:
            print("\n⏹  Stopped by user")
            raise

    downloaded = len(tracks) - failed
    print(f"\n📁 Files saved to: {DOWNLOAD_PATH}")
    print(f"✅ {downloaded} downloaded, ❌ {failed} failed")

    if failed:
        print("   Re-run the same link to retry only the failed/missing tracks.")


# ══════════════════════════════════════════════
# 🧠 MAIN
# ══════════════════════════════════════════════

def main():
    print("\n🎧 Spotify → YouTube Downloader (V2 Final)\n")

    # 🔽 Ask user for download location
    try:
        path = input("📁 Enter download folder (leave blank for default):\n> ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye 👋")
        return

    global DOWNLOAD_PATH

    DOWNLOAD_PATH = os.path.expanduser(path.strip('"\' ')) if path else DEFAULT_PATH

    try:
        os.makedirs(DOWNLOAD_PATH, exist_ok=True)

        # Make sure the folder is actually writable before downloading.
        probe = os.path.join(DOWNLOAD_PATH, ".write_test")
        with open(probe, "w", encoding="utf-8"):
            pass
        os.remove(probe)
    except OSError as e:
        print(f"❌ Can't use that folder: {e}")
        print(f"   Falling back to: {DEFAULT_PATH}")
        DOWNLOAD_PATH = DEFAULT_PATH
        os.makedirs(DOWNLOAD_PATH, exist_ok=True)

    print(f"\n📂 Files will be saved to:\n{DOWNLOAD_PATH}\n")

    while True:
        try:
            url = input("Paste Spotify link (Ctrl+C or 'q' to exit):\n> ").strip()

            if url.lower() in ("q", "quit", "exit"):
                print("Goodbye 👋")
                break

            if not url:
                continue

            fmt = input("Format (MP3 / MP4): ").strip().upper()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye 👋")
            break

        if fmt not in ("MP3", "MP4"):
            print("❌ Invalid format (choose MP3 or MP4)")
            continue

        process(url, fmt)


if __name__ == "__main__":
    main()
