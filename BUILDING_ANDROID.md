# Building the Android APK

This document explains how to compile **Telegram Message Ticker** into an
Android APK using [Buildozer](https://buildozer.readthedocs.io/).

---

## How the Android app works

```
┌───────────────────────────────────┐
│          Android APK              │
│                                   │
│  ┌────────────┐  ┌─────────────┐  │
│  │  Kivy UI   │  │ Flask server│  │
│  │ (main.py)  │  │ + Telethon  │  │
│  └─────┬──────┘  └──────┬──────┘  │
│        │  Android WebView         │
│        └──── localhost:3005 ──────┘
└───────────────────────────────────┘
```

1. `main.py` is the Kivy entry point.
2. On first launch a setup screen asks for your Telegram API credentials
   (API ID + API Hash from <https://my.telegram.org>).
3. The existing `telegram_message_ticker.py` Flask server starts in a
   background thread on port 3005.
4. An Android `WebView` opens `http://localhost:3005` and renders the
   full web interface inside the APK.

---

## Prerequisites

| Requirement | Minimum version |
|---|---|
| Ubuntu / Debian Linux (or WSL2) | 20.04+ |
| Python | 3.10+ |
| Buildozer | 1.5+ |
| Java JDK | 17 |

> **macOS / Windows**: Run in a Docker container or GitHub Actions
> (see the CI section below).

### Install Buildozer

```bash
pip install --upgrade buildozer cython virtualenv
```

Install system dependencies (Ubuntu/Debian):

```bash
sudo apt-get update
sudo apt-get install -y \
    git zip unzip openjdk-17-jdk python3-pip \
    autoconf libtool pkg-config zlib1g-dev \
    libncurses5-dev libncursesw5-dev libtinfo5 \
    cmake libffi-dev libssl-dev
```

---

## Building the APK

### 1 — Clone and enter the repo

```bash
git clone https://github.com/ynixon/telegram-message-ticker.git
cd telegram-message-ticker
```

### 2 — (Optional) Add custom icons

Place a **512 × 512 px** PNG at `static/icon.png` and a
**720 × 1280 px** PNG at `static/presplash.png`, then uncomment the
corresponding lines in `buildozer.spec`.

### 3 — Build a debug APK

```bash
buildozer android debug
```

The first run downloads the Android SDK/NDK and all Python wheels —
expect **30–60 minutes** on a fresh machine.

The APK is placed at:
```
bin/telegramticker-1.0-arm64-v8a_armeabi-v7a-debug.apk
```

### 4 — Build a release AAB (Play Store)

```bash
buildozer android release
```

Sign the resulting `.aab` with your release keystore before uploading.

### 5 — Deploy directly to a connected device

```bash
buildozer android debug deploy run
```

---

## First-run configuration (on device)

1. Open the app — a setup screen appears.
2. Enter your **API ID** and **API Hash** from
   [my.telegram.org](https://my.telegram.org).
3. Enter your **phone number** (used for Telegram authentication).
4. Tap **Start**. The credentials are saved to the app's private
   storage so you only need to do this once.

### Adding channels

On the device, place a `channels.json` file in the app's storage
directory, or edit `channels.json.example` and push it:

```bash
adb push channels.json /data/data/com.ynixon.telegramticker/files/channels.json
```

`channels.json` format:

```json
{
  "channels": [
    {"id": "channelname", "name": "Display Name"},
    {"id": "-1001234567890", "name": "My Channel"}
  ]
}
```

---

## GitHub Actions CI (build in the cloud)

Create `.github/workflows/build-apk.yml` to build on every push:

```yaml
name: Build Android APK

on: [push]

jobs:
  build:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y openjdk-17-jdk zip unzip \
            autoconf libtool pkg-config zlib1g-dev cmake \
            libffi-dev libssl-dev
          pip install buildozer cython

      - name: Build APK
        run: buildozer android debug

      - name: Upload APK
        uses: actions/upload-artifact@v4
        with:
          name: telegram-ticker-apk
          path: bin/*.apk
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `SDK licence not accepted` | Run `buildozer android debug` once interactively; accept licences. Or set `android.accept_sdk_license = True` (already set). |
| Build fails on `lxml` / `lxml-html-clean` | Ensure `libxml2-dev` and `libxslt-dev` are installed. |
| App shows blank screen on device | Run `adb logcat \*:S python:D` to see Python tracebacks. |
| Telethon auth loop hangs | Make sure `phone_number` is set correctly in the setup screen. |
| WebView shows "connection refused" | The Flask server needs a few seconds to start — the loading screen retries automatically. |
| APK upgrade fails / garbled error on install | The signing certificate changed between builds (GitHub Actions cache was evicted). **Fix:** uninstall the old app, install the new APK. **Prevent:** save the debug keystore as a base64-encoded GitHub secret `DEBUG_KEYSTORE_BASE64` — see workflow comments for instructions. |
