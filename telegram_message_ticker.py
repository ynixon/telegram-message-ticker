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
import eventlet
from bs4 import BeautifulSoup
from argparse import ArgumentParser
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
from flask_socketio import SocketIO, emit  # Import SocketIO
from telethon import TelegramClient, events
import requests


app = Flask(__name__)
socketio = SocketIO(app, async_mode="eventlet")


LATEST_MESSAGES = []
CHANNELS = []
REFRESH_FLAG = False
TELEGRAM_CLIENT = None
STOP_EVENT_LOOP = False
CONFIG = None
telethon_event_loop = None  # Global event loop for Telethon thread
# Add a new dictionary to track last processed message ID per channel
LAST_PROCESSED_MESSAGE = {}
MAX_LATEST_MESSAGES = 100  # Set your desired limit
app.config["JSON_AS_ASCII"] = False


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
total_messages_fetched = 0
total_messages_processed = 0
channel_message_counters = {}  # Dictionary to hold per-channel message counts

# Lock for thread-safe operations on LATEST_MESSAGES
messages_lock = threading.Lock()


def load_translations(language):
    try:
        with open(f"translations/{language}.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Fallback to default language (e.g., English)
        with open("translations/en.json", "r", encoding="utf-8") as f:
            return json.load(f)


def delete_old_files(media_dir):
    now = time.time()
    for filename in os.listdir(media_dir):
        file_path = os.path.join(media_dir, filename)
        if os.path.isfile(file_path):
            file_mod_time = os.path.getmtime(file_path)
            message_age_limit = (
                CONFIG.get("message_age_limit", 2) * 3600
            )  # Convert hours to seconds
            if now - file_mod_time > message_age_limit:
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted old file: {file_path}")
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")


def load_config(args):
    cfg = {
        "api_id": os.getenv("TELEGRAM_API_ID"),
        "api_hash": os.getenv("TELEGRAM_API_HASH"),
        "port": os.getenv("PORT", "3005"),
        "media_folder": os.getenv("MEDIA_FOLDER", "media"),
        "channel_list_file": os.getenv(
            "CHANNEL_LIST_FILE", "channels.json"
        ),  # Ensure this line exists
        "phone_number": os.getenv("PHONE_NUMBER"),
        "message_age_limit": int(os.getenv("MESSAGE_AGE_LIMIT", "2")),
        "default_language": os.getenv("DEFAULT_LANGUAGE", "en"),
        "secret_key": os.getenv("SECRET_KEY", "your_secret_key_here"),
    }

    config_file = args.config_file if args.config_file else "config.json"
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                file_config = json.load(f)
                cfg.update(file_config)
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from config file.")
            sys.exit(1)

    # Check for missing required parameters
    missing_params = []
    if not cfg.get("api_id"):
        missing_params.append("API ID")
    if not cfg.get("api_hash"):
        missing_params.append("API hash")
    if not cfg.get("phone_number"):
        missing_params.append("Phone number")
    if not cfg.get("channel_list_file"):
        missing_params.append(
            "Channel list file"
        )  # Ensure channel_list_file is checked

    if missing_params:
        logger.error(
            f"Missing required configuration: {', '.join(missing_params)}. "
            "Provide them via environment variables or config file."
        )
        sys.exit(1)

    return cfg


async def list_all_channels(telegram_client):
    """
    List all available channels for the Telegram client.
    """
    dialogs = await telegram_client.get_dialogs()
    for dialog in dialogs:
        logger.info("Channel/Group: %s, ID: %s", dialog.name, dialog.id)


def load_channels(channel_list_file):
    try:
        with open(channel_list_file, "r", encoding="utf-8") as f:
            channels = json.load(f)["channels"]
            # Initialize message counters and last processed message ID per channel
            for channel in channels:
                channel_id = channel.get("id")
                channel_name = channel.get("name", "Unknown")
                if channel_id:
                    channel_message_counters[channel_id] = 0
                    LAST_PROCESSED_MESSAGE[channel_id] = None  # Initialize as None
                else:
                    logger.warning(f"Channel without ID found: {channel_name}")
            return channels
    except Exception as e:
        logger.error(f"Failed to load channels from {channel_list_file}: {e}")
        sys.exit(1)


def setup_push_notifications(telegram_client):
    @telegram_client.on(
        events.NewMessage(chats=[channel["id"] for channel in CHANNELS])
    )
    async def new_message_listener(event):
        global REFRESH_FLAG
        try:
            message_content = ""
            if hasattr(event.message, "message") and event.message.message:
                message_content = event.message.message
            elif hasattr(event.message, "caption") and event.message.caption:
                message_content = event.message.caption

            if message_content:
                REFRESH_FLAG = True
                channel_title = event.chat.title if event.chat else "Unknown Channel"
                logger.info(f"New message received in {channel_title}.")

                # Push the new message immediately
                message_data = {
                    "channel": channel_title,
                    "message": message_content,
                    "time": event.message.date.strftime("%Y-%m-%d %H:%M:%S"),
                }
                broadcast_new_message(message_data, is_push=True)
        except Exception as e:
            logger.error(f"Error in new_message_listener: {e}")
            shutdown()  # Signal shutdown on error


def extract_and_replace_urls(message_content):
    logger.debug(f"Original message content before URL extraction: {message_content}")

    # Regular expression to find URLs (more precise to avoid nested href attributes)
    url_regex = re.compile(r'(?<!href=["\'])https?://[^\s<>"\'\)]+', re.UNICODE)

    # Function to replace URLs with anchor tags
    def replace_url(match):
        url = match.group(0).strip()  # Strip any leading/trailing spaces
        title = fetch_url_title(url)
        # Return the proper anchor tag without nesting <a> tags inside href
        return f'<a href="{url}" target="_blank">{url}</a>'

    # Replace URLs in the message content only if they're not already part of an <a> tag
    processed_content = url_regex.sub(replace_url, message_content)

    # Convert the processed content into a BeautifulSoup object for link removal
    soup = BeautifulSoup(processed_content, "html.parser")

    # Remove duplicate links
    remove_duplicate_links(soup)

    # Convert back to a string for further use
    processed_content = str(soup)

    # Log and return the processed content
    logger.debug(
        f"Processed message content after URL extraction and link removal: {processed_content}"
    )
    return processed_content


def create_anchor_tag(url):
    title = fetch_url_title(url)
    return f'<a href="{url}" target="_blank">{title}</a>'


def fetch_url_title(url):
    """
    Fetches the HTML title of the given URL.

    Args:
        url (str): The URL to fetch.

    Returns:
        str: The title of the webpage or the URL itself if the title cannot be fetched.
    """
    try:
        logger.info(f"Attempting to fetch the title for URL: {url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Bot/1.0; +http://example.com/bot)"
        }
        response = requests.get(url, timeout=5, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            logger.info(f"Fetched title for URL: {url} -> Title: {title}")
        else:
            title = url  # Use the URL as the title if none found
            logger.warning(f"No title found in HTML for URL: {url}")
    except Exception as e:
        logger.error(f"Error fetching URL title for {url}: {e}")
        title = url  # Use the URL as the title in case of error
    return title


def remove_duplicate_links(soup):
    seen_links = set()
    for a_tag in soup.find_all("a"):
        href = a_tag.get("href")
        if href in seen_links:
            a_tag.decompose()  # Remove the duplicate link
        else:
            seen_links.add(href)


def broadcast_new_message(message_data, is_push=False):
    # Ensure 'time' is a string
    if isinstance(message_data.get("time"), datetime.datetime):
        message_data["time"] = message_data["time"].strftime("%Y-%m-%d %H:%M:%S")
    else:
        message_data["time"] = str(message_data["time"])

    # Add push indication
    message_data["is_push"] = is_push

    # Extract and replace URLs with titles
    message_data["message"] = extract_and_replace_urls(message_data["message"])

    logger.debug(f"Broadcasting new_message: {message_data}")
    socketio.emit("new_message", message_data)


def download_and_update_message(
    telegram_client, message, media_dir, channel_id, channel_name
):
    global total_messages_processed

    async def download_and_update():
        media_type = "unknown"
        media_size = 0
        download_start_time = time.time()
        try:
            if hasattr(
                message.media, "document"
            ) and message.media.document.mime_type.startswith("video"):
                media_type = "video"
                filename = f"{message.id}.mp4"
            elif hasattr(message.media, "photo"):
                media_type = "photo"
                filename = f"{message.id}.jpg"
            else:
                logger.warning(f"Unsupported media type in message ID: {message.id}")
                return  # Unsupported media

            file_path = os.path.join(media_dir, filename)

            # Download media
            await telegram_client.download_media(message, file=file_path)

            download_end_time = time.time()
            elapsed_time = download_end_time - download_start_time
            media_size = os.path.getsize(file_path)

            logger.info(
                f"Downloaded {media_type} for message ID {message.id}: "
                f"Size={media_size} bytes, Elapsed Time={elapsed_time:.2f} seconds"
            )

            # Embed media tag
            if media_type == "video":
                media_tag = f'<video controls autoplay class="message-video"><source src="/media/{os.path.basename(file_path)}" type="video/mp4">Your browser does not support the video tag.</video>'
            elif media_type == "photo":
                media_tag = f'<img src="/media/{os.path.basename(file_path)}" alt="Photo" class="message-image">'

            # Update the message in LATEST_MESSAGES
            with messages_lock:
                for msg in LATEST_MESSAGES:
                    if msg["id"] == message.id and msg["channel"] == channel_name:
                        msg["message"] += media_tag
                        msg["media_type"] = media_type
                        logger.info(f"Updated message ID {message.id} with media.")
                        # Broadcast the updated message to clients
                        broadcast_new_message(msg)
                        break

        except Exception as e:
            logger.error(
                f"Failed to download {media_type} for message ID {message.id}: {e}"
            )

    # Schedule the coroutine in the Telethon event loop
    asyncio.run_coroutine_threadsafe(download_and_update(), telethon_event_loop)


async def get_latest_messages(telegram_client, cfg):
    global LATEST_MESSAGES, total_messages_fetched, total_messages_processed, channel_message_counters, LAST_PROCESSED_MESSAGE
    media_dir = cfg.get("media_folder", "media")
    message_age_limit = cfg.get("message_age_limit", 2)

    if not os.path.exists(media_dir):
        os.makedirs(media_dir)

    for channel in CHANNELS:
        channel_name = channel.get("name", "Unknown")
        channel_id = channel.get("id")
        if not channel_id:
            logger.error(f"Channel ID missing for channel: {channel_name}")
            continue

        try:
            entity = await telegram_client.get_entity(channel_id)
            messages = await telegram_client.get_messages(
                entity, limit=1
            )  # Fetch the latest message
            fetched_messages = len(messages)
            total_messages_fetched += fetched_messages
            logger.info(f"Fetched {fetched_messages} message from {channel_name}")

            for message in messages:
                logger.debug(
                    f"Processing message ID {message.id} from '{channel_name}'"
                )
                logger.debug(
                    f"Message timestamp: {message.date.strftime('%Y-%m-%d %H:%M:%S')}"
                )

                # Check if this message has already been processed
                if LAST_PROCESSED_MESSAGE.get(channel_id) == message.id:
                    logger.info(
                        f"Skipped message ID {message.id} from '{channel_name}': Duplicate message."
                    )
                    continue  # Skip already processed messages

                # Validate the event loop before proceeding
                if not telethon_event_loop or not telethon_event_loop.is_running():
                    logger.error(
                        "Telethon event loop is not running or has been stopped."
                    )
                    return  # Avoid further processing if the loop isn't valid

                message_time = message.date.replace(tzinfo=datetime.timezone.utc)
                current_time = datetime.datetime.now(datetime.timezone.utc)
                time_diff_in_hours = (
                    current_time - message_time
                ).total_seconds() / 3600

                if time_diff_in_hours > message_age_limit:
                    logger.info(
                        f"Skipped message ID {message.id} from '{channel_name}': Older than {message_age_limit} hours."
                    )
                    continue

                message_content = (
                    message.message
                    if hasattr(message, "message")
                    else (message.caption or "")
                )
                message_content = extract_and_replace_urls(message_content)

                if not message_content.strip():
                    logger.info(
                        f"Skipped message ID {message.id} from '{channel_name}': Empty message content."
                    )
                    continue

                with messages_lock:
                    LATEST_MESSAGES.append(
                        {
                            "id": message.id,
                            "channel": channel_name,
                            "message": message_content,
                            "time": message.date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "timestamp": message_time,
                            "media_type": "text",
                        }
                    )
                    total_messages_processed += 1
                    channel_message_counters[channel_id] += 1
                    LAST_PROCESSED_MESSAGE[channel_id] = message.id

                if message.media:
                    download_and_update_message(
                        telegram_client, message, media_dir, channel_id, channel_name
                    )

        except Exception as e:
            logger.error(f"Error fetching messages for {channel_name}: {e}")
            shutdown()
            raise SystemExit(f"Stopping application due to error: {e}")

    with messages_lock:
        LATEST_MESSAGES = LATEST_MESSAGES[:MAX_LATEST_MESSAGES]
        logger.debug(
            f"Current LATEST_MESSAGES ({len(LATEST_MESSAGES)}): {LATEST_MESSAGES}"
        )

    logger.info(f"Total messages fetched: {total_messages_fetched}")
    logger.info(f"Total messages processed: {total_messages_processed}")


@app.errorhandler(ConnectionAbortedError)
def handle_aborted_connection(e):
    logger.warning(f"Connection aborted: {e}")
    return jsonify({"error": "Connection aborted"}), 500


@app.route("/test")
def test():
    return "Flask server is running"


# Add a route to set the language
@app.route("/set_language/<lang>", methods=["GET"])
def set_language(lang):
    if lang in ["en", "he"]:  # Add your supported languages here
        session["language"] = lang  # Store language in the session
        logger.info(f"Language set to {lang}")
    else:
        logger.warning(f"Unsupported language: {lang}")
    return redirect(
        url_for("display")
    )  # Redirect to refresh the page with new language


@app.route("/")
def display():
    # Get the selected language from the session or fallback to default language in the config
    language = session.get("language", CONFIG.get("default_language", "en"))

    logger.info(f"Using language: {language}")
    translations = load_translations(language)  # Load the appropriate translation file

    # Pass the translations and refresh flag to the template
    return render_template(
        "index.html", translations=translations, refresh_flag=REFRESH_FLAG
    )


@app.route("/fetch-title", methods=["GET"])
def fetch_title():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string if soup.title else url
        return jsonify({"title": title})
    except requests.RequestException as e:
        logger.error(f"Failed to fetch URL {url}: {e}")
        return jsonify({"error": "Failed to fetch title"}), 500


@socketio.on("connect")
def handle_connect(auth):
    logger.info("Client connected.")
    with messages_lock:
        valid_messages = [
            {
                "id": data["id"],
                "channel": data["channel"],
                "message": data["message"],
                "time": data["time"],
            }
            for data in LATEST_MESSAGES
            if data["message"]
        ]
    logger.info(f"Emitting {len(valid_messages)} initial messages to the client.")
    logger.debug(
        f"Valid messages being sent: {json.dumps(valid_messages, ensure_ascii=False)}"
    )
    emit("initial_messages", {"messages": valid_messages})


@socketio.on("disconnect")
def handle_disconnect():
    logger.info("Client disconnected")


@app.route("/media/<path:filename>")
def media(filename):
    media_path = os.path.join(CONFIG["media_folder"], filename)
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
    with open(media_path, "rb") as f:
        f.seek(byte1)
        data = f.read(length)

    rv = Response(
        data,
        206,
        mimetype="video/mp4",
        content_type="video/mp4",
        direct_passthrough=True,
    )
    rv.headers.add("Content-Range", f"bytes {byte1}-{byte1 + len(data) - 1}/{size}")
    return rv


@app.route("/start-over")
def start_over():
    global REFRESH_FLAG
    REFRESH_FLAG = False
    return redirect(url_for("display"))


@app.route("/getMessages")
def get_messages():
    logger.info("==============Processing /getMessages Request==============")
    try:
        global telethon_event_loop
        if telethon_event_loop is None:
            raise RuntimeError("Telethon event loop is not initialized.")

        # Schedule the coroutine in the Telethon event loop
        future = asyncio.run_coroutine_threadsafe(
            get_latest_messages(TELEGRAM_CLIENT, CONFIG), telethon_event_loop
        )
        future.result()  # Wait for the coroutine to complete

        with messages_lock:
            if LATEST_MESSAGES:
                valid_messages = [
                    {
                        "channel": message_data["channel"],
                        "message": message_data["message"],
                        "time": message_data["time"],  # Already a string
                    }
                    for message_data in LATEST_MESSAGES
                ]
                return jsonify({"messages": valid_messages})
            else:
                return jsonify({"status": "No new messages found."})

    except Exception as e:
        logger.error(f"Error fetching latest messages: {e}")
        shutdown()  # Signal shutdown on error
        return jsonify({"error": "Error fetching latest messages"}), 500


@app.route("/shutdown", methods=["POST"])
def shutdown_server():
    secret_token = request.headers.get("X-Shutdown-Token")
    if secret_token != os.getenv("SHUTDOWN_TOKEN"):
        logger.warning("Unauthorized shutdown attempt.")
        return "Unauthorized", 401

    logger.info("Shutting down the Flask-SocketIO server...")
    try:
        # Use SocketIO's stop method
        socketio.stop()
        logger.info("Flask-SocketIO server shutting down...")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
    return "Server shutting down..."


def trigger_client_refresh():
    try:
        # Emit a 'refresh' event to all connected clients
        socketio.emit("refresh", {"message": "Refresh the data"})
        logger.info("Refresh event triggered for clients.")
    except Exception as e:
        logger.error(f"Error triggering refresh event: {e}")


@app.route("/trigger-client-refresh", methods=["POST"])
def trigger_refresh():
    try:
        trigger_client_refresh()
        return jsonify({"status": "Refresh event triggered"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Set up Flask app with secret key
def run_flask(cfg):
    global CONFIG
    CONFIG = cfg

    app.secret_key = CONFIG.get("secret_key")  # Set the secret key from config

    logger.info(f"Starting Flask-SocketIO server on port {CONFIG['port']}...")
    time.sleep(2)

    try:
        eventlet.wsgi.server(
            eventlet.listen(("0.0.0.0", CONFIG["port"])),
            app,
            log_output=True,
            socket_timeout=30,
        )
    except Exception as e:
        logger.error(f"Error running Flask server: {e}")
        shutdown()  # Signal shutdown on error


async def run_telethon_client(cfg):
    try:
        # Telethon runs in its own event loop
        await start_telegram_client()
    except Exception as e:
        logger.error(f"Critical error in Telethon event loop: {e}")
        shutdown()  # Signal shutdown on critical error
    finally:
        # Ensure Telegram client disconnects properly
        if TELEGRAM_CLIENT and TELEGRAM_CLIENT.is_connected():
            try:
                await TELEGRAM_CLIENT.disconnect()
                logger.info("Telegram client disconnected.")
            except Exception as e:
                logger.error(f"Error disconnecting Telethon client: {e}")


async def start_telegram_client():
    global TELEGRAM_CLIENT, telethon_event_loop

    if not CONFIG:
        logger.error("CONFIG is not set. Cannot start Telegram client.")
        shutdown()
        return

    if not CONFIG.get("api_id") or not CONFIG.get("api_hash"):
        logger.error("API ID or API Hash is missing from CONFIG.")
        shutdown()
        return

    session_file = "user_session.session"
    TELEGRAM_CLIENT = TelegramClient(session_file, CONFIG["api_id"], CONFIG["api_hash"])

    try:
        telethon_event_loop = asyncio.get_event_loop()

        while not STOP_EVENT_LOOP:
            try:
                if not TELEGRAM_CLIENT.is_connected():
                    await TELEGRAM_CLIENT.connect()

                if not await TELEGRAM_CLIENT.is_user_authorized():
                    await TELEGRAM_CLIENT.send_code_request(CONFIG["phone_number"])
                    await TELEGRAM_CLIENT.sign_in(
                        CONFIG["phone_number"], input("Enter the code: ")
                    )
                    TELEGRAM_CLIENT.session.save()

                # Set up notifications and message processing
                setup_push_notifications(TELEGRAM_CLIENT)

                # Processing loop
                while not STOP_EVENT_LOOP:
                    await get_latest_messages(TELEGRAM_CLIENT, CONFIG)
                    delete_old_files(CONFIG["media_folder"])
                    await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"Error in Telethon client: {e}")
                await asyncio.sleep(10)

    except Exception as e:
        logger.error(f"Critical error in Telethon event loop: {e}")
        shutdown()
    finally:
        if TELEGRAM_CLIENT and TELEGRAM_CLIENT.is_connected():
            await TELEGRAM_CLIENT.disconnect()


def shutdown_handler(signal_received, frame):
    logger.info("SIGINT or CTRL-C detected. Stopping gracefully...")
    shutdown()


def shutdown():
    global STOP_EVENT_LOOP
    STOP_EVENT_LOOP = True  # Signal the event loop to stop

    try:
        if (
            TELEGRAM_CLIENT
            and TELEGRAM_CLIENT.is_connected()
            and telethon_event_loop.is_running()
        ):
            future = asyncio.run_coroutine_threadsafe(
                TELEGRAM_CLIENT.disconnect(), telethon_event_loop
            )
            future.result(timeout=5)  # Wait for client to disconnect
            logger.info("Telegram client disconnected.")
    except Exception as e:
        logger.error(f"Error disconnecting Telethon client: {e}")
    finally:
        # Stop the Telethon event loop
        if telethon_event_loop and telethon_event_loop.is_running():
            telethon_event_loop.call_soon_threadsafe(telethon_event_loop.stop)
            logger.info("Telethon event loop stopped.")

        # Stop Flask-SocketIO if running
        if flask_thread:
            shutdown_event.set()  # Signal Flask to shut down
            socketio.stop()  # Stop the Flask server
            flask_thread.join()  # Wait for Flask to finish


def run_telethon_thread(cfg):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    logger.info("Telethon event loop created.")

    # Run the Telethon client and keep the loop alive
    try:
        loop.run_until_complete(run_telethon_client(cfg))
        loop_ready_event.set()  # Ensure it's set when Telethon is ready
        loop.run_forever()
    except Exception as e:
        logger.error(f"Error in Telethon loop: {e}")
    finally:
        loop.close()
        logger.info("Telethon event loop closed.")


def main(args):
    global CONFIG
    cfg = load_config(args)
    CONFIG = cfg
    logger.info(f"Loaded CONFIG: {CONFIG}")

    if args.list_channels:
        asyncio.run(
            list_all_channels(
                TelegramClient("user_session", cfg["api_id"], cfg["api_hash"])
            )
        )
        return

    global CHANNELS
    CHANNELS = load_channels(cfg["channel_list_file"])

    # Signal handler for SIGINT (Ctrl + C)
    signal.signal(signal.SIGINT, shutdown_handler)

    global flask_thread, telethon_thread

    # Start Flask thread first
    logger.info("Starting Flask thread...")
    flask_thread = threading.Thread(target=run_flask, args=(cfg,), daemon=True)
    flask_thread.start()

    # Ensure Flask has enough time to initialize before starting Telethon
    time.sleep(2)  # Small delay for Flask initialization

    # Start Telethon thread after Flask
    logger.info("Starting Telethon thread...")
    telethon_thread = threading.Thread(
        target=run_telethon_thread, args=(cfg,), daemon=True
    )
    telethon_thread.start()

    try:
        while not shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Stopping the application.")
        shutdown()
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        shutdown()


if __name__ == "__main__":
    try:
        parser = ArgumentParser()
        parser.add_argument("--api_id", type=int, help="Telegram API ID")
        parser.add_argument("--api_hash", type=str, help="Telegram API Hash")
        parser.add_argument(
            "--port", type=int, help="Port for Flask server", default=3005
        )
        parser.add_argument(
            "--config_file",
            type=str,
            help="Path to configuration file (default: config.json)",
        )
        parser.add_argument(
            "--list-channels", action="store_true", help="List all available channels"
        )
        parser.add_argument(
            "--phone_number", type=str, help="Your phone number for user login"
        )
        parser.add_argument(
            "--media_folder",
            type=str,
            help="Directory for downloaded media files",
            default="media",
        )
        parser.add_argument(
            "--message_age_limit",
            type=int,
            help="Maximum age of messages in hours",
            default=2,
        )

        args = parser.parse_args()
        main(args)
    except KeyboardInterrupt:
        logger.info("Stopping the application...")
        shutdown()
    except Exception as e:
        logger.error(f"Unhandled exception: {e}")
        shutdown()
