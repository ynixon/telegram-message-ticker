"""
Telegram Message Ticker - Android Entry Point (Kivy)

This module serves as the Android APK entry point. It:
  1. Shows a setup screen if no credentials are saved
  2. Starts the Flask + Telethon backend in a background thread
  3. Renders the web interface inside an Android WebView

For desktop use, run telegram_message_ticker.py directly.
"""

import os
import sys
import json
import threading
import logging

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.clock import Clock
from kivy.utils import platform
from kivy.core.window import Window

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SERVER_PORT = 3005
_server_error = None


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def get_app_storage():
    """Return a writable directory for app data."""
    if platform == 'android':
        from android.storage import app_storage_path  # noqa: pylint: disable=import-error
        return app_storage_path()
    return os.path.dirname(os.path.abspath(__file__))


def get_config_path():
    return os.path.join(get_app_storage(), 'config.json')


def load_saved_config():
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(data):
    os.makedirs(os.path.dirname(get_config_path()), exist_ok=True)
    with open(get_config_path(), 'w') as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Backend server
# ---------------------------------------------------------------------------

class _Args:
    """Simple namespace that mimics argparse.Namespace."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def run_server(cfg):
    """Start Flask + Telethon in the current thread (called from daemon thread)."""
    global _server_error
    try:
        # Work from the directory that contains this file so that templates/
        # static/ can be found by Flask.
        app_dir = os.path.dirname(os.path.abspath(__file__))
        os.chdir(app_dir)

        media_folder = os.path.join(get_app_storage(), 'media')
        os.makedirs(media_folder, exist_ok=True)

        channels_file = os.path.join(get_app_storage(), 'channels.json')
        if not os.path.exists(channels_file):
            # Copy the example file or create an empty one
            example = os.path.join(app_dir, 'channels.json.example')
            if os.path.exists(example):
                import shutil
                shutil.copy(example, channels_file)
            else:
                with open(channels_file, 'w') as fh:
                    json.dump({"channels": []}, fh)

        args = _Args(
            api_id=int(cfg['api_id']),
            api_hash=str(cfg['api_hash']),
            port=SERVER_PORT,
            config_file=get_config_path(),
            list_channels=False,
            phone_number=str(cfg.get('phone_number', '')),
            media_folder=media_folder,
            message_age_limit=int(cfg.get('message_age_limit', 2)),
        )

        from telegram_message_ticker import main as ticker_main
        ticker_main(args)

    except Exception as exc:
        logger.error("Server error: %s", exc, exc_info=True)
        _server_error = str(exc)


# ---------------------------------------------------------------------------
# Kivy screens
# ---------------------------------------------------------------------------

class SetupScreen(BoxLayout):
    """First-run screen asking for Telegram API credentials."""

    def __init__(self, on_start, **kwargs):
        super().__init__(
            orientation='vertical',
            padding=[24, 48, 24, 24],
            spacing=14,
            **kwargs
        )
        self.on_start_cb = on_start

        self.add_widget(Label(
            text='[b]Telegram Message Ticker[/b]',
            markup=True,
            font_size='22sp',
            size_hint_y=None, height=52,
        ))
        self.add_widget(Label(
            text='Enter your API credentials from my.telegram.org',
            font_size='13sp',
            color=(0.7, 0.7, 0.7, 1),
            size_hint_y=None, height=38,
        ))

        self.api_id_input = TextInput(
            hint_text='API ID (numbers only)',
            multiline=False,
            input_filter='int',
            size_hint_y=None, height=46,
        )
        self.api_hash_input = TextInput(
            hint_text='API Hash',
            multiline=False,
            size_hint_y=None, height=46,
        )
        self.phone_input = TextInput(
            hint_text='Phone number  e.g. +12223334444',
            multiline=False,
            size_hint_y=None, height=46,
        )

        for widget in (self.api_id_input, self.api_hash_input, self.phone_input):
            self.add_widget(widget)

        start_btn = Button(
            text='Start',
            size_hint_y=None, height=52,
            background_color=(0.18, 0.55, 0.88, 1),
        )
        start_btn.bind(on_press=self._submit)
        self.add_widget(start_btn)

        self.msg_label = Label(
            text='',
            color=(1, 0.35, 0.35, 1),
            size_hint_y=None, height=40,
        )
        self.add_widget(self.msg_label)

        # Spacer
        self.add_widget(Widget())

    def _submit(self, *_):
        api_id_text = self.api_id_input.text.strip()
        api_hash_text = self.api_hash_input.text.strip()

        if not api_id_text or not api_hash_text:
            self.msg_label.text = 'API ID and API Hash are required.'
            return

        cfg = {
            'api_id': int(api_id_text),
            'api_hash': api_hash_text,
            'phone_number': self.phone_input.text.strip(),
            'port': SERVER_PORT,
            'media_folder': 'media',
            'channel_list_file': os.path.join(get_app_storage(), 'channels.json'),
            'message_age_limit': 2,
            'default_language': 'en',
            'secret_key': os.urandom(16).hex(),
        }
        save_config(cfg)
        self.on_start_cb(cfg)


class LoadingScreen(BoxLayout):
    """Shown while the server is starting up."""

    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', **kwargs)
        self._label = Label(
            text='Starting server\u2026',
            font_size='17sp',
        )
        self.add_widget(self._label)

    def set_text(self, text):
        self._label.text = text


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class TelegramTickerApp(App):
    """Kivy application shell that embeds the Flask web UI in a WebView."""

    def build(self):
        Window.clearcolor = (0.07, 0.07, 0.07, 1)
        self._container = BoxLayout()

        cfg = load_saved_config()
        if cfg.get('api_id') and cfg.get('api_hash'):
            self._begin_server(cfg)
        else:
            self._container.add_widget(SetupScreen(on_start=self._begin_server))

        return self._container

    # ------------------------------------------------------------------
    def _begin_server(self, cfg):
        """Replace whatever is on screen with a loading screen and
        launch the backend server thread."""
        self._container.clear_widgets()
        self._loading = LoadingScreen()
        self._container.add_widget(self._loading)

        threading.Thread(
            target=run_server,
            args=(cfg,),
            daemon=True,
        ).start()

        Clock.schedule_interval(self._poll_server, 1.0)

    # ------------------------------------------------------------------
    def _poll_server(self, dt):
        """Check whether the Flask server is accepting connections yet."""
        if _server_error:
            self._loading.set_text(
                f'Server error:\n{_server_error}\n\nCheck your credentials and restart.'
            )
            return False  # stop polling

        import socket
        try:
            sock = socket.create_connection(('127.0.0.1', SERVER_PORT), timeout=0.5)
            sock.close()
            # Server is up - open the WebView after a short delay
            Clock.schedule_once(lambda _dt: self._open_webview(), 0.8)
            return False  # stop polling
        except OSError:
            self._loading.set_text('Connecting to Telegram\u2026')

    # ------------------------------------------------------------------
    def _open_webview(self):
        """Replace the Kivy UI with an Android WebView (Android only).
        On desktop, simply display the server URL."""
        url = f'http://localhost:{SERVER_PORT}'

        if platform == 'android':
            from android.runnable import run_on_ui_thread  # noqa: pylint: disable=import-error

            @run_on_ui_thread
            def _show():
                from jnius import autoclass  # noqa: pylint: disable=import-error
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                WebView = autoclass('android.webkit.WebView')
                WebViewClient = autoclass('android.webkit.WebViewClient')

                activity = PythonActivity.mActivity
                wv = WebView(activity)

                settings = wv.getSettings()
                settings.setJavaScriptEnabled(True)
                settings.setDomStorageEnabled(True)
                settings.setMediaPlaybackRequiresUserGesture(False)
                settings.setAllowFileAccess(True)
                settings.setAllowContentAccess(True)

                wv.setWebViewClient(WebViewClient())

                # Replace the whole content view with the WebView so it
                # fills the entire screen (Kivy canvas is hidden behind it).
                activity.setContentView(wv)
                wv.loadUrl(url)

            _show()

        else:
            # Desktop fallback
            self._loading.set_text(
                f'Server is running!\n\nOpen your browser at:\n{url}'
            )


if __name__ == '__main__':
    TelegramTickerApp().run()
