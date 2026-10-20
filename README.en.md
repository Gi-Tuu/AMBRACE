# AMBRACE (拥爱)

English | [简体中文](README.md)

> **Note:** This English translation is kept in sync with the authoritative Chinese [`README.md`](README.md) as of **v3.5.5**. If you find any discrepancy, the Chinese version prevails — please open an issue to let us know.

A self-hosted AI companion app: configure multiple AI characters who chat with you, keep memories, post Moments, write diaries, and raise pets; enjoy real-time voice calls, optionally enable phone perception (screen reading / clipboard / album / notifications), and even connect characters to external chat bots so you can talk to them anywhere. **All your data stays on your own server.**

**Current version**: v3.5.5 ｜ Version-by-version changes are in [`docs/changelog.md`](docs/changelog.md) (also the data source for the in-app "What's New" panel) and on the [Releases page](https://github.com/Gi-Tuu/AMBRACE/releases).

## Features

### Clients
- **Android app**: full feature set (background keep-alive, system notifications, phone perception)
- **Web (PWA, basic experience verified locally)**: login / characters / chat / read-only browsing / text attachments / character creation and basic editing are all verified locally; the backend does not mount the entry by default, and public-network, iPhone and real-device use are not verified yet

### Chat & Voice
- **Whole-sentence rendering**: AI replies appear as **complete semantic chunks** (no more character-by-character typewriter stutter on weak networks); while generating, a **blinking caret is shown exactly where the message will land**, and disappears as soon as the first sentence arrives. Backed by a weak-network watchdog, send de-duplication, no duplicates after reconnect, and SSE keepalive.
- **Sentence-by-sentence voice**: speech is synthesized and played sentence by sentence, streaming as it generates; interruption works instantly at any time (turn_id debounce), and voice replies never read out prefixes like "Name (expression):".
- **Real-time voice calls**: push-to-talk / auto-listening; server-side VAD filters silence and ultra-short frames; streaming vs. full-segment ASR is switchable.
- **Image understanding**: local OCR text recognition plus a cloud vision API (OpenAI-compatible; you configure the endpoint and key); **image binaries never reach the chat LLM**.
- **AI image generation**: ask the AI to generate images inside chat (requires your own image-generation service; can be set to "ask every time").
- **Batch sending**: continuous mode collects your messages first, then submits them all at once; multiple messages are merged into context by paragraph.
- **Read state & unread dots**: while you are in the conversation, an incoming reply is marked read immediately (no waiting for the next polling round, no flicker-back); read state is also recorded once when you leave the chat or the app goes to background.

### Characters & Companionship
- **Multiple characters**: custom AI characters (personality / avatar / relationship web: friends, partners, etc.), with per-character model and permission settings.
- **Memory system**: bge-m3 embeddings + BM25 hybrid retrieval; memory chains (past events threaded by time), tiered retrieval, an Ebbinghaus-style decay curve, and the Weave / Memory Book visualizations.
- **Remembering accurately matters more than remembering a lot**: the same event is never stored twice (new phrasing is merged onto the old entry, with an auditable record of who changed it); corrected old content is automatically collapsed and no longer treated as the current state.
- **World Settings (long-term knowledge)**: facts / constraints / preference profiles / relationship baselines a character keeps long-term. The page is split into two groups — **"My settings" and "Noted by TA · pending your confirmation"** — and every entry can be edited or deleted, with **multi-select bulk deletion**.
  - Only what you personally wrote counts as "immutable"; anything the character compiles on their own is marked **pending your confirmation** and is never used as a premise every turn.
  - High-risk domains (health, medication, finances, legal matters, personal safety) are referenced only if you explicitly stated or confirmed them — so a figure of speech can never be mistaken for a health fact and extrapolated from.
  - Duplicate entries are merged (paraphrase is recognized by an on-device embedding model — nothing is uploaded, nothing is billed), and each category has an entry cap (overflow only makes room; no data is deleted).
  - **Editing and saving an entry on the page means "I confirm this one"**: it takes effect immediately, keeps its tier, and the same claim won't be recorded again afterward.
- **Lorebook / World Book**: trigger-based setting injection with keywords / regex / probability / mutually exclusive groups / sticky entries / cooldowns.
- **On-demand memory retrieval**: time search, two-hop association, stable knowledge consolidation, prospective intentions (promises and wishes you mentioned are remembered and brought up on time), and memory pack (`.mempak`) export / import.
- **What you ask for actually gets done**: saying "note this down for me" or "don't schedule anything those days" lands in the memo / calendar instead of being merely acknowledged in a reply.
- **Proactive messages**: characters reach out on their own, triggered by missing you or by state changes, with a naturalness score and daily-rhythm learning; promises like "going to a meeting", "taking a shower", "will try later" are automatically registered for follow-up — they check in naturally at the right time, and stop asking once you've reported the result.
  - **Undelivered doesn't count as "already sent"**: proactive messages that are blocked or fail generation are retried later (greetings up to 3 retries a day) instead of being silently dropped forever.
  - **Nighttime & disturbance restraint**: quiet hours, anniversaries, life reports, and automatic group chatter are all throttled — no chain of reminders in the middle of the night.
- **Diary / Moments**: a first-person diary is generated for each character every day, and **the nightly settlement fills in events that happened later that day**; characters post updates and **comment on each other's posts** (several friends can chime in under one post, and replies keep going), with multi-user data isolation.
- **Home & pets**: pixel-art home (multiple rooms, furniture freely placed and edited); origami-style pets to adopt / feed / play with / clean.

### Group Chat & Games
- **Group chat**: multi-character family group chats; @-mention a character for a guaranteed reply; per-character talkativeness and muting; a three-layer funnel for picking who speaks (@ must-reply → probabilistic activation → random fallback); group memories are attributed by speaker.
- **Group games**: 6 titles (Undercover / Truth or Dare / 20 Questions / Werewolf / Liars Bar / Turtle Soup). The rules engine is separated from the AI, private information is isolated to prevent cheating, the game is pushed in real time, and a keepsake journal is generated afterward.
- **Session safeguards**: if a single game exceeds decision limits or gets stuck abnormally, it is safely closed out (as a draw) instead of consuming tokens endlessly.

### Appearance
- **7 skins**: Aurora Glass / Native / Warm Companion / Material You / Paper Journal / Neon Night / Aegean Codex (parchment · starlight dual variants).
- **Dark mode & theme colors**: light/dark modes + multiple theme colors + font variants, unified by design tokens, switched with one tap in appearance settings.
- **Loading & motion**: skeleton screens for long lists, message entrance animations, and accessibility reduction (no flashes or jumps when reduced motion is on).

### Extensions & Integrations
- **Plugin system**: page-type / no-code plugins, with local directory scanning and a remote marketplace index (official repository built in; use `PLUGIN_MARKET_URL` to point at your own marketplace or disable it).
- **Example: forum plugin** — characters can visit a forum on their own when you're not chatting (read posts, reply to comments under their own posts, answer private messages, and proactively post). **Disabled by default**; requires a self-hosted forum site and host credentials.
- **Plugin safety gate**: install trusted plugins only, permission confirmation before installation, and traceable sources and checksums.
- **MCP support**: standard Model Context Protocol (stdio / SSE / streamable-http), with resource and prompt discovery and inspectable call logs.
- **External chat channels**: bind chat bots (ClawBot) to characters through a gateway — private messages the bot receives go straight to that character. Supports multiple owners on one device and multiple bots each bound to different characters, with messages and memories distinguishable by source.
- **Phone perception (Android)**: accessibility screen reading, clipboard, recent album images, notification listening, location, and AI-suggested notifications; reports are **split into fields per item** (app / time / battery / filename) instead of being crammed into one truncated sentence.
- **Messaging notifications (Android)**: a foreground service keeps the app alive, with real-time WebSocket push and system notifications; **messages arrive even when the app is backgrounded or the screen is off**. Foreground/background detection uses the conservative rule "only count as foreground when confirmed" — if confirmation can't be obtained, it's treated as background and the notification is posted. After a reboot or data clear, the background service recreates the notification channel on its own.

### Administration & Operations
- **Permissions & accounts**: a three-in-one tab page (AI capability permissions / master account management / server feature switches); the master account manages sub-accounts and usage.
- **Usage reports**: token and call counts broken down by purpose / model / day, with proportion distributions by purpose and by channel; estimated usage is counted separately and never mixed with measured usage.
- **Feature flags**: the flags page is folded into groups with plain-language descriptions; important capabilities can be gray-released and hot-toggled for gradual rollout.
- **Desktop console**: cross-platform (tkinter) start/stop/restart and log viewing; server management goes through `scripts/server_manager.py` (`status` health check, `repair` one-click fix for double instances, `start/stop/restart`).
- **Reliable operation**: Alembic version-chain migrations (cross-version upgrades without data loss), startup readiness (`/ready`) and liveness (`/liveness`) endpoints, resident-task self-checks, self-healing external gateway supervision, and automatic backoff/retry for background maintenance failures.

## Tech Stack
- Backend: FastAPI + SQLAlchemy (async) + SQLite + ChromaDB + bge-m3 (local embeddings) + BM25 hybrid retrieval (jieba + rank-bm25).
- Frontend: Flutter (**Android builds only**; phone perception depends on Android native services), Provider + Dio + WebSocket + flutter_local_notifications + flutter_background_service (background keep-alive).
- LLM: any OpenAI-compatible endpoint (default: DeepSeek; user-level BYOK overrides supported); image understanding uses local OCR plus a cloud vision API (no local model required).
- Voice: local faster-whisper transcription (switchable to a streaming ASR endpoint); TTS goes through a self-configured voice service (emotional synthesis) and automatically falls back to a backup chain on failure.
- Deployment: one-click scripts (`setup.bat` / `setup.sh`) and Docker images (multi-arch amd64 / arm64, image `ghcr.io/gi-tuu/ambrace`; models download automatically on first start).

## Project Layout
```
backend/          FastAPI server (business code under app/)
flutter_app/      Flutter client (business code under lib/)
ambrace_core/     Shared Flutter package (message/error/identity/transport primitives for the mobile shell and the web build)
pwa/              Web build (Flutter Web; not mounted by default — served at `/pwa/` only when `PWA_STATIC_DIR` is set)
plugins/          Built-in example plugins and remote marketplace index (examples/ for samples, marketplace/ for the index)
scripts/          Ops scripts (server_manager.py start/stop/health/repair, watchdog.py supervisor, backup.py backups, setup.bat/setup.sh one-click deploy, uninstall.bat/uninstall.sh removal, build_apk.bat/build_apk.sh one-click APK packaging, get_server_info.py server address, init_db.py manual table rebuild, download_models.py embedding model download, verify.py one-click verification)
server_controller/ Desktop console (cross-platform tkinter: on Windows double-click start_controller.vbs; on Linux/macOS run bash start_controller.sh)
docs/             Development docs (architecture / plans / landscape; except changelog.md — the in-app "What's New" data source — these are not distributed in the open-source package)
```

## Quick Start

### 1. Docker (recommended if you already have Docker)
- Pull the image: `docker pull ghcr.io/gi-tuu/ambrace:latest`; or run it directly with `docker-compose.ghcr.yml` from the repo root (no local build needed): `docker compose -f docker-compose.ghcr.yml up -d`
- To build the image yourself (and modify the code): `docker compose up -d` (uses the default `docker-compose.yml` with `build: .`)
- The embedding model (~542 MB) downloads automatically on first start; data / configuration persist via volumes.
- The container listens on port **8000**; health check: `http://127.0.0.1:8000/api/v1/system/ready`
- Note: the first model download requires internet; for offline use, mount the `backend/models` volume yourself.

### 2. Backend (one-click deploy, recommended)
- Windows: run `setup.bat` (double-click it or run it from the repo root)
- Linux / macOS: run `bash setup.sh`

The script creates the virtual environment, installs dependencies, generates `.env` (leave everything blank for defaults), and self-checks the model directory.

> ⚠️ **Models**: the `bge-m3` embedding model (required by the memory system; local ONNX, 1024d, ~542 MB) is not included in git. The `whisper-small` transcription model is optional — voice transcription degrades gracefully when it's missing. To fetch them: run `python scripts/download_models.py` for the embedding model (offline machines can place it at `backend/models/bge-m3` manually). To distribute a complete package including models across machines, generate one locally with `python scripts/make_release.py --zip` (initial releases include a complete package; later revisions do not refresh it — see section 5).
>
> 🎤 **Voice (optional)**: voice messages and real-time voice calls additionally require `pip install -r backend/requirements-voice.txt` (faster-whisper, lazy-loaded; without it, voice messages are stored as audio without transcription and the log prints the install command). TTS for voice replies needs a self-configured voice service, otherwise it falls back automatically.

Manual setup:
```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# Linux/macOS: .venv/bin/pip install -r requirements.txt
cp ../.env.example .env                         # leaving everything blank is fine (recommended)
```
No manual database creation is needed: it is created automatically on first server start (`backend/data/sqlite/`).

LLM keys are recommended to be stored in the database (zero keys in code/.env): after starting, log in with the master account and fill them in under **Settings → API configuration**.
Stored keys are **encrypted at rest** (the master key is kept separately on this machine and never leaves it except inside backup archives).
Or call the management API directly (master account user_id=1 only):
- `PUT /api/v1/system/api-config/server`        server-level LLM (required for chat, recommended)
- `PUT /api/v1/system/image-gen-config/server`  image generation (optional)

Start the server (Windows, recommended — auto-launches the watchdog supervisor):
```bash
python scripts/server_manager.py start
```
Or manually (enter the backend directory first, otherwise `app` can't be imported):
```bash
cd backend
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # Windows
# Linux/macOS: .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Starting on Linux / macOS (server_manager / watchdog are Windows-specific; the desktop console is cross-platform — see **2.4 Linux/macOS deployment & supervision** below):
```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Key `.env` variables (see `.env.example` for details):

| Variable | Description |
|---|---|
| `DEEPSEEK_API_KEY` | LLM API key (optional: prefer storing via the management API; this is the .env fallback) |
| `DEEPSEEK_BASE_URL` | API base URL, default `https://api.deepseek.com` |
| `LLM_MODEL` | Model name |
| `IMAGE_GEN_PROVIDER` | Image provider: `dashscope` (Qwen-Image) or `openai` (optional; storing in the DB recommended) |
| `SERVER_HOST` / `SERVER_PORT` | Listen address, default `0.0.0.0:8000` |
| `DATABASE_URL` | SQLite path, default `./data/sqlite/ai_companion.db` |
| `CHROMA_PERSIST_DIR` | Vector store directory |
| `VLM_ENABLED` | `.env` fallback switch for cloud vision, default `false` (treated as enabled when a vision model is configured in the database) |
| `VLM_API_KEY` | Cloud vision API key (OpenAI-compatible, e.g. Alibaba Cloud DashScope Qwen-VL) |
| `VLM_BASE_URL` / `VLM_MODEL` | Cloud vision endpoint and model name (e.g. `https://xxx/compatible-mode/v1` / `qwen-vl-max`) |

### 2.1 Configuring image understanding (vision / text recognition)
Works out of the box: local OCR text recognition (installed with dependencies, no extra download). Natural-language image understanding uses a **cloud vision API** (the local Ollama branch was removed on 2026-10-17; no local model needed):
- Set `VLM_API_KEY` + `VLM_BASE_URL` in `.env` (an OpenAI-compatible endpoint, e.g. DashScope `https://xxx/compatible-mode/v1`) + `VLM_MODEL` (e.g. `qwen-vl-max`); configuring it in the app's model settings also works and takes priority.

Image binaries are always sent only to local OCR or the vision endpoint you specify — **never** to the chat LLM.

### 2.1.5 Windows Firewall (optional)
If LAN/phone access is blocked, run `scripts\open_firewall.bat` as administrator to open port 8000 (for Linux firewalls see step 5 of section 2.4).

### 2.2 Getting the server address (to enter on the phone)
After starting the server, run `python scripts/get_server_info.py`, which prints the LAN address (use it when phone and server are on the same Wi-Fi): `http://192.168.x.x:8000`. You can also view it in the app or desktop console after logging in (`GET /api/v1/system/status/detail`, login required). The public `/api/v1/system/status` returns only minimal status and never includes the LAN address.

### 2.3 Remote access with Tailscale (reaching the server on your PC across networks)
When the phone and PC aren't on the same Wi-Fi (4G/5G hotspot, or you're away from home), the LAN address is unreachable. **Tailscale** is recommended: it puts both devices on the same encrypted virtual LAN, so port 8000 on your PC becomes reachable as if you were on the same Wi-Fi.

**On the PC (server)**
1. Install Tailscale: https://tailscale.com/download (on Windows use the installer; sign in with a Microsoft/Google/email account)
2. After signing in, run `tailscale ip -4` (or check the IP in the taskbar icon) to get an address like `100.x.y.z`
3. Keep the PC awake with **no sleep**: Windows Settings → System → Power & battery → Screen and sleep → set Sleep to "Never"; on a laptop with the lid closed on external power, also allow it to stay awake, otherwise the server goes offline during remote sessions.

**On the phone**
1. Install the Tailscale app (search your app store) and sign in to the **same account**; if friends need access, add their devices to your network from the admin console at https://login.tailscale.com
2. Turn on Tailscale's VPN switch (status shows connected)
3. In the app, set **Settings → Server address** to `http://100.x.y.z:8000` (replace 100.x.y.z with the PC's Tailscale IP)

**Verification & FAQ**
- Open `http://100.x.y.z:8000/api/v1/system/status` in the phone browser — seeing JSON means it's connected; this also works over 4G/5G (through the encrypted tunnel, no shared Wi-Fi needed)
- Can't connect: first confirm on the PC that `tailscale status` shows online and the server is running (locally visit `http://127.0.0.1:8000/api/v1/system/status`)
- Security note: plain HTTP is suitable only inside Tailscale's private network; to expose the service on the public internet, configure an HTTPS reverse proxy yourself.

### 2.4 Linux/macOS deployment & supervision
> Note: the desktop console (`server_controller/`) is a cross-platform tkinter app that works on Windows / Linux / macOS. `scripts/server_manager.py` and `scripts/watchdog.py` rely on Windows features (pythonw/PowerShell) and are **Windows-only**; on Linux/macOS run and supervise the service as follows, with identical functionality.

- Windows: double-click `server_controller/start_controller.vbs`
- Linux / macOS: `bash server_controller/start_controller.sh` (the console can start/stop/restart the server and view logs)

**1. Install dependencies (requires Python 3.12+, 3.14 also works)**
```bash
bash setup.sh            # creates the venv, installs deps, generates .env, self-checks model dirs
```
Or manually:
```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # leaving everything blank is fine (recommended)
```

**2. Foreground start (quick verification)**
```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
First start creates the database (`backend/data/sqlite/`) and loads the embedding model (~30 seconds). Visiting `http://127.0.0.1:8000/api/v1/system/status` in a browser and seeing JSON means success.

**3. Background supervision (systemd recommended, Linux)**
Create `/etc/systemd/system/ai-companion.service` (replace `/path/to/ambrace` with your extracted directory):
```ini
[Unit]
Description=AMBRACE Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/ambrace/backend
ExecStart=/path/to/ambrace/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-companion
sudo systemctl status ai-companion        # check status
journalctl -u ai-companion -f             # live logs
```

**4. Background supervision on macOS (launchd)**
Create `~/Library/LaunchAgents/com.aicompanion.server.plist`, with `ProgramArguments` pointing to `backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` and `WorkingDirectory` pointing at `backend`; then:
```bash
launchctl load ~/Library/LaunchAgents/com.aicompanion.server.plist
```

**5. Open port 8000 in the firewall**
```bash
# ufw (Ubuntu/Debian)
sudo ufw allow 8000/tcp
# firewalld (CentOS/RHEL/Fedora)
sudo firewall-cmd --permanent --add-port=8000/tcp && sudo firewall-cmd --reload
```

**6. Logs & backups**
- Service logs: use `journalctl -u ai-companion` under systemd; otherwise redirect stdout to a file
- Daily backup: `backend/.venv/bin/python scripts/backup.py` (can pair with cron: `0 3 * * * cd /path/to/ambrace && backend/.venv/bin/python scripts/backup.py`)

**7. LAN / Tailscale connections**
Same as Windows: run `backend/.venv/bin/python scripts/get_server_info.py` for the address; across networks use Tailscale (see section 2.3 — the PC-side steps apply to Linux/macOS as well).

### 3. Frontend (Flutter)
```bash
cd flutter_app
flutter pub get
flutter build apk --release
```
The mirror used for dependency fetching (`flutter pub get`) depends on your local environment; without configuration it defaults to the official `pub.dev`. If access is unstable (e.g. mainland China networks), configure a mirror first:
- Windows (PowerShell): `setx PUB_HOSTED_URL "https://pub.flutter-io.cn"`, then **reopen the terminal**
- Linux/macOS: `export PUB_HOSTED_URL=https://pub.flutter-io.cn` (can be added to `~/.bashrc`)

After installing the APK, enter the server address on the settings page (phone and server on the same LAN, or connected via Tailscale).

### 3.1 Android compatibility
- **Minimum OS**: Android 7.0 (`minSdk` is the greater of Flutter's default and 24, required by Shizuku and the recorder plugin); Android 10+ is recommended for the full experience (phone perception / media reading). `targetSdk` follows the Flutter SDK; builds cover arm64-v8a / armeabi-v7a / x86 / x86_64 (Snapdragon / Dimensity / Kirin / UNISOC chips all supported).
- **Notification permission**: on Android 13+, allow "Notifications" in the system dialog on first launch, otherwise banners/system notifications won't arrive.
- **Location permission**: when enabling geographic location under **Phone perception → Location**, the system shows a location authorization prompt ("Allow only while using the app" recommended); if it fails, check that system location services are on and that the app's location permission isn't blocked by the OEM by default (some Chinese ROMs require manual approval under Settings → Apps → Permissions).
- **Phone perception permissions**: manually enable this app's accessibility service and "notification access" in system settings (paths vary by OEM; common ones: Xiaomi/Redmi: Settings → More settings → Accessibility; HUAWEI/Honor: Settings → Accessibility features → Accessibility; OPPO/vivo/iQOO: Settings → Other settings → Accessibility).
- **Background stability**: add the app to the battery-optimization whitelist and allow auto-start/background activity, otherwise the system may kill the connection and notifications after screen-off (OEM ROM behavior varies; the new notification chain already treats unconfirmed state as background, but allowing background activity is still recommended).
- **Install without building**: skip compilation entirely by installing the APK attached to the Release (same version as the source). Building yourself requires the Flutter and Dart SDKs ≥ 3.6, the Android SDK, and **JDK 17+** (required by AGP 8.3 / Gradle 8.7; Gradle and dependencies download automatically on first build).

### Web basic experience (PWA)

Download `ambrace_pwa.zip` from v3.5.5 or later, extract it, set `PWA_STATIC_DIR` to the absolute path of its `pwa/` directory, and restart the backend. Open `/pwa/` on the same backend origin. The web entry is disabled by default.

To build from source, run `flutter build web --release --base-href /pwa/` inside `pwa/` and point `PWA_STATIC_DIR` at `build/web`. Use HTTPS when deploying. iPhone/Safari installation and real-device behavior remain unverified. Authentication is kept in page memory; refreshing requires signing in again. Basic features include text chat, content browsing, session management, personal notes/diaries, a single text attachment, and character creation/basic editing.

### 4. Phone perception (optional, Android)
- Enable the app's accessibility service and notification access in system settings (names differ across ROMs; e.g. iQOO/OriginOS requires manual background approval)
- Turn on the desired switches on the in-app "Phone perception" page (screen reading / clipboard / album / notifications / location / AI-suggested notifications)
- Battery-optimization whitelist and "allow background start" are needed for stable connections and popups.

### 5. Deploying from GitHub source (without a packaged release)
The source repository differs from the "complete package with models" in two ways; once both are covered, functionality is identical.

1. **Embedding model (required)**: `backend/models/bge-m3` (~542 MB) is not in git. Run `python scripts/download_models.py` to download it (on first start without it the backend errors out, and the `setup.bat/sh` self-check also warns). To distribute a complete package with models across machines, run `python scripts/make_release.py --zip` locally (output directory is set by the `AMBRACE_RELEASE_OUT` env var, defaulting to `release_output/` next to the project root) — **each version initially ships `ai_companion_public.zip`; later revisions of the same version update the APK without refreshing the complete ZIP**.
2. **APK (least effort, recommended)**: just download and install the APK from the Release assets — no Flutter/Android SDK and no configuration needed. To build it yourself: run `scripts\build_apk.bat` (Windows) or `bash scripts/build_apk.sh` (Linux/macOS) for a one-click release build (`--debug` for a debug build), or follow section 3 manually.

> **Signing note**: without a configured keystore, builds automatically use the debug signature, which installs and runs fine (but can't be published to app stores).

Everything else matches "Quick Start": `setup.bat` / `bash setup.sh` one-click backend deploy → run `get_server_info.py` for the address → enter it on the app's settings page → enable phone perception permissions as needed.

### 6. Updating (upgrading to later versions)
Choose based on how you obtained the project:

**Updating from GitHub source**
```bash
git pull                                                             # pull the latest code
backend/.venv/Scripts/python.exe scripts/server_manager.py restart   # Windows
# Linux: sudo systemctl restart ai-companion
# macOS: launchctl kickstart -k gui/$(id -u)/com.aicompanion.server
```
- The database and uploads live in `backend/data/` (not in git) and are never touched by updates; `.env` is preserved as-is
- Database schema changes migrate automatically on startup through the Alembic version chain (smooth cross-version upgrades, no data loss) — no manual migration commands needed
- Re-run `setup.bat` / `bash setup.sh` when dependencies change

**Updating from a complete release package** (generated locally by `scripts/make_release.py --zip`, or a package you kept from before)
1. Extract the new package to a new directory (or overwrite the old one)
2. Preserve data: copy `.env`, `backend/data/`, and `backend/models/` from the old directory to the new one (not needed if you extracted directly over it)
3. Start: `backend\.venv\Scripts\python.exe scripts\server_manager.py start` (use `backend/.venv/bin/python` on Linux/macOS)
4. APK: install the new APK from the Release assets over the old one (login state and data live on the server and are unaffected; the build number increases monotonically, so overlay install works)

**Uninstall**
- Windows: `scripts\uninstall.bat` (keeps data and config by default; append `--purge` to remove everything)
- Linux / macOS: `bash scripts/uninstall.sh` (same behavior)

## Backup & Restore
- **One-click export from the app**: Settings → System → Data backup → "Export backup" packages the SQLite database + config + source snapshot into a zip saved on the phone, with restore instructions included.
- **Automatic server-side backups**: `scripts/backup.py` backs up daily to the `backups/` directory (keeps the latest 14 days) and can also be run manually; it also cleans rotated logs older than 7 days.
- ⚠️ **Backups contain plaintext credentials**: the backup includes the database and `server_config.json`, where model / voice / multimodal API keys are currently stored in **plaintext** (the UI masks them; the database does not). A `README-BACKUP.txt` inside the archive warns: **do not send it to others, do not upload it to cloud drives, do not commit it to any repository**. Encrypted-at-rest storage is on the roadmap.
- **Restore**: stop the server → extract the backup zip over `backend/data` → start the server again.

## Development & Testing
After making changes, pick a command by the scope of what you touched:

```bash
# Backend (inside the backend directory)
.venv/Scripts/python.exe -m pytest tests -q --basetemp=.pytest_tmp      # Windows
.venv/bin/python -m pytest tests -q                                     # Linux / macOS
.venv/Scripts/python.exe -m ruff check app                              # lint

# Frontend (inside the flutter_app directory)
flutter analyze
flutter test

# One-click verification (ruff + byte-compile + backend tests + frontend analyze/test; add --smoke for API smoke tests)
backend/.venv/Scripts/python.exe scripts/verify.py

# API smoke tests (requires a running server)
backend/.venv/Scripts/python.exe scripts/smoke_test.py
```

- **CI**: every push to `main` runs `.github/workflows/ci.yml` — the always-on jobs are Flutter (analyze + test) and backend Linux on Python 3.12 / 3.13 / 3.14; the Windows backend and Docker build jobs are triggered based on what changed (skipped when only frontend and docs are touched).
- **Green locally ≠ green in CI**: CI checks out the file set with internal docs and privacy-excluded files removed; tests that read repository files must pass on that pruned tree. Run `scripts/check_pruned_tree.py` before pushing to pre-run tests on the pruned tree.
- **Public-surface self-check**: `scripts/check_public_leak.py` scans for "author machine paths + credential shapes" against the same exclusion list and exits non-zero on hits; the `public-hygiene` CI job runs on every push.
- **Privacy hard rule**: real user data (conversations, memories, nicknames, pet names, and content derived/rewritten from them) must never enter the public repository; related fixtures and documents always go through the exclusion list, with a guard test asserting they are absent from the public surface.

## Privacy
- Images go through local OCR text recognition by default; with vision enabled, image binaries are sent only to the vision API endpoint you configured — **never** to the chat LLM.
- All data is stored on your own server by default (SQLite + local vector store).
- Phone perception data is used only to inject AI context; this repository contains no collection or telemetry upload.
- Semantic de-duplication and vector retrieval both use **on-device models**; "similarity checks" never call external services.
- Model / voice keys saved on the server are encrypted at rest (the master key is kept separately on this machine); the **daily backup archives still contain plaintext**, as noted under "Backup & Restore" above.

## Open Source & License
- Code license: **AGPL-3.0-only** ([full text](LICENSE), GNU Affero General Public License v3), **in effect since v3.5.4**. v3.5.3 and earlier were released under MIT; those releases and their tags are historical records and are not changed.
- [NOTICE](NOTICE): a bilingual statement of the grant and its boundaries (who is licensed to do what, how commercial use and trademarks are treated, and what is outside the grant).
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md): itemised third-party components (Python / Flutter / model weights / artwork) and their obligations; verbatim license copies are in [THIRD_PARTY_LICENSES/](THIRD_PARTY_LICENSES/) and can be checked with `scripts/collect_license_notices.py --verify`.
- ⚠ **This license does not forbid commercial use**: you may run it commercially and you may charge for it. What you must comply with is the AGPL itself:
  - **Distributing** the software (modified or not) → comply with the applicable requirements of sections 4–6; when conveying object code, use a Corresponding Source method permitted by section 6 for that form of distribution. A written offer is not a substitute in every case;
  - Letting other users interact with a **modified** version over a network → offer those users the corresponding source code of your modified version, per section 13;
  - Running it yourself, or hosting an unmodified copy for your own use, does not by itself trigger a source-offer obligation.
- If you'd like to support maintenance you can [contact the author](#support-the-author) — this is a voluntary courtesy and **not** a condition of the license; never contacting the author is equally compliant.
- **Trademarks**: no trademark rights are granted. Please do not present a modified or repackaged build as an official build, or in a way that would cause confusion about its source.
- Third-party models and services (LLM / vision / voice / image generation) are applied for and paid by you; their terms follow their official statements.

## Support the Author
If this project has brought you companionship and joy, your support is welcome ☕

- **afdian (爱发电)**: [support the author on afdian](https://www.ifdian.net/a/gituu)
- **WeChat Reward**: scan the QR code below in WeChat (any amount; every bit of support helps)
- **Douyin (抖音)**: follow the author on Douyin, ID `dsOHOTzx` (copy it into Douyin search)
- **QQ group**: join the community QQ group `1065741798` (copy it into QQ's group search)

![WeChat Reward QR code](flutter_app/assets/reward_qrcode.png)
