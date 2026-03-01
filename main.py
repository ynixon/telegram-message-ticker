"""
Telegram Message Ticker - Android Entry Point (Kivy)

This module serves as the Android APK entry point. It:
  1. Shows a setup screen (pre-filled with saved credentials if available)
  2. Starts the Flask + Telethon backend in a background thread
  3. Shows live progress messages while connecting
  4. Handles Telegram auth-code / 2FA-password entry via a dedicated screen
  5. Renders the web interface inside an Android WebView

For desktop use, run telegram_message_ticker.py directly.
"""

import os
import sys
import json
import threading
import logging
import collections

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

# Thread-safe progress message queue (newest last, shown in loading screen)
_status_msgs = collections.deque(maxlen=12)
_status_lock = threading.Lock()


def _on_status(msg):
    """Receives live progress messages from the background server thread."""
    logger.info("[STATUS] %s", msg)
    with _status_lock:
        _status_msgs.append(msg)


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def get_app_storage():
    """Return a writable directory for app data."""
    if platform == 'android':
        from android.storage import app_storage_path  # noqa
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
        app_dir = os.path.dirname(os.path.abspath(__file__))
        os.chdir(app_dir)

        media_folder = os.path.join(get_app_storage(), 'media')
        os.makedirs(media_folder, exist_ok=True)

        channels_file = os.path.join(get_app_storage(), 'channels.json')
        if not os.path.exists(channels_file):
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
    """Credential entry screen.  Pre-fills fields from saved_cfg when provided."""

    def __init__(self, on_start, saved_cfg=None, **kwargs):
        super().__init__(
            orientation='vertical',
            padding=[30, 40, 30, 24],
            spacing=20,
            **kwargs
        )
        self.on_start_cb = on_start

        self.add_widget(Label(
            text='[b]Telegram Message Ticker[/b]',
            markup=True,
            font_size='24sp',
            size_hint_y=None, height=56,
        ))
        self.add_widget(Label(
            text='Get your credentials at my.telegram.org \u2192 API development tools',
            font_size='13sp',
            color=(0.75, 0.75, 0.75, 1),
            size_hint_y=None, height=44,
            halign='center',
            text_size=(Window.width - 60, None),
        ))

        def _field(label_text, hint, **kw):
            box = BoxLayout(orientation='vertical', size_hint_y=None, height=88, spacing=4)
            box.add_widget(Label(
                text=label_text,
                font_size='14sp',
                color=(0.9, 0.9, 0.9, 1),
                size_hint_y=None, height=26,
                halign='left',
                text_size=(Window.width - 60, None),
            ))
            inp = TextInput(
                hint_text=hint,
                hint_text_color=(0.45, 0.45, 0.45, 1),
                foreground_color=(0, 0, 0, 1),
                background_color=(1, 1, 1, 1),
                cursor_color=(0.1, 0.1, 0.1, 1),
                multiline=False,
                font_size='16sp',
                padding=[12, 14, 12, 14],
                size_hint_y=None, height=58,
                **kw
            )
            box.add_widget(inp)
            return box, inp

        id_box,   self.api_id_input   = _field('API ID',       'e.g. 5390776',                       input_filter='int')
        hash_box, self.api_hash_input = _field('API Hash',     'e.g. 2df8c2493f52845f2045f035499e837b')
        phone_box, self.phone_input   = _field('Phone number', 'e.g. +12223334444')

        for box in (id_box, hash_box, phone_box):
            self.add_widget(box)

        # Pre-fill from saved config so the user can review / edit before starting
        if saved_cfg:
            self.api_id_input.text   = str(saved_cfg.get('api_id',       ''))
            self.api_hash_input.text = str(saved_cfg.get('api_hash',     ''))
            self.phone_input.text    = str(saved_cfg.get('phone_number', ''))

        start_btn = Button(
            text='Start',
            size_hint_y=None, height=58,
            font_size='17sp',
            background_color=(0.18, 0.55, 0.88, 1),
        )
        start_btn.bind(on_press=self._submit)
        self.add_widget(start_btn)

        self.msg_label = Label(
            text='',
            color=(1, 0.35, 0.35, 1),
            font_size='14sp',
            size_hint_y=None, height=44,
        )
        self.add_widget(self.msg_label)
        self.add_widget(Widget())

    def _submit(self, *_):
        api_id_text   = self.api_id_input.text.strip()
        api_hash_text = self.api_hash_input.text.strip()

        if not api_id_text or not api_hash_text:
            self.msg_label.text = 'API ID and API Hash are required.'
            return

        cfg = {
            'api_id':            int(api_id_text),
            'api_hash':          api_hash_text,
            'phone_number':      self.phone_input.text.strip(),
            'port':              SERVER_PORT,
            'media_folder':      'media',
            'channel_list_file': os.path.join(get_app_storage(), 'channels.json'),
            'message_age_limit': 2,
            'default_language':  'en',
            'secret_key':        os.urandom(16).hex(),
        }
        save_config(cfg)
        self.on_start_cb(cfg)


