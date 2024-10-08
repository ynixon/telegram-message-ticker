# -*- coding: utf-8 -*-
import os
import sys
import json
import threading
import asyncio
import time
import logging
import signal
import datetime
import re
from bs4 import BeautifulSoup
from flask import (
    Flask,
    jsonify,
    render_template,
    redirect,
    url_for,
    send_from_directory,
    request,
    Response,
    session,
)
import eventlet
from argparse import ArgumentParser
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
import requests

# Initialize Flask and SocketIO
app = Flask(__name__)
socketio = SocketIO(app, async_mode="eventlet")

# Global Variables
LATEST_MESSAGES = []
CHANNELS = []
REFRESH_FLAG = False
TELEGRAM_CLIENT = None
STOP_EVENT_LOOP = False
CONFIG = None
TELETHON_EVENT_LOOP = None
LAST_PROCESSED_MESSAGE = {}
MAX_LATEST_MESSAGES = 100
app.config["JSON_AS_ASCII"] = False
INITIAL_FETCH_DONE = False

# Initialize logging with timestamps
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
# logging.getLogger("werkzeug").setLevel(logging.WARNING)

flask_thread = None
telethon_thread = None

# Events to manage shutdown and loop readiness
shutdown_event = threading.Event()
loop_ready_event = threading.Event()

# Counters for messages
TOTAL_MESSAGES_FETCHED = 0
TOTAL_MESSAGES_PROCESSED = 0
channel_message_counters = {}

messages_lock = threading.Lock()


