# ClipRelay

Docker service for relaying social posts and media to Telegram. The backend is
FastAPI served by Uvicorn; the web interface is built with Vue 3 and Vite.

- Publishes individual TikTok videos to Telegram after caption editing.
- Downloads Instagram videos and image posts, including carousels, and publishes them to Telegram after caption editing.
- Downloads X / Twitter videos and photo posts, and sends text-only posts as
  Telegram messages.
- Downloads Reddit videos, images, galleries, and text posts.
- Accepts TikTok channels and lets you publish or skip existing videos.
- Automatically monitors configured TikTok channels.
- Downloads YouTube videos and thumbnails in the best available quality.
- Publishes YouTube links with thumbnails and prepared Telegram captions.
- Downloads Spotify tracks directly from Spotify, converts them to 320 kbps MP3,
  and publishes the audio to Telegram after caption editing.

## Setup

Initial settings are stored in the local `config.yaml`. On first startup, the
Telegram token and channel list are imported into `data/state.sqlite3` for the
administrator account `boyd`. The first time you open the web interface, enter
`web.username` and `web.password` from `config.yaml` to confirm that you control
the deployment, then set the permanent password for `boyd`. These config values
are one-time bootstrap credentials, not the normal login after setup.

```bash
cp config.example.yaml config.yaml
```

Fill at least these values in `config.yaml`:

```yaml
telegram:
  bot_token: "123456789:bot_token"
  chat_id: "@my_channel"
  channels:
    - name: "Main channel"
      chat_id: "@my_channel"
    - name: "Test channel"
      chat_id: "@my_test_channel"
web:
  host: 0.0.0.0
  port: 8080
  username: "setup-admin"
  password: "replace-with-a-long-random-bootstrap-password"
```

`telegram.chat_id` is used as the default destination for automatic monitoring.
`telegram.channels` defines the initial channel list. Add the bot as an
administrator to every channel, then start the service:

```bash
docker compose up -d --build
docker compose logs -f
```

Open `http://127.0.0.1:6767`. For TikTok, paste a video or channel link. For
Instagram, paste a video, reel, or post link. X / Twitter accepts status links;
Reddit accepts post, gallery, share, and `redd.it` links. For YouTube, paste a video link:
the thumbnail preview appears automatically, followed by buttons for downloading
the video, downloading the thumbnail, and preparing the Telegram post. For
Spotify, paste a link to an individual track to preview its cover and artist,
download an MP3 with in-button progress, or prepare a Telegram post. The same
track can be requested directly in a Telegram chat:

```text
/spotify https://open.spotify.com/track/…
/spotifysearch approximate track or artist name
/instagram https://www.instagram.com/p/…
/x https://x.com/user/status/…
/twitter https://x.com/user/status/… (alias for /x)
/reddit https://www.reddit.com/r/community/comments/…/…
```

You can also reply to a message containing a link with `/spotify` or
`/instagram`, `/x`, `/twitter`, or `/reddit`, or reply with `/spotifysearch` to
a message containing an approximate track title. Spotify search uses the authenticated Spotify catalog,
selects its highest-ranked track, and then downloads the original Spotify stream
in the same way as a direct link. Spotify sends the same pair of messages as the
web interface: the cover with the post caption, followed by the MP3 with track
metadata and cover thumbnail. Instagram sends the post video or all carousel
images with the author and description. X / Twitter and Reddit send every
downloaded video in a media group, preserve image galleries, and send text-only
posts with Telegram's 4096-character message limit.

## Local development