class AuthScreen(BoxLayout):
    """Telegram verification-code or 2FA-password entry screen."""

    def __init__(self, kind, phone, on_submit, **kwargs):
        super().__init__(orientation='vertical', padding=[30, 50, 30, 24], spacing=20, **kwargs)

        if kind == 'code':
            title   = 'Verification Code'
            desc    = f'A login code was sent to {phone or "your phone"}.\nEnter it below:'
            hint    = '12345'
            is_pw   = False
        else:
            title   = 'Two-Step Verification'
            desc    = 'Your account has 2FA enabled.\nEnter your Telegram password:'
            hint    = 'Password'
            is_pw   = True

        self.add_widget(Label(
            text=f'[b]{title}[/b]',
            markup=True,
            font_size='22sp',
            size_hint_y=None, height=52,
        ))
        self.add_widget(Label(
            text=desc,
            font_size='14sp',
            color=(0.8, 0.8, 0.8, 1),
            halign='center',
            text_size=(Window.width - 60, None),
            size_hint_y=None, height=64,
        ))

        self._inp = TextInput(
            hint_text=hint,
            hint_text_color=(0.45, 0.45, 0.45, 1),
            foreground_color=(0, 0, 0, 1),
            background_color=(1, 1, 1, 1),
            cursor_color=(0.1, 0.1, 0.1, 1),
            password=is_pw,
            multiline=False,
            font_size='22sp',
            size_hint_y=None, height=64,
            padding=[12, 16, 12, 16],
        )
        self.add_widget(self._inp)

        btn = Button(
            text='Confirm',
            size_hint_y=None, height=58,
            font_size='17sp',
            background_color=(0.18, 0.55, 0.88, 1),
        )
        btn.bind(on_press=lambda *_: on_submit(self._inp.text.strip()))
        self.add_widget(btn)

        self._err = Label(
            text='',
            color=(1, 0.35, 0.35, 1),
            font_size='13sp',
            size_hint_y=None, height=36,
        )
        self.add_widget(self._err)
        self.add_widget(Widget())