def load_translations(language):
    """Load translation file for the specified language."""
    base_folder = os.path.dirname(os.path.realpath(__file__))
    translation_file = os.path.join(base_folder, "translations", f"{language}.json")

    try:
        with open(translation_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        default_translation_file = os.path.join(base_folder, "translations", "en.json")
        with open(default_translation_file, "r", encoding="utf-8") as f:
            return json.load(f)


def delete_old_files(media_dir):
    """Delete media files older than the configured message age limit."""
    now = time.time()
    message_age_limit_seconds = CONFIG.get("message_age_limit", 2) * 3600  # Convert hours to seconds
    for filename in os.listdir(media_dir):
        file_path = os.path.join(media_dir, filename)
        if os.path.isfile(file_path):
            file_mod_time = os.path.getmtime(file_path)
            if now - file_mod_time > message_age_limit_seconds:
                try:
                    os.remove(file_path)
                    logger.info("Deleted old file: %s", file_path)
                except OSError as remove_error:
                    logger.error("Failed to delete %s: %s", file_path, remove_error)


def load_config(args=None):
    """Load configuration from a file or environment variables."""
    cfg = {
        "api_id": os.getenv("TELEGRAM_API_ID"),
        "api_hash": os.getenv("TELEGRAM_API_HASH"),
        "port": os.getenv("PORT", "3005"),
        "media_folder": os.getenv("MEDIA_FOLDER", "media"),
        "channel_list_file": os.getenv("CHANNEL_LIST_FILE", "channels.json"),
        "phone_number": os.getenv("PHONE_NUMBER"),
        "message_age_limit": int(os.getenv("MESSAGE_AGE_LIMIT", "2")),
        "default_language": os.getenv("DEFAULT_LANGUAGE", "en"),
        "secret_key": os.getenv("SECRET_KEY", "your_secret_key_here"),
    }

    current_dir = os.path.dirname(os.path.realpath(__file__))
    config_file = os.path.join(current_dir, "config.json")
    logger.info(f"Loading config file from: {config_file}")

    if not os.path.exists(config_file):
        logger.error("Missing required configuration: API ID, API hash, Phone number.")
        sys.exit(1)

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            file_config = json.load(f)
            logger.debug("Config file before merge: %s", file_config)
            logger.info(f"Loaded config file contents: {file_config}")
            cfg.update(file_config)
            logger.debug("Config after merge: %s", cfg)
    except json.JSONDecodeError:
        logger.error("Failed to decode JSON from config file.")
        sys.exit(1)

    # Convert media_folder to absolute path
    cfg["media_folder"] = os.path.join(current_dir, cfg["media_folder"])

    logger.debug("Final CONFIG after merge: %s", cfg)

    return cfg



async def list_all_channels(telegram_client):
    """List all available channels for the Telegram client."""
    dialogs = await telegram_client.get_dialogs()
    for dialog in dialogs:
        logger.info("Channel/Group: %s, ID: %s", dialog.name, dialog.id)


def load_channels(channel_list_file):
    """Load the list of channels from the configuration file."""
    if not os.path.exists(channel_list_file):
        current_dir = os.path.dirname(os.path.realpath(__file__))
        channel_list_file = os.path.join(current_dir, "channels.json")
        if not os.path.exists(channel_list_file):
            logger.error("Missing channels list file: %s", channel_list_file)
            sys.exit(1)

    try:
        with open(channel_list_file, "r", encoding="utf-8") as f:
            channels = json.load(f)["channels"]
            for channel in channels:
                channel_id = channel.get("id")
                channel_name = channel.get("name", "Unknown")
                if channel_id:
                    channel_message_counters[channel_id] = 0
                    LAST_PROCESSED_MESSAGE[channel_id] = None
                else:
                    logger.warning("Channel without ID found: %s", channel_name)
            return channels
    except Exception as load_err:
        logger.error("Failed to load channels from %s: %s", channel_list_file, load_err)
        sys.exit(1)


async def download_media_and_get_tag(telegram_client, message, media_dir, channel_name):
    """Download media and return the media tag."""
    media_type = "unknown"
    media_tag = ""
    try:
        if hasattr(message.media, "document") and message.media.document.mime_type.startswith("video"):
            media_type = "video"
            filename = f"{message.id}.mp4"
        elif hasattr(message.media, "photo"):
            media_type = "photo"
            filename = f"{message.id}.jpg"
        else:
            logger.warning("Unsupported media type in message ID: %d", message.id)
            return media_type, media_tag

        file_path = os.path.join(media_dir, filename)
        await telegram_client.download_media(message, file=file_path)

        logger.info("Downloaded %s for message ID %d: %s", media_type, message.id, file_path)

        if media_type == "video":
            media_tag = f'<video controls autoplay class="message-video"><source src="/media/{os.path.basename(file_path)}" type="video/mp4">Your browser does not support the video tag.</video>'
        elif media_type == "photo":
            media_tag = f'<img src="/media/{os.path.basename(file_path)}" alt="Photo" class="message-image">'

    except (OSError, RuntimeError) as download_err:
        logger.error("Failed to download %s for message ID %d: %s", media_type, message.id, download_err)

    return media_type, media_tag


def setup_push_notifications(telegram_client):
    """Set up push notifications for new messages in subscribed channels."""
    @telegram_client.on(
        events.NewMessage(chats=[channel["id"] for channel in CHANNELS])
    )
    async def new_message_listener(event):
        channel_title = event.chat.title if event.chat else "Unknown Channel"
        logger.info("New message event received from: %s", channel_title)
        try:
            message_content = ""
            if hasattr(event.message, "message") and event.message.message:
                message_content = event.message.message
            elif hasattr(event.message, "caption") and event.message.caption:
                message_content = event.message.caption

            if message_content:
                refresh_state["refresh_flag"] = True
                logger.info("New message received in %s", channel_title)

                message_time = event.message.date
                message_time = message_time.astimezone(datetime.timezone.utc)

                # Handle media content
                media_tag = ""
                media_type = "text"
                if event.message.media:
                    media_dir = CONFIG.get("media_folder", "media")
                    media_type, media_tag = await download_media_and_get_tag(
                        telegram_client, event.message, media_dir, channel_title
                    )

                # Append media tag to message content if available
                if media_tag:
                    message_content += media_tag

                message_data = {
                    "id": event.message.id,
                    "channel": channel_title,
                    "message": message_content,
                    "time": message_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "media_type": media_type if media_type != "text" else "text",
                }

                with messages_lock:
                    # Avoid duplicate messages
                    if not any(msg["id"] == message_data["id"] and msg["channel"] == message_data["channel"] for msg in LATEST_MESSAGES):
                        LATEST_MESSAGES.append({
                            "id": message_data["id"],
                            "channel": message_data["channel"],
                            "message": message_data["message"],
                            "time": message_data["time"],
                            "media_type": message_data["media_type"],
                            "displayed": False,
                        })
                        TOTAL_MESSAGES_PROCESSED += 1
                        channel_message_counters[event.chat.id] += 1
                        LAST_PROCESSED_MESSAGE[event.chat.id] = event.message.id

                broadcast_new_message(message_data, is_push=True)

        except (AttributeError, RuntimeError) as listen_err:
            logger.error("Error in new_message_listener: %s", listen_err)
            shutdown()


def extract_and_replace_urls(message_content):
    """Extract URLs from message content and replace them with anchor tags."""
    logger.debug("Original message content before URL extraction: %s", message_content)

    url_regex = re.compile(r'(?<!href=["\'])https?://[^\s<>"\'\)]+', re.UNICODE)

    def replace_url(match):
        url = match.group(0).strip()
        title = fetch_url_title(url)
        return f'<a href="{url}" target="_blank">{title}</a>'

    processed_content = url_regex.sub(replace_url, message_content)

    soup = BeautifulSoup(processed_content, "html.parser")

    remove_duplicate_links(soup)

    processed_content = str(soup)

    logger.debug("Processed message content after URL extraction and link removal: %s", processed_content)
    return processed_content


def create_anchor_tag(url):
    """Create an anchor tag for a given URL."""
    title = fetch_url_title(url)
    return f'<a href="{url}" target="_blank">{title}</a>'


def fetch_url_title(url):
    """Fetch the HTML title of the given URL."""
    try:
        logger.info("Attempting to fetch the title for URL: %s", url)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Bot/1.0; +http://example.com/bot)"
        }
        response = requests.get(url, timeout=5, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            logger.info("Fetched title for URL: %s -> Title: %s", url, title)
        else:
            title = url
            logger.warning("No title found in HTML for URL: %s", url)
    except requests.exceptions.RequestException as req_err:
        logger.error("Error fetching URL title for %s: %s", url, req_err)
        title = url
    return title


logger.info("Total messages processed: %d", TOTAL_MESSAGES_PROCESSED)


def remove_duplicate_links(soup):
    """Remove duplicate links from the BeautifulSoup object."""
    seen_links = set()
    for a_tag in soup.find_all("a"):
        href = a_tag.get("href")
        if href in seen_links:
            a_tag.decompose()
        else:
            seen_links.add(href)


def broadcast_new_message(message_data, is_push=False):
    """Broadcast a new message to connected clients via SocketIO."""
    logger.info("Broadcasting new message to clients: %s", message_data)

    # Convert the timestamp field to string if it's a datetime object
    if isinstance(message_data.get("timestamp"), datetime.datetime):
        message_data["time"] = message_data["timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        message_data["time"] = str(message_data.get("time"))

    message_data["is_push"] = is_push
    message_data["message"] = extract_and_replace_urls(message_data["message"])

    # Remove timestamp field before broadcasting, since it's not needed for clients
    if "timestamp" in message_data:
        del message_data["timestamp"]

    logger.debug("Broadcasting new_message: %s", message_data)
    socketio.emit("new_message", message_data)


async def download_and_update_message(telegram_client, message, media_dir, channel_id, channel_name):
    """Download media content from a message and update the message with media links."""
    # Removed broadcasting from here to prevent duplicate broadcasts
    pass  # This function is no longer needed and can be removed or kept empty


async def get_latest_messages_once(telegram_client, cfg):
    """Fetch the latest messages once and stop."""
    global INITIAL_FETCH_DONE, LATEST_MESSAGES, TOTAL_MESSAGES_FETCHED, TOTAL_MESSAGES_PROCESSED
    global channel_message_counters, LAST_PROCESSED_MESSAGE

    # Check if the initial fetch is done
    if INITIAL_FETCH_DONE:
        logger.info("Initial fetch already completed. Skipping fetch and waiting for push notifications.")
        return

    media_dir = cfg.get("media_folder", "media")
    message_age_limit = cfg.get("message_age_limit", 2)

    if not os.path.exists(media_dir):
        os.makedirs(media_dir)

    try:
        for channel in CHANNELS:
            channel_name = channel.get("name", "Unknown")
            channel_id = channel.get("id")
            if not channel_id:
                logger.error("Channel ID missing for channel: %s", channel_name)
                continue

            entity = await telegram_client.get_entity(channel_id)
            messages = await telegram_client.get_messages(entity, limit=1)  # Fetch up to 1 message
            TOTAL_MESSAGES_FETCHED += len(messages)
            logger.info("Fetched %d message(s) from %s", len(messages), channel_name)

            for message in messages:
                logger.debug("Processing message ID %d from '%s'", message.id, channel_name)

                message_content = message.message if hasattr(message, "message") else (message.caption or "")
                message_content = extract_and_replace_urls(message_content)

                # Parse message time
                message_time = message.date  # Assuming this is timezone-aware
                cutoff_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=message_age_limit)

                # Ensure message_time is timezone-aware and in UTC
                if message_time.tzinfo is None:
                    message_time = message_time.replace(tzinfo=datetime.timezone.utc)
                else:
                    message_time = message_time.astimezone(datetime.timezone.utc)

                # Check if message is within the age limit
                if message_time < cutoff_time:
                    logger.info("Skipped message ID %d from '%s': Older than %d hours.", message.id, channel_name, message_age_limit)
                    continue

                with messages_lock:
                    # Avoid duplicate messages
                    if any(msg["id"] == message.id and msg["channel"] == channel_name for msg in LATEST_MESSAGES):
                        continue

                # Handle media content
                media_tag = ""
                media_type = "text"
                if message.media:
                    media_type, media_tag = await download_media_and_get_tag(
                        telegram_client, message, media_dir, channel_name
                    )

                # Append media tag to message content if available
                if media_tag:
                    message_content += media_tag

                message_data = {
                    "id": message.id,
                    "channel": channel_name,
                    "message": message_content,
                    "time": message_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "media_type": media_type if media_type != "text" else "text",
                }

                with messages_lock:
                    LATEST_MESSAGES.append({
                        "id": message_data["id"],
                        "channel": message_data["channel"],
                        "message": message_data["message"],
                        "time": message_data["time"],
                        "media_type": message_data["media_type"],
                        "displayed": False,
                    })
                    TOTAL_MESSAGES_PROCESSED += 1
                    channel_message_counters[channel_id] += 1
                    LAST_PROCESSED_MESSAGE[channel_id] = message.id

                broadcast_new_message(message_data, is_push=True)  # Set is_push to True

        with messages_lock:
            LATEST_MESSAGES = LATEST_MESSAGES[-MAX_LATEST_MESSAGES:]

        logger.info("Total messages fetched: %d", TOTAL_MESSAGES_FETCHED)
        logger.info("Total messages processed: %d", TOTAL_MESSAGES_PROCESSED)

        # Mark the initial fetch as done
        INITIAL_FETCH_DONE = True

    except Exception as err:
        logger.error("Error fetching messages: %s", err)


@app.errorhandler(ConnectionAbortedError)
def handle_aborted_connection(e):
    """Handle connection aborted error."""
    logger.warning("Connection aborted: %s", e)
    return jsonify({"error": "Connection aborted"}), 500


@app.route("/test")
def test():
    """Test route to ensure the Flask server is running."""
    return "Flask server is running"


@app.route("/set_language/<lang>", methods=["GET"])
def set_language(lang):
    """Set the language for the session based on user input."""
    if lang in ["en", "he"]:  # Add your supported languages here
        session["language"] = lang  # Store language in the session
        logger.info("Language set to %s", lang)
    else:
        logger.warning("Unsupported language: %s", lang)
    return redirect(url_for("display"))  # Redirect to refresh the page with new language


@app.route("/")
def display():
    """Main route to display the page with the latest messages."""
    language = session.get("language", CONFIG.get("default_language", "en"))
    translations = load_translations(language)

    # Ensure message_age_limit is retrieved from config
    message_age_limit = CONFIG.get("message_age_limit", 2)  # Default to 2 hours if not defined

    return render_template("index.html", 
                           translations=translations, 
                           refresh_flag=REFRESH_FLAG, 
                           message_age_limit=message_age_limit)


@app.route("/fetch-title", methods=["GET"])
def fetch_title():
    """Fetch the title of a webpage given its URL."""
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string if soup.title else url
        return jsonify({"title": title})
    except requests.RequestException as err:
        logger.error("Failed to fetch URL %s: %s", url, err)
        return jsonify({"error": "Failed to fetch title"}), 500


@socketio.on("connect")
def handle_connect(auth):
    """Handle a new client connection via SocketIO."""
    logger.info("Client connected.")
    with messages_lock:
        valid_messages = [
            {
                "id": data["id"],
                "channel": data["channel"],
                "message": data["message"],
                "time": data["time"],
                "is_push": data.get("is_push", False),
            }
            for data in LATEST_MESSAGES
            if data["message"]
        ]
    logger.info("Emitting %d initial messages to the client.", len(valid_messages))
    logger.debug(
        "Valid messages being sent: %s", json.dumps(valid_messages, ensure_ascii=False)
    )
    emit("initial_messages", {"messages": valid_messages})


@socketio.on("disconnect")
def handle_disconnect():
    """Handle a client disconnection."""
    logger.info("Client disconnected")


@app.route("/media/<path:filename>")
def media(filename):
    media_path = os.path.join(CONFIG["media_folder"], filename)

    if not os.path.isfile(media_path):
        logger.error(f"Requested media file does not exist: {media_path}")
        return jsonify({"error": "File not found"}), 404

    range_header = request.headers.get("Range", None)
    if not range_header:
        return send_from_directory(CONFIG["media_folder"], filename)

    size = os.path.getsize(media_path)
    byte1, byte2 = 0, None
    match = re.search(r"(\d+)-(\d*)", range_header)
    if match:
        groups = match.groups()
        byte1 = int(groups[0])
        if groups[1]:
            byte2 = int(groups[1])

    length = size - byte1 if byte2 is None else byte2 - byte1 + 1
    data = None
    try:
        with open(media_path, "rb") as f:
            f.seek(byte1)
            data = f.read(length)
    except OSError as e:
        logger.error(f"Error reading media file {media_path}: {e}")
        return jsonify({"error": "Error reading file"}), 500

    # Determine the correct MIME type
    mimetype, _ = mimetypes.guess_type(media_path)
    if not mimetype:
        mimetype = "application/octet-stream"

    rv = Response(data, 206, mimetype=mimetype, content_type=mimetype, direct_passthrough=True)
    rv.headers.add("Content-Range", f"bytes {byte1}-{byte1 + len(data) - 1}/{size}")
    return rv


@app.route("/start-over")
def start_over():
    """Reset the refresh flag and redirect to the main display."""
    global REFRESH_FLAG  # Using global REFRESH_FLAG to modify its state across requests
    REFRESH_FLAG = False
    return redirect(url_for("display"))


@app.route("/getMessages")
def get_messages():
    """Fetch and return the latest messages."""
    logger.info("==============Processing /getMessages Request==============")
    try:
        if TELETHON_EVENT_LOOP is None:
            raise RuntimeError("Telethon event loop is not initialized.")

        # Schedule the coroutine in the Telethon event loop
        future = asyncio.run_coroutine_threadsafe(
            get_latest_messages_once(TELEGRAM_CLIENT, CONFIG), TELETHON_EVENT_LOOP
        )
        future.result()  # Wait for the coroutine to complete

        with messages_lock:
            if LATEST_MESSAGES:
                valid_messages = [
                    {
                        "id": msg["id"],
                        "channel": msg["channel"],
                        "message": msg["message"],
                        "time": msg["time"],
                        "is_push": True,  # All fetched messages are push messages
                    }
                    for msg in LATEST_MESSAGES
                ]
                return jsonify({"messages": valid_messages})
            else:
                return jsonify({"status": "No new messages found."})

    except RuntimeError as runtime_err:
        logger.error("Runtime error occurred while fetching messages: %s", runtime_err)
        shutdown()
        return jsonify({"error": "Error fetching latest messages"}), 500
    except asyncio.CancelledError as cancel_err:
        logger.error("Async task was cancelled: %s", cancel_err)
        return jsonify({"error": "Task cancelled"}), 500
    except Exception as general_err:
        logger.error("Unexpected error fetching latest messages: %s", general_err)
        shutdown()  # Signal shutdown on error
        return jsonify({"error": "Unexpected error fetching latest messages"}), 500


@app.route("/shutdown", methods=["POST"])
def shutdown_server():
    """Shuts down the Flask-SocketIO server if the correct token is provided."""
    secret_token = request.headers.get("X-Shutdown-Token")
    if secret_token != os.getenv("SHUTDOWN_TOKEN"):
        logger.warning("Unauthorized shutdown attempt.")
        return "Unauthorized", 401

    logger.info("Shutting down the Flask-SocketIO server...")
    try:
        # Use SocketIO's stop method
        socketio.stop()
        logger.info("Flask-SocketIO server shutting down...")
    except RuntimeError as runtime_err:
        logger.error("Runtime error during shutdown: %s", runtime_err)
        return "Error during shutdown", 500
    except Exception as general_err:
        logger.error("Unexpected error during shutdown: %s", general_err)
        return "Error during shutdown", 500

    return "Server shutting down..."


def trigger_client_refresh():
    """Emit a 'refresh' event to all connected clients."""
    try:
        # Emit a 'refresh' event to all connected clients
        socketio.emit("refresh", {"message": "Refresh the data"})
        logger.info("Refresh event triggered for clients.")
    except RuntimeError as runtime_err:
        logger.error("Runtime error during refresh event trigger: %s", runtime_err)
    except Exception as general_err:
        logger.error("Unexpected error triggering refresh event: %s", general_err)


@app.route("/trigger-client-refresh", methods=["POST"])
def trigger_refresh():
    """Trigger client refresh by emitting a 'refresh' event."""
    try:
        trigger_client_refresh()
        return jsonify({"status": "Refresh event triggered"}), 200
    except RuntimeError as runtime_err:
        logger.error("Runtime error during client refresh trigger: %s", runtime_err)
        return jsonify({"error": str(runtime_err)}), 500
    except Exception as general_err:
        logger.error("Unexpected error during client refresh trigger: %s", general_err)
        return jsonify({"error": str(general_err)}), 500


def run_flask(cfg):
    """Start the Flask-SocketIO server."""
    global CONFIG
    CONFIG = cfg

    app.secret_key = CONFIG.get("secret_key")

    logger.info("Starting Flask-SocketIO server on port %s...", CONFIG["port"])
    time.sleep(2)  # Allow some time before starting the server

    try:
        eventlet.wsgi.server(
            eventlet.listen(("0.0.0.0", int(CONFIG["port"]))),  # Ensure port is an integer
            app,
            log_output=True,
            socket_timeout=30,
        )
    except OSError as os_err:
        logger.error("OS error occurred while running the Flask server: %s", os_err)
        shutdown()  # Signal shutdown on error
    except Exception as general_err:
        logger.error("Error running Flask server: %s", general_err)
        shutdown()  # Signal shutdown on error


async def run_telethon_client(cfg):
    """Run the Telethon client within its own event loop."""
    global TELEGRAM_CLIENT, INITIAL_FETCH_DONE

    if not CONFIG:
        logger.error("CONFIG is not set. Cannot start Telegram client.")
        shutdown()
        return

    if not CONFIG.get("api_id") or not CONFIG.get("api_hash"):
        logger.error("API ID or API Hash is missing from CONFIG.")
        shutdown()
        return

    # Determine session file path, prioritize current directory first
    session_file = "user_session.session"

    # Check in the current directory
    if not os.path.exists(session_file):
        logger.info("Session file not found in current directory, checking application directory...")

        # Then check in application directory
        current_dir = os.path.dirname(os.path.realpath(__file__))
        session_file = os.path.join(current_dir, "user_session.session")

    TELEGRAM_CLIENT = TelegramClient(session_file, CONFIG["api_id"], CONFIG["api_hash"])

    try:
        # Start the Telethon client and connect
        while not STOP_EVENT_LOOP:
            try:
                if not TELEGRAM_CLIENT.is_connected():
                    await TELEGRAM_CLIENT.connect()

                if not await TELEGRAM_CLIENT.is_user_authorized():
                    await TELEGRAM_CLIENT.send_code_request(CONFIG["phone_number"])
                    code = input(f"Enter the code for {CONFIG['phone_number']}: ")
                    await TELEGRAM_CLIENT.sign_in(CONFIG["phone_number"], code)
                    TELEGRAM_CLIENT.session.save()

                # Perform initial fetch of messages
                await get_latest_messages_once(TELEGRAM_CLIENT, CONFIG)

                # Set up notifications after initial fetch
                if INITIAL_FETCH_DONE:
                    setup_push_notifications(TELEGRAM_CLIENT)

            except SessionPasswordNeededError:
                password = input("Two-step verification enabled. Please enter your password: ")
                await TELEGRAM_CLIENT.sign_in(password=password)
            except Exception as client_error:
                logger.error("Error in Telethon client: %s", client_error)
                await asyncio.sleep(10)

    except Exception as loop_error:
        logger.error("Critical error in Telethon event loop: %s", loop_error)
        shutdown()
    finally:
        if TELEGRAM_CLIENT and TELEGRAM_CLIENT.is_connected():
            try:
                await TELEGRAM_CLIENT.disconnect()
                logger.info("Telegram client disconnected successfully.")
            except Exception as disconnect_err:
                logger.error("Error disconnecting Telethon client: %s", disconnect_err)


def shutdown_handler(signal_received, frame):
    """
    Handles SIGINT (Ctrl+C) and gracefully shuts down the application.

    Args:
        signal_received: The signal that was received (e.g., SIGINT).
        frame: The current stack frame (unused but required by signal handlers).
    """
    logger.info("SIGINT or CTRL-C detected. Stopping gracefully...")
    shutdown()


async def shutdown_telethon():
    """Gracefully disconnect Telethon and stop its event loop."""
    global TELEGRAM_CLIENT
    try:
        if TELEGRAM_CLIENT and TELEGRAM_CLIENT.is_connected():
            logger.info("Stopping Telethon event loop...")
            # Cancel all pending tasks to ensure clean shutdown
            pending_tasks = asyncio.all_tasks()
            for task in pending_tasks:
                task.cancel()

            await asyncio.gather(*pending_tasks, return_exceptions=True)  # Collect all task cancellations
            await TELEGRAM_CLIENT.disconnect()
            logger.info("Telethon client disconnected.")
    except asyncio.CancelledError:
        logger.info("Pending tasks were cancelled during shutdown.")
    except RuntimeError as runtime_err:
        logger.error(f"Error during Telethon client disconnect: {runtime_err}")
    except Exception as general_err:
        logger.error(f"General error during Telethon shutdown: {general_err}")


def shutdown():
    """
    Gracefully shut down the application, stopping all active threads including
    the Telegram client and Flask-SocketIO server.
    """
    global STOP_EVENT_LOOP
    STOP_EVENT_LOOP = True

    try:
        # Check if the event loop is running and cancel pending tasks cleanly
        if TELETHON_EVENT_LOOP and TELETHON_EVENT_LOOP.is_running():
            # Cancel all pending tasks
            pending_tasks = [t for t in asyncio.all_tasks(loop=TELETHON_EVENT_LOOP) if not t.done()]
            for task in pending_tasks:
                task.cancel()

            # Schedule the shutdown coroutine
            asyncio.run_coroutine_threadsafe(shutdown_telethon(), TELETHON_EVENT_LOOP)

            logger.info("Telethon event loop shutdown initiated.")
        else:
            logger.warning("Telethon event loop is not running, unable to cancel tasks.")

        # Stop Flask-SocketIO server if running
        if flask_thread and flask_thread.is_alive():
            shutdown_event.set()
            socketio.stop()
            flask_thread.join()
            logger.info("Flask-SocketIO server stopped.")
        # Stop Telethon thread
        if telethon_thread and telethon_thread.is_alive():
            telethon_thread.join()
            logger.info("Telethon thread stopped.")

    except Exception as err:
        logger.error("Error during shutdown: %s", err)
    finally:
        logger.info("Application shutdown complete.")
        sys.exit(0)


def run_telethon_thread(cfg):
    """
    Create a new event loop for the Telethon client and keep it running.
    This function is intended to be run in a separate thread.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("Telethon event loop created.")

    # Run the Telethon client and keep the loop alive
    try:
        loop.run_until_complete(run_telethon_client(cfg))
        loop_ready_event.set()  # Mark the event loop as ready
        loop.run_forever()
    except Exception as err:
        logger.error("Error in Telethon event loop: %s", err)
    finally:
        loop.close()
        logger.info("Telethon event loop closed.")


def main(args):
    """Main function to initialize and start the Flask and Telethon threads."""
    
    # Load configuration from args
    cfg = load_config(args)
    global CONFIG, CHANNELS
    CONFIG = cfg
    logger.info("Loaded CONFIG: %s", CONFIG)

    # If the user passed the list_channels flag, list channels and exit
    if args.list_channels:
        telegram_client = TelegramClient("user_session", cfg["api_id"], cfg["api_hash"])
        loop = asyncio.get_event_loop()
        loop.run_until_complete(list_all_channels(telegram_client))
        return

    # Load channels from the configuration file
    CHANNELS = load_channels(cfg["channel_list_file"])

    # Set up signal handling for graceful shutdown
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Start Flask in a separate thread
    logger.info("Starting Flask thread...")
    flask_thread = threading.Thread(target=run_flask, args=(cfg,), daemon=True)
    flask_thread.start()

    # Delay for Flask initialization
    time.sleep(2)

    # Start Telethon in a separate thread
    logger.info("Starting Telethon thread...")
    telethon_thread = threading.Thread(target=run_telethon_thread, args=(cfg,), daemon=True)
    telethon_thread.start()

    # Main loop to keep the application running
    try:
        while not shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Stopping the application.")
        shutdown()
    except Exception as err:
        logger.error("Unhandled exception: %s", err)
        shutdown()


if __name__ == "__main__":
    try:
        parser = ArgumentParser()
        parser.add_argument("--api_id", type=int, help="Telegram API ID")
        parser.add_argument("--api_hash", type=str, help="Telegram API Hash")
        parser.add_argument("--port", type=int, help="Port for Flask server", default=3005)
        parser.add_argument("--config_file", type=str, help="Path to configuration file (default: config.json)")
        parser.add_argument("--list-channels", action="store_true", help="List all available channels")
        parser.add_argument("--phone_number", type=str, help="Your phone number for user login")
        parser.add_argument("--media_folder", type=str, help="Directory for downloaded media files", default="media")
        parser.add_argument("--message_age_limit", type=int, help="Maximum age of messages in hours", default=2)

        args = parser.parse_args()
        main(args)
    except KeyboardInterrupt:
        logger.info("Stopping the application...")
        shutdown()
    except Exception as err:
        logger.error("Unhandled exception: %s", err)
        shutdown()
