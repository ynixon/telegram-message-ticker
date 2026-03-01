# Telegram Message Ticker

A web application that fetches the latest messages from Telegram channels using Telethon and displays them in a real-time ticker format. Runs on **desktop** (direct Python) and on **Android** as a native APK (Kivy + WebView).

## Features

- Fetches and displays messages from Telegram channels in real time.
- Supports text, photos, and videos.
- Automatically pushes new messages via Socket.IO (no manual refresh needed).
- Allows remote refresh of the message feed via a POST endpoint.
- Deletes old media files automatically after a configurable period.
- **Android APK** — credential entry screen, live progress display, auth-code / 2FA screens, and an embedded WebView ticker.
- **Language support** — switch between English and Hebrew dynamically.
- **CI/CD** — GitHub Actions builds a debug APK on every push; APK is uploaded as a workflow artifact and published to GitHub Releases on version tags.

## Prerequisites

- Python 3.10 or higher (3.11 recommended)
- Flask + Telethon (see `requirements.txt`)
- For Android builds: Buildozer 1.5, Android SDK API 33, NDK 25b

## Setup Instructions (Desktop)

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd telegram-message-ticker
   ```

2. **Install the required packages:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Create a configuration file:**

   Copy the example and fill in your credentials (get them at <https://my.telegram.org> → *API development tools*):

   ```bash
   cp config.json.example config.json
   ```

   ```json
   {
     "api_id": 123456,
     "api_hash": "your32charhashhere",
     "port": 3005,
     "media_folder": "media",
     "channel_list_file": "channels.json",
     "message_age_limit": 2,
     "default_language": "en",
     "secret_key": "change-me-to-a-random-string"
   }
   ```

   Generate a random `secret_key`:
   ```python
   python -c "import os; print(os.urandom(24).hex())"
   ```

4. **Create a channels file:**

   ```bash
   cp channels.json.example channels.json
   ```

   Edit `channels.json` and add the Telegram channel IDs you want to monitor:

   ```json
   {
     "channels": [
       {"id": "channel_username_or_id", "name": "My Channel"}
     ]
   }
   ```

   Tip: use `--list-channels` to discover channel IDs once you are logged in.

5. **Run the application:**

   ```bash
   python telegram_message_ticker.py
   ```

   Open `http://127.0.0.1:3005` in a browser. On first run Telethon will send a login code to your phone; enter it when prompted.

## Android APK

The app can be installed as a native Android APK. A pre-built debug APK is produced automatically by GitHub Actions on every push and is available as a workflow artifact (and as a GitHub Release on version tags).

### Installing / Updating the APK

1. Download `telegram-message-ticker-debug.apk` from the *Actions* tab (latest workflow run → *Artifacts*) or from the *Releases* page.
2. On your Android device enable *Install from unknown sources* for your browser or file manager.
3. Open the downloaded APK and tap **Install**.

> **Update note:** all CI-built APKs are signed with the same stable debug keystore (cached between runs). You can install new versions as updates without uninstalling first, as long as you use APKs produced by the same repository's CI.

### First-time setup on Android

1. Launch the app. The setup screen appears.
2. Enter your **API ID**, **API Hash**, and **Phone number** (with country code, e.g. `+12223334444`). Get credentials at <https://my.telegram.org>.
3. Tap **Start**. A live progress log shows connection status.
4. If your account has not been used from this device, Telegram sends a login code to your phone/Telegram app. Enter it on the verification screen.
5. If 2FA is enabled enter your Telegram password when prompted.
6. Once connected the app switches automatically to the message-ticker view.

### Building locally

```bash
pip install buildozer==1.5.0 "cython==0.29.37"
buildozer android debug
# APK is written to bin/
```

See `BUILDING_ANDROID.md` for full instructions.

## Running Options

You can run the application with various options:

1. **Environment Variables:**
   - `TELEGRAM_API_ID`: Your Telegram API ID.
   - `TELEGRAM_API_HASH`: Your Telegram API Hash.
   - `PORT`: Port for the Flask server (default is 3005).
   - `MEDIA_FOLDER`: Directory for storing downloaded media files (default is "media").
   - `CHANNEL_LIST_FILE`: Path to the JSON file containing channels (default is "channels.json").
   - `MESSAGE_AGE_LIMIT`: Maximum age of messages in hours (default is 2).
   - `DEFAULT_LANGUAGE`: The default language for the interface (e.g., "en" or "he").
   - `SECRET_KEY`: The key used for securely signing the Flask session cookies.

2. **Command-Line Arguments:**
   You can also provide arguments while running the script:

   ```bash
   python telegram_message_ticker.py --api_id <Your_Telegram_API_ID> --api_hash <Your_Telegram_API_Hash> --port 3005 --config_file config.json --list-channels --phone_number <Your_Phone_Number> --message_age_limit 2
   ```

   Replace `<Your_Telegram_API_ID>`, `<Your_Telegram_API_Hash>`, and `<Your_Phone_Number>` with the appropriate values.

3. **Using Configuration File:**
   If you have created the `config.json` file as described in the setup instructions, it will automatically be loaded when running the application without additional arguments.

## Remote Refresh URL

You can remotely trigger a refresh of the message ticker by sending a `POST` request to the following endpoint:

```
POST /trigger-client-refresh
```

This will send a refresh event to all connected clients, prompting them to reload the message feed without manually interacting with the page.

Example using `curl`:

```bash
curl -X POST http://127.0.0.1:3005/trigger-client-refresh
```

## Language Support

The application supports dynamic language switching. Users can switch between available languages using the dropdown list on the page.

- Supported languages (e.g., English and Hebrew) are handled via the `/set_language/<lang>` route.
- To switch between languages programmatically, you can navigate to:

```
/set_language/en   # For English
/set_language/he   # For Hebrew
```

Upon changing the language, the page will automatically refresh to reflect the selected language.

Example usage for language change:

```bash
curl http://127.0.0.1:3005/set_language/he
```

## Usage

- Open your web browser and navigate to `http://127.0.0.1:3005`.
- The latest messages from the configured Telegram channels will be displayed in a ticker format.
- Use the **Refresh Feed** button or the remote refresh URL to manually refresh the messages.
- Change the language using the dropdown or by calling the appropriate route to update the UI language.

## File Structure

```
.
├── main.py                        # Android APK entry point (Kivy)
├── telegram_message_ticker.py     # Flask + Telethon backend (desktop & Android)
├── buildozer.spec                 # Android build configuration
├── requirements.txt               # Python dependencies (desktop)
├── config.json.example            # Configuration template
├── channels.json.example          # Channels template
├── templates/
│   ├── index.html                 # Ticker web UI
│   └── loading.html               # Loading screen (web)
├── static/
│   ├── ticker.js                  # Socket.IO client logic
│   └── styles.css                 # UI styles
├── translations/
│   ├── en.json                    # English strings
│   └── he.json                    # Hebrew strings
└── .github/workflows/
    └── build-apk.yml              # CI/CD – builds & publishes the Android APK
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! If you have suggestions for improvements or find bugs, please open an issue or submit a pull request.