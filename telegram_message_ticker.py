"""
This module implements a Flask application that interacts with the Telegram API
to fetch and display messages from specified channels. It supports media file
management and provides push notifications for new messages.
"""

import os
import sys
import json
import threading
import asyncio
import time
import logging
from argparse import ArgumentParser
from queue import Queue
from flask import (
    Flask,
    jsonify,
    render_template,
    redirect,
    url_for,
    send_from_directory,
)
from telethon import TelegramClient, events

app = Flask(__name__)

LATEST_MESSAGES = {}
CHANNELS = []
REFRESH_FLAG = False
TELEGRAM_CLIENT = None
STOP_EVENT_LOOP = False
CONFIG = None
TASK_QUEUE = Queue()

logging.basicConfig(level=logging.WARN)
logger = logging.getLogger(__name__)
logging.getLogger("werkzeug").setLevel(logging.WARNING)


def delete_old_files(media_dir):
    """
    Delete files older than 300 seconds from the media directory.
    """
    now = time.time()
    for filename in os.listdir(media_dir):
        file_path = os.path.join(media_dir, filename)
        if os.path.isfile(file_path):
            file_mod_time = os.path.getmtime(file_path)
            if now - file_mod_time > 300:
                os.remove(file_path)
                logger.info("Deleted old file: %s", file_path)


def load_config(args):
    """
    Load configuration from environment variables or the config file.
    """
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

    missing_params = []
    if not cfg.get("api_id"):
        missing_params.append("API ID")
    if not cfg.get("api_hash"):
        missing_params.append("API hash")
    if not cfg.get("phone_number"):
        missing_params.append("Phone number")

    if missing_params:
        logger.error(
            "Missing required configuration: %s. Provide them via environment variable or config file.",
            ", ".join(missing_params),
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
    """
    Load the list of channels from the specified file.
    """
    with open(channel_list_file, "r", encoding="utf-8") as f:
        return json.load(f)["channels"]


async def get_latest_messages(telegram_client, cfg):
    """
    Fetch the latest messages from the Telegram channels.
    """
    global LATEST_MESSAGES
    all_messages = []
    media_dir = cfg.get("media_folder", "media")
    message_age_limit = cfg.get("message_age_limit", 2)

    if not os.path.exists(media_dir):
        os.makedirs(media_dir)

    for channel in CHANNELS:
        try:
            entity = await telegram_client.get_entity(channel["id"])
            messages = await asyncio.wait_for(
                telegram_client.get_messages(entity, limit=1), timeout=5.0
            )
            if messages:
                for message in messages:
                    current_time = time.time()
                    message_time = message.date.timestamp()
                    time_diff_in_hours = (current_time - message_time) / 3600

                    if time_diff_in_hours > message_age_limit:
                        logger.info(
                            "Skipping message from %s older than %s hours.",
                            channel["name"],
                            message_age_limit,
                        )
                        continue

                    message_content = ""
                    media_type = "text"

                    if message.message:
                        message_content = message.message

                    if message.media:
                        if (
                            hasattr(message.media, "document")
                            and message.media.document.mime_type == "video/mp4"
                        ):
                            for attribute in message.media.document.attributes:
                                if hasattr(attribute, "file_name"):
                                    logger.info(
                                        "Skipping video file: %s", attribute.file_name
                                    )
                                    break
                            else:
                                logger.info(
                                    "Skipping video file with no filename available"
                                )
                            continue
                        elif hasattr(message.media, "photo"):
                            media_tag = ""
                            message_content += media_tag
                            media_type = "photo"

                    if message_content:
                        all_messages.append(
                            {
                                "channel": channel["name"],
                                "message": message_content,
                                "time": message.date,
                                "media_type": media_type,
                            }
                        )
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout while fetching messages for channel: %s", channel["name"]
            )
        except ValueError as e:
            logger.error("Could not fetch messages for %s: %s", channel["name"], e)

    all_messages.sort(key=lambda x: x["time"], reverse=True)
    LATEST_MESSAGES = {msg["channel"]: msg for msg in all_messages}


def setup_push_notifications(telegram_client):
    """
    Set up push notifications for new messages in the specified channels.
    """

    @telegram_client.on(
        events.NewMessage(chats=[channel["id"] for channel in CHANNELS])
    )
    async def new_message_listener(event):
        global REFRESH_FLAG
        if event.message.message:
            REFRESH_FLAG = True


