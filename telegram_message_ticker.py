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
from bs4 import BeautifulSoup, NavigableString, Tag
from argparse import ArgumentParser
from flask import (
    Flask,
    jsonify,
    render_template,
    redirect,
    url_for,
    send_from_directory,
    request,
)
from flask_socketio import SocketIO, emit  # Import SocketIO
from telethon import TelegramClient, events
import requests


app = Flask(__name__)
socketio = SocketIO(app)  # Initialize SocketIO

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
    level=logging.WARN,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

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
        "channel_list_file": os.getenv("CHANNEL_LIST_FILE", "channels.json"),
        "phone_number": os.getenv("PHONE_NUMBER"),
        "message_age_limit": int(os.getenv("MESSAGE_AGE_LIMIT", "2")),
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

    # Override with command-line arguments if provided
    if args.api_id:
        cfg["api_id"] = args.api_id
    if args.api_hash:
        cfg["api_hash"] = args.api_hash
    if args.port:
        cfg["port"] = args.port
    if args.media_folder:
        cfg["media_folder"] = args.media_folder
    if args.message_age_limit:
        cfg["message_age_limit"] = args.message_age_limit

    cfg["port"] = int(cfg["port"])

    # Check for missing required parameters
    missing_params = []
    if not cfg.get("api_id"):
        missing_params.append("API ID")
    if not cfg.get("api_hash"):
        missing_params.append("API hash")
    if not cfg.get("phone_number"):
        missing_params.append("Phone number")

    if missing_params:
        logger.error(
            f"Missing required configuration: {', '.join(missing_params)}. "
            f"Provide them via environment variables or config file."
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
            if event.message.message:
                REFRESH_FLAG = True
                channel_title = event.chat.title if event.chat else "Unknown Channel"
                logger.info(f"New message received in {channel_title}.")

                # Push the new message immediately
                message_data = {
                    "channel": channel_title,
                    "message": event.message.message,
                    "time": event.message.date.strftime("%Y-%m-%d %H:%M:%S"),
                }
                broadcast_new_message(message_data, is_push=True)
        except Exception as e:
            logger.error(f"Error in new_message_listener: {e}")
            shutdown()  # Signal shutdown on error


def extract_and_replace_urls(message_content):
    # Parse the message content as HTML
    soup = BeautifulSoup(message_content, "html.parser")

    # Function to recursively replace URLs in text nodes
    def replace_in_text_nodes(element):
        for child in element.contents:
            if isinstance(child, NavigableString):
                # Skip if parent is an anchor tag
                if child.parent.name == "a":
                    continue

                # Replace URLs in the text node
                new_text = re.sub(
                    r"(?P<url>https?://[^\s<]+)",
                    lambda match: create_anchor_tag(match.group(0)),
                    str(child),
                )
                if new_text != str(child):
                    # Replace the text node with new content
                    child.replace_with(BeautifulSoup(new_text, "html.parser"))
            elif isinstance(child, Tag):
                # Recursively process child tags
                if child.name != "a":  # Do not process inside <a> tags
                    replace_in_text_nodes(child)

    replace_in_text_nodes(soup)
    remove_duplicate_links(soup)

    return str(soup)


def create_anchor_tag(url):
    title = fetch_url_title(url)
    return f'<a href="{url}" target="_blank">{title}</a>'


def fetch_url_title(url):
    try:
        logger.info(f"Attempting to fetch the title for URL: {url}")
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; Bot/1.0; +http://example.com/bot)"
        }
        response = requests.get(url, timeout=5, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
                logger.info(f"Fetched title for URL: {url} -> Title: {title}")
            else:
                title = url  # Use the URL as the title if none found
                logger.warning(f"No title found in HTML for URL: {url}")
        else:
            title = url
            logger.warning(
                f"Failed to get a successful response for URL: {url}, status code: {response.status_code}"
            )
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
                    if (
                        msg["timestamp"] == message.date.timestamp()
                        and msg["channel"] == channel_name
                    ):
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
                # Log message details
                logger.debug(
                    f"Processing message ID {message.id} from '{channel_name}'"
                )
                logger.debug(
                    f"Message timestamp: {message.date.strftime('%Y-%m-%d %H:%M:%S')}"
                )

                # Check if this message has already been processed
                if LAST_PROCESSED_MESSAGE[channel_id] == message.id:
                    logger.info(
                        f"Skipped message ID {message.id} from '{channel_name}': Duplicate message."
                    )
                    continue  # Skip already processed messages

                # Use message.date directly; it's already a datetime object
                message_time = message.date
                # Ensure message_time is timezone-aware in UTC
                if message_time.tzinfo is None:
                    message_time = message_time.replace(tzinfo=datetime.timezone.utc)
                else:
                    message_time = message_time.astimezone(datetime.timezone.utc)

                # Get current time in UTC
                current_time = datetime.datetime.now(datetime.timezone.utc)
                time_diff_in_hours = (
                    current_time - message_time
                ).total_seconds() / 3600

                logger.info(
                    f"Message time: {message_time}, "
                    f"Current time: {current_time}, "
                    f"Time difference in hours: {time_diff_in_hours}, "
                    f"Message Age Limit: {message_age_limit}"
                )

                if time_diff_in_hours > message_age_limit:
                    logger.info(
                        f"Skipped message ID {message.id} from '{channel_name}': Older than {message_age_limit} hours."
                    )
                    continue  # Skip old messages
                else:
                    logger.info(
                        f"Adding message ID {message.id} from '{channel_name}' timestamp '{message_time}'"
                    )

                message_content = ""
                media_type = "text"

                logger.debug(
                    f"Message content for message ID {message.id}: {message_content}"
                )

                if message.message:
                    message_content = message.message
                    if isinstance(message_content, bytes):
                        message_content = message_content.decode("utf-8")
                    # Ensure message_content is a string
                    if not isinstance(message_content, str):
                        message_content = str(message_content)

                # Log about to add message
                logger.debug(
                    f"Adding message ID {message.id} from '{channel_name}' with content length {len(message_content)}."
                )

                # Add message without media first
                with messages_lock:
                    LATEST_MESSAGES.append(
                        {
                            "id": message.id,  # Add message ID here
                            "channel": channel_name,
                            "message": message_content,
                            "time": message.date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "timestamp": message_time,
                            "media_type": media_type,
                        }
                    )
                    total_messages_processed += 1
                    channel_message_counters[channel_id] += 1
                    logger.info(
                        f"Added message #{channel_message_counters[channel_id]} from channel '{channel_name}' timestamp '{message_time}' (Total Processed: {total_messages_processed})"
                    )

                # Update the last processed message ID for the channel
                LAST_PROCESSED_MESSAGE[channel_id] = message.id

                # If media exists, download asynchronously and update the message
                if message.media:
                    download_and_update_message(
                        telegram_client, message, media_dir, channel_id, channel_name
                    )

        except Exception as e:
            logger.error(f"Error fetching messages for {channel_name}: {e}")
            shutdown()  # Signal shutdown on error
            raise SystemExit(f"Stopping application due to error: {e}")

    # Sort messages after processing
    with messages_lock:
        LATEST_MESSAGES.sort(key=lambda x: x["timestamp"], reverse=True)
        logger.debug(
            f"Current LATEST_MESSAGES ({len(LATEST_MESSAGES)}): {LATEST_MESSAGES}"
        )

    logger.info(f"Total messages fetched: {total_messages_fetched}")
    logger.info(f"Total messages processed: {total_messages_processed}")


@app.route("/")
def display():
    logger.debug("Rendering index.html with initial parameters.")
    return render_template("index.html", messages=[], refresh_flag=REFRESH_FLAG)


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


@app.route("/media/<path:filename>")
def media(filename):
    return send_from_directory(CONFIG["media_folder"], filename)


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


def run_flask(cfg):
    global CONFIG
    CONFIG = cfg
    try:
        logger.info("Starting Flask-SocketIO server...")
        socketio.run(
            app, host="0.0.0.0", port=CONFIG["port"], use_reloader=False, debug=False
        )
        logger.info("Flask-SocketIO server started successfully.")
    except Exception as e:
        logger.error(f"Error running Flask server: {e}")
        shutdown()  # Signal shutdown on error


def run_telethon_client(cfg):
    global TELEGRAM_CLIENT
    global telethon_event_loop

    async def start_telegram_client():
        global TELEGRAM_CLIENT
        session_file = "user_session.session"  # Use a consistent session file name
        TELEGRAM_CLIENT = TelegramClient(session_file, cfg["api_id"], cfg["api_hash"])
        attempt = 0  # Counter for retry attempts

        while not STOP_EVENT_LOOP:
            try:
                if not TELEGRAM_CLIENT.is_connected():
                    await TELEGRAM_CLIENT.connect()

                if not await TELEGRAM_CLIENT.is_user_authorized():
                    await TELEGRAM_CLIENT.send_code_request(cfg["phone_number"])
                    await TELEGRAM_CLIENT.sign_in(
                        cfg["phone_number"], input("Enter the code: ")
                    )
                    TELEGRAM_CLIENT.session.save()  # Save session after signing in

                setup_push_notifications(TELEGRAM_CLIENT)

                # Main message processing loop
                while not STOP_EVENT_LOOP:
                    await get_latest_messages(TELEGRAM_CLIENT, cfg)
                    delete_old_files(cfg["media_folder"])
                    await asyncio.sleep(10)  # Throttle to avoid hitting rate limits
            except Exception as e:
                attempt += 1
                logger.error(f"Error in Telethon client (Attempt {attempt}): {e}")

                # Incremental backoff to avoid rapid retries
                backoff = min(60, attempt * 5)
                logger.info(f"Attempting to reconnect after {backoff} seconds...")
                await asyncio.sleep(backoff)

                if attempt > 10:  # Increase the number of reconnection attempts
                    logger.error("Too many reconnection attempts. Stopping client.")
                    shutdown()  # Gracefully shutdown after multiple failures
                    break

    # Create and share the event loop for the thread
    telethon_event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(telethon_event_loop)

    # Signal that the loop is ready
    loop_ready_event.set()

    # Run everything within this loop
    try:
        telethon_event_loop.run_until_complete(start_telegram_client())
    except Exception as e:
        logger.error(f"Critical error in Telethon event loop: {e}")
        shutdown()  # Signal shutdown on critical error
    finally:
        # Ensure Telegram client disconnects properly
        if TELEGRAM_CLIENT and TELEGRAM_CLIENT.is_connected():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    TELEGRAM_CLIENT.disconnect(), telethon_event_loop
                )
                future.result(timeout=5)
                logger.info("Telegram client disconnected.")
            except Exception as e:
                logger.error(f"Error disconnecting Telethon client: {e}")

        # Ensure all asyncio event loops are stopped
        if telethon_event_loop and telethon_event_loop.is_running():
            telethon_event_loop.call_soon_threadsafe(telethon_event_loop.stop)
            telethon_event_loop.run_until_complete(
                asyncio.sleep(0)
            )  # Allow all tasks to complete
            telethon_event_loop.close()


