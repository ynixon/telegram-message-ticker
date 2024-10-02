# Telegram Message Ticker

This project is a web application that fetches the latest messages from specified Telegram channels using the Telethon library and displays them in a ticker format. It serves as a simple interface to monitor messages, including media such as photos.

## Features

- Fetches and displays messages from Telegram channels.
- Supports displaying text and photo messages.
- Automatically updates the ticker every few seconds.
- Allows manual refresh of the message feed.
- Deletes old media files after a specified period.

## Prerequisites

- Python 3.7 or higher
- Flask
- Telethon
- jQuery

## Setup Instructions

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd <repository-folder>
   ```

2. **Install the required packages:**

   You can use `pip` to install the required libraries. Make sure you have `requirements.txt` in your project directory.

   ```bash
   pip install -r requirements.txt
   ```

3. **Create a configuration file:**

   Create a `config.json` file in the project root with the following structure:

   ```json
   {
     "api_id": <Your_Telegram_API_ID>,
     "api_hash": "<Your_Telegram_API_Hash>",
     "port": 3005,
     "media_folder": "media",
     "channel_list_file": "channels.json",
     "message_age_limit": 2
   }
   ```

   Replace `<Your_Telegram_API_ID>` and `<Your_Telegram_API_Hash>` with your actual Telegram API credentials. The `message_age_limit` specifies the maximum age of messages to fetch, in hours.

4. **Create a channels JSON file:**

   Create a `channels.json` file with the following structure:

   ```json
   {
     "channels": [
       {"id": "channel_id_1", "name": "Channel Name 1"},
       {"id": "channel_id_2", "name": "Channel Name 2"}
     ]
   }
   ```

   Replace `"channel_id_1"` and `"channel_id_2"` with the actual channel IDs you want to monitor. You can list channels using the `--list-channels` command-line argument after providing valid API credentials.

5. **Run the application:**

   Start the application by executing:

   ```bash
   python telegram_message_ticker.py
   ```

   The application will start running on `http://127.0.0.1:3005` by default. You can also specify the port using command-line arguments or the configuration file.

## Running Options

You can run the application with various options:

1. **Environment Variables:**
   - `TELEGRAM_API_ID`: Your Telegram API ID.
   - `TELEGRAM_API_HASH`: Your Telegram API Hash.
   - `PORT`: Port for the Flask server (default is 3005).
   - `MEDIA_FOLDER`: Directory for storing downloaded media files (default is "media").
   - `CHANNEL_LIST_FILE`: Path to the JSON file containing channels (default is "channels.json").
   - `MESSAGE_AGE_LIMIT`: Maximum age of messages in hours (default is 2).

2. **Command-Line Arguments:**
   You can also provide arguments while running the script:

   ```bash
   python telegram_message_ticker.py --api_id <Your_Telegram_API_ID> --api_hash <Your_Telegram_API_Hash> --port 3005 --config_file config.json --list-channels --phone_number <Your_Phone_Number> --message_age_limit 2
   ```

   Replace `<Your_Telegram_API_ID>`, `<Your_Telegram_API_Hash>`, and `<Your_Phone_Number>` with the appropriate values.

3. **Using Configuration File:**
   If you have created the `config.json` file as described in the setup instructions, it will automatically be loaded when running the application without additional arguments.

## Usage

- Open your web browser and navigate to `http://127.0.0.1:3005`.
- The latest messages from the configured Telegram channels will be displayed in a ticker format.
- Use the **Refresh Feed** button to manually refresh the messages.

## File Structure

```
.
├── telegram_message_ticker.py    # Main application file
├── config.json                   # Configuration file for API credentials and settings
├── channels.json                 # JSON file containing the channels to monitor
├── media                         # Directory for storing downloaded media files
├── requirements.txt              # List of required Python packages
└── static                        # Directory for static files (e.g., JavaScript)
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! If you have suggestions for improvements or find bugs, please open an issue or submit a pull request.