async def process_queue():
    """
    Process the task queue for any pending tasks.
    """
    while not STOP_EVENT_LOOP:
        while not TASK_QUEUE.empty():
            coro, args = TASK_QUEUE.get()
            try:
                await coro(*args)
            except (asyncio.TimeoutError, ValueError) as e:
                logger.error("Error processing task from queue: %s", e)
        await asyncio.sleep(1)


def add_task_to_queue(coro, *args):
    """
    Add tasks to the queue.
    """
    TASK_QUEUE.put((coro, args))


@app.route("/")
def display():
    """
    Display the latest messages on the home page.
    """
    if not LATEST_MESSAGES:
        return render_template("loading.html")

    valid_messages = [
        {"channel": channel, "message": data["message"], "time": data["time"]}
        for channel, data in LATEST_MESSAGES.items()
        if data["message"]
    ]

    return render_template(
        "index.html", messages=valid_messages, refresh_flag=REFRESH_FLAG
    )


@app.route("/media/")
def media(filename):
    """
    Serve media files from the media directory.
    """
    return send_from_directory(CONFIG["media_folder"], filename)


@app.route("/start-over")
def start_over():
    """
    Restart the message fetching process.
    """
    global REFRESH_FLAG
    REFRESH_FLAG = False
    return redirect(url_for("display"))


@app.route("/getMessages")
def get_messages():
    """
    Fetch and return the latest messages.
    """
    try:
        if LATEST_MESSAGES:
            valid_messages = [
                {
                    "channel": channel,
                    "message": message_data["message"],
                    "time": str(message_data["time"]),
                }
                for channel, message_data in LATEST_MESSAGES.items()
                if message_data["message"]
            ]
            return jsonify({"messages": valid_messages})
        else:
            add_task_to_queue(get_latest_messages, TELEGRAM_CLIENT, CONFIG)
            return jsonify(
                {
                    "status": "Messages are being processed, please try again in a moment."
                }
            )
    except Exception as e:
        logger.error("Error adding task to queue: %s", e)
        return jsonify({"error": "Error adding task to queue"}), 500


def run_flask(cfg):
    """
    Run the Flask application.
    """
    global CONFIG
    CONFIG = cfg
    app.run(host="0.0.0.0", port=CONFIG["port"], use_reloader=False, threaded=True)


async def run_telethon_client(cfg):
    """
    Run the Telethon client for fetching messages.
    """
    global TELEGRAM_CLIENT, STOP_EVENT_LOOP
    TELEGRAM_CLIENT = TelegramClient("user_session", cfg["api_id"], cfg["api_hash"])

    await TELEGRAM_CLIENT.connect()
    if not await TELEGRAM_CLIENT.is_user_authorized():
        await TELEGRAM_CLIENT.send_code_request(cfg["phone_number"])
        await TELEGRAM_CLIENT.sign_in(
            cfg["phone_number"], input("Enter the code you received: ")
        )

    setup_push_notifications(TELEGRAM_CLIENT)

    while not STOP_EVENT_LOOP:
        await get_latest_messages(TELEGRAM_CLIENT, cfg)
        delete_old_files(cfg["media_folder"])
        await process_queue()
        await asyncio.sleep(5)


def main(args):
    """
    Main entry point for the application.
    """
    cfg = load_config(args)

    if args.list_channels:
        asyncio.run(
            list_all_channels(
                TelegramClient("user_session", cfg["api_id"], cfg["api_hash"])
            )
        )
        return

    global CHANNELS, TELEGRAM_CLIENT
    CHANNELS = load_channels(cfg["channel_list_file"])

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    TELEGRAM_CLIENT = TelegramClient(
        "user_session", cfg["api_id"], cfg["api_hash"], loop=loop
    )

    async def run_client():
        """
        Start the client and process messages.
        """
        await TELEGRAM_CLIENT.start(phone=cfg["phone_number"])
        setup_push_notifications(TELEGRAM_CLIENT)
        while not STOP_EVENT_LOOP:
            await get_latest_messages(TELEGRAM_CLIENT, cfg)
            delete_old_files(cfg["media_folder"])
            await process_queue()
            await asyncio.sleep(5)

    client_task = loop.create_task(run_client())

    flask_thread = threading.Thread(target=run_flask, args=(cfg,), daemon=True)
    flask_thread.start()

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        client_task.cancel()
        loop.run_until_complete(asyncio.gather(client_task, return_exceptions=True))
        loop.close()


def shutdown():
    """
    Shutdown the application.
    """
    global STOP_EVENT_LOOP
    STOP_EVENT_LOOP = True


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

        args = parser.parse_args()  # Changed from parsed_args to args
        main(args)  # Passes the renamed variable
    except KeyboardInterrupt:
        logger.info("Stopping the application...")
        shutdown()