def shutdown_handler(signal_received, frame):
    logger.info("SIGINT or CTRL-C detected. Stopping gracefully...")
    shutdown()


def shutdown():
    global STOP_EVENT_LOOP
    STOP_EVENT_LOOP = True  # Signal the event loop to stop

    # Ensure Telegram client disconnects properly
    if TELEGRAM_CLIENT and TELEGRAM_CLIENT.is_connected():
        try:
            if telethon_event_loop and telethon_event_loop.is_running():
                # Schedule a coroutine to disconnect the Telegram client in the running loop
                future = asyncio.run_coroutine_threadsafe(
                    TELEGRAM_CLIENT.disconnect(), telethon_event_loop
                )
                future.result(timeout=5)
                logger.info("Telegram client disconnected.")
            else:
                logger.warning(
                    "Telethon event loop is not running. Skipping disconnect."
                )
        except Exception as e:
            logger.error(f"Error disconnecting Telethon client: {e}")

    # Shutdown Flask server by calling the shutdown route with the correct token
    try:
        if CONFIG and CONFIG.get("port"):
            shutdown_token = os.getenv("SHUTDOWN_TOKEN")
            headers = {"X-Shutdown-Token": shutdown_token}
            response = requests.post(
                f'http://127.0.0.1:{CONFIG["port"]}/shutdown',
                headers=headers,
                timeout=5,
            )
            if response.status_code == 200:
                logger.info("Shutdown request sent to Flask server successfully.")
            else:
                logger.error(
                    f"Failed to shutdown Flask server: {response.status_code} {response.text}"
                )
        else:
            logger.error(
                "CONFIG is not set or port is missing. Cannot shutdown Flask server."
            )
    except Exception as e:
        logger.error(f"Error shutting down Flask server: {e}")

    logger.info("Shutting down the application...")

    # Stop and close the event loop safely
    if telethon_event_loop and telethon_event_loop.is_running():
        try:
            telethon_event_loop.call_soon_threadsafe(telethon_event_loop.stop)
            telethon_event_loop.run_until_complete(
                asyncio.sleep(0)
            )  # Allow all tasks to complete
            logger.info("Telethon event loop stopped.")
        except Exception as e:
            logger.error(f"Error stopping Telethon event loop: {e}")

    if telethon_event_loop and not telethon_event_loop.is_closed():
        try:
            telethon_event_loop.close()
            logger.info("Telethon event loop closed.")
        except Exception as e:
            logger.error(f"Error closing Telethon event loop: {e}")

    # Wait for Flask and Telethon threads to stop
    if flask_thread:
        flask_thread.join()
    if telethon_thread:
        telethon_thread.join()

    # Signal the main thread to exit
    shutdown_event.set()


def main(args):
    cfg = load_config(args)

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
    # Start Telethon thread first to initialize the event loop
    telethon_thread = threading.Thread(
        target=lambda: asyncio.run(run_telethon_client(cfg)), daemon=True
    )
    telethon_thread.start()

    # Wait until the Telethon event loop is ready
    loop_ready_event.wait()

    # Start Flask thread after Telethon is ready
    flask_thread = threading.Thread(target=run_flask, args=(cfg,), daemon=True)
    flask_thread.start()

    try:
        # Keep the main thread alive, allowing it to receive signals
        while not shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping the application...")
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