Install the backend and test dependencies, then build the Vue application:

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt  # Windows
cd frontend
npm ci
npm run build
```

Run `python -m app.main` from the repository root after creating `config.yaml`.
For Vue hot reload, keep the FastAPI backend on port `8080` and run
`npm run dev` inside `frontend`; the Vite development server proxies API and
static-asset requests to FastAPI.

Run the backend suite with `.venv/Scripts/python -m pytest -q` and rebuild the
frontend with `npm run build` before deployment.

Telegram commands work only in destinations already saved through ClipRelay's
settings or channel discovery. A bot membership update or a first command never
registers a chat automatically.

Each user has separate Telegram destinations, TikTok monitoring settings, and
cookies. In the "Telegram settings and cookies" section, you can:

- add a public channel by `@handle` or a private channel by numeric ID such as
  `-1001234567890`, with a display name and bot token;
- automatically discover and add channels for a bot token after the bot is made
  an administrator and a new post is published in the channel;
- replace TikTok cookies;
- replace Instagram cookies;
- replace YouTube cookies;
- replace Spotify cookies;
- replace X / Twitter cookies;
- replace Reddit cookies.

Public Telegram channels are stored and displayed by `@handle`. Private
channels are stored by numeric ID; the destination picker shows that ID so
chats with identical names remain distinguishable. Channels can be searched and
removed in settings. Search is also available when choosing a destination for
publishing. The Telegram command menu is configured
automatically on startup. Command polling uses `getUpdates`, so the same bot
cannot have an active webhook at the same time.

The TikTok, Instagram, X / Twitter, Reddit, and YouTube post builder supports Telegram HTML captions,
including bold, italic, underline, strikethrough, spoiler, links, inline code,
code blocks, quotes, and expandable quotes. For TikTok and Instagram posts, the
author and description can also be disabled separately.

Tokens and cookies uploaded through the web interface are stored inside the
`data` directory, grouped by user, and excluded from Git.

`config.yaml` is excluded from Git. Do not share it because it contains the
Telegram bot token.

## Web Interface

The Vue/Vite web interface uses built-in username/password accounts backed by
the FastAPI application. The initial administrator is `boyd`; confirm the
one-time bootstrap credentials and set the password on the first visit. Admin users can
open the admin panel, view users with pagination, disable users, edit each
user's settings, and disable access to TikTok, Instagram, YouTube, Spotify,
X / Twitter, or Reddit. If a service is disabled for a user, its
upload/download UI is hidden and cookies for that service cannot be uploaded.

By default, Docker exposes the interface only on `127.0.0.1:6767`.
If you change the Compose port binding to `0.0.0.0` or otherwise expose the app,
set unique, strong `web.username` and `web.password` values before first startup.
Do not expose an installation whose bootstrap credentials are blank or still
use the example values.

## TikTok Cookies

Public TikTok videos usually do not require credentials. If TikTok requires
authorization, export browser cookies in Netscape format to `cookies.txt`,
uncomment the volume in `compose.yaml`, and configure:

```yaml
tiktok:
  cookies_file: tiktok-cookies.txt
```

The service does not need your TikTok login or password.

## Instagram Cookies

Instagram often restricts downloads without authorization. If you see
`Requested content is not available, rate-limit reached or login required`,
export browser cookies for Instagram in Netscape format and configure:

```yaml
instagram:
  cookies_file: instagram-cookies.txt
```

The file can also be updated from the web interface settings without manually
restarting the service.

## YouTube Cookies

YouTube can require a signed-in session to confirm that the request is not from
a bot. In that case, export browser cookies for YouTube to
`youtube-cookies.txt` and configure:

```yaml
youtube:
  cookies_file: youtube-cookies.txt
```

YouTube often rotates cookies for open tabs. For a stable export:

1. Open a separate incognito window and sign in to YouTube.
2. In that same single incognito tab, open `https://www.youtube.com/robots.txt`.
3. Export cookies for the `youtube.com` domain to `youtube-cookies.txt`.
4. Close the incognito window immediately and do not reuse that session.
5. Recreate the container with `docker compose up -d --force-recreate`.

## Spotify Cookies

Spotify downloads use the authenticated Spotify player session and the original
Spotify CDN stream; YouTube and other replacement audio sources are not used.
The `vorbis-high` stream is requested directly and converted to MP3 at 320
kbps with `ffmpeg`. An active Spotify Premium account is required for the
high-quality stream.

Export cookies for `open.spotify.com` in Netscape format after signing in. The
file must contain `sp_dc`. Upload it in the web interface settings, or configure
an initial file:

```yaml
spotify:
  cookies_file: spotify-cookies.txt
```

For Docker, also uncomment the corresponding `spotify-cookies.txt` volume in
`compose.yaml`. This is not needed when the file is uploaded through the web
interface.

Use only tracks you are authorized to save. Automated downloading may conflict
with Spotify's terms, and third-party player tools can put the Spotify account
used for cookies at risk.

## X / Twitter and Reddit Cookies

Public X / Twitter and Reddit posts normally work without credentials. Upload
Netscape-format cookies in settings when a post requires an authenticated
session, or configure initial files:

```yaml
twitter:
  cookies_file: twitter-cookies.txt
reddit:
  cookies_file: reddit-cookies.txt
```

The X adapter first uses the public syndication metadata endpoint and falls back
to the uploaded cookies for restricted posts. Reddit uses the post's JSON
representation for images, galleries, text, and metadata; videos are downloaded
with yt-dlp. The corresponding optional Docker volume examples are in
`compose.yaml`.

## Automatic Monitoring

State, temporary downloads, and working cookie copies are stored in the local
`data` directory. The directory is mounted into the container as a bind mount
and excluded from Git, so videos are not published again after restart.
`tiktok.channels` can be left empty if automatic monitoring is not needed.

TikTok does not provide an accessible webhook for new videos, so the service
uses periodic polling. Telegram Bot API accepts bot-uploaded videos up to 50 MB.

YouTube videos are downloaded in the best available quality. If the best video
and audio tracks are separate, the service merges them with `ffmpeg`.

For videos where YouTube requires a PO Token, Compose automatically starts the
internal `bgutil-provider`. You do not need to obtain or refresh PO Tokens
manually.