class LoadingScreen(BoxLayout):
    """Progress screen shown while the server is starting up."""

    def __init__(self, on_reset, **kwargs):
        super().__init__(orientation='vertical', padding=[32, 40, 32, 24], spacing=12, **kwargs)
        self._on_reset = on_reset

        # Latest status line shown in bold at the top
        self._status = Label(
            text='Starting\u2026',
            font_size='16sp',
            bold=True,
            size_hint_y=None, height=36,
            halign='center',
            color=(1, 1, 1, 1),
            text_size=(Window.width - 64, None),
        )
        self.add_widget(self._status)

        # Scrolling log of previous status lines
        self._log = Label(
            text='',
            font_size='12sp',
            halign='left',
            valign='top',
            color=(0.6, 0.85, 0.6, 1),
            text_size=(Window.width - 64, None),
            size_hint_y=1,
        )
        self.add_widget(self._log)

        self._reset_btn = Button(
            text='Reset Credentials & Try Again',
            size_hint_y=None, height=56,
            font_size='15sp',
            background_color=(0.75, 0.18, 0.18, 1),
            opacity=0,
            disabled=True,
        )
        self._reset_btn.bind(on_press=lambda *_: self._on_reset())
        self.add_widget(self._reset_btn)

    def update_status(self, msgs):
        """Show newest message as the header; older ones in the log area."""
        if msgs:
            self._status.text = msgs[-1]
            self._log.text    = '\n'.join(list(msgs)[:-1])

    def set_text(self, text):
        self._status.text = text

    def show_reset_button(self):
        self._reset_btn.opacity  = 1
        self._reset_btn.disabled = False


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class TelegramTickerApp(App):
    """Kivy application shell that embeds the Flask web UI in a WebView."""

    def build(self):
        Window.clearcolor = (0.07, 0.07, 0.07, 1)
        self._container = BoxLayout()
        # Always show the setup screen; pre-fill with any saved credentials
        # so returning users can review and just tap Start.
        cfg = load_saved_config()
        self._container.add_widget(
            SetupScreen(on_start=self._begin_server, saved_cfg=cfg)
        )
        return self._container

    # ------------------------------------------------------------------
    def _begin_server(self, cfg):
        global _server_error
        _server_error = None
        with _status_lock:
            _status_msgs.clear()

        # Wire progress and auth callbacks into the backend module
        import telegram_message_ticker as _tmt
        _tmt._status_cb     = _on_status
        _tmt._needs_auth_cb = self._show_auth_screen

        self._container.clear_widgets()
        self._loading = LoadingScreen(on_reset=self._reset)
        self._container.add_widget(self._loading)

        threading.Thread(target=run_server, args=(cfg,), daemon=True).start()
        Clock.schedule_interval(self._poll_server, 1.0)

    # ------------------------------------------------------------------
    def _show_auth_screen(self, kind, phone):
        """Called from the background thread; switches to auth UI on the main thread."""
        def _do(dt):
            from telegram_message_ticker import provide_auth_code, provide_2fa_password
            provide = provide_auth_code if kind == 'code' else provide_2fa_password

            def _submit(value):
                if not value:
                    return
                # Return to loading screen and resume polling
                self._container.clear_widgets()
                self._loading = LoadingScreen(on_reset=self._reset)
                self._container.add_widget(self._loading)
                Clock.schedule_interval(self._poll_server, 1.0)
                provide(value)

            self._container.clear_widgets()
            self._container.add_widget(
                AuthScreen(kind=kind, phone=phone, on_submit=_submit)
            )
        Clock.schedule_once(_do, 0)

    # ------------------------------------------------------------------
    def _reset(self):
        global _server_error
        _server_error = None
        with _status_lock:
            _status_msgs.clear()
        try:
            os.remove(get_config_path())
        except OSError:
            pass
        self._container.clear_widgets()
        self._container.add_widget(
            SetupScreen(on_start=self._begin_server, saved_cfg={})
        )

    # ------------------------------------------------------------------
    def _poll_server(self, dt):
        """Drains status queue, checks for errors, and detects when Flask is up."""
        # Update loading screen with latest progress messages
        with _status_lock:
            msgs = list(_status_msgs)
        if msgs and hasattr(self, '_loading') and self._loading.parent:
            self._loading.update_status(msgs)

        if _server_error:
            if hasattr(self, '_loading') and self._loading.parent:
                self._loading.set_text(f'Error: {_server_error}')
                self._loading.show_reset_button()
            return False  # stop polling

        import socket
        try:
            sock = socket.create_connection(('127.0.0.1', SERVER_PORT), timeout=0.5)
            sock.close()
            Clock.schedule_once(lambda _dt: self._open_webview(), 0.8)
            return False  # stop polling
        except OSError:
            pass

    # ------------------------------------------------------------------
    def _open_webview(self):
        url = f'http://localhost:{SERVER_PORT}'

        if platform == 'android':
            from android.runnable import run_on_ui_thread  # noqa

            @run_on_ui_thread
            def _show():
                from jnius import autoclass  # noqa
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                WebView        = autoclass('android.webkit.WebView')
                WebViewClient  = autoclass('android.webkit.WebViewClient')

                activity = PythonActivity.mActivity
                wv = WebView(activity)

                settings = wv.getSettings()
                settings.setJavaScriptEnabled(True)
                settings.setDomStorageEnabled(True)
                settings.setMediaPlaybackRequiresUserGesture(False)
                settings.setAllowFileAccess(True)
                settings.setAllowContentAccess(True)

                wv.setWebViewClient(WebViewClient())
                activity.setContentView(wv)
                wv.loadUrl(url)

            _show()

        else:
            self._loading.set_text(
                f'Server is running!\n\nOpen your browser at:\n{url}'
            )


if __name__ == '__main__':
    TelegramTickerApp().run()
