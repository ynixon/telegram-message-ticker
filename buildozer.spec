[app]

# App metadata
title = Telegram Message Ticker
package.name = telegramticker
package.domain = com.ynixon

# Source
source.dir = .
source.include_exts = py,png,jpg,jpeg,gif,kv,atlas,json,css,js,html,txt
source.include_patterns = templates/*,static/*,translations/*,*.json,*.example

# Version (increment numeric_version on every release so Android accepts updates)
version = 2.3
android.numeric_version = 14

# Requirements
# NOTE: newspaper3k is intentionally excluded – it is not imported by the
#       application code and brings in heavy native dependencies.
#
# Flask 2.x dropped setup.py (moved to pyproject.toml), which breaks the
# python-for-android recipe build system that calls "python setup.py install".
# We pin the entire Flask ecosystem to the last Flask-1.x-compatible stack:
#   - flask 1.1.4 (last 1.x release, has setup.py)
#   - werkzeug 1.0.1 (flask 1.x is incompatible with werkzeug 2.x at runtime)
#   - jinja2 2.11.3 / markupsafe 1.1.1 (matching pair, both have setup.py)
#   - itsdangerous 1.1.0 (2.x removed APIs that flask 1.x uses)
#   - click 7.1.2 (flask 1.x CLI is incompatible with click 8.x)
#   - flask-socketio 4.3.2 (5.x requires flask 2.x)
#   - python-socketio 4.6.1 (required by flask-socketio 4.x)
#   - python-engineio 3.14.2 (required by python-socketio 4.x)
#   - greenlet 2.0.2 (old greenlet uses CPython internals removed in Python 3.11:
#       exc_type/exc_traceback on _err_stackitem, recursion_depth on _ts, frame on _ts,
#       use_tracing on _ts, lvalue Py_REFCNT; all fixed in greenlet 2.0.0)
#   - eventlet 0.33.3 (first eventlet release with Python 3.11 + greenlet 2.x support)
#   - lxml excluded: lxml 4.8.0 (p4a recipe default) uses Cython-generated C that
#     directly accesses PyFrameObject internals (f_back, f_lineno) which became an
#     opaque type in Python 3.11.  The app uses BeautifulSoup exclusively with
#     "html.parser" (Python's built-in parser), never "lxml", so the dependency
#     is unused and safe to drop.
requirements =
    python3,
    kivy==2.3.0,
    flask==1.1.4,
    werkzeug==1.0.1,
    jinja2==2.11.3,
    click==7.1.2,
    itsdangerous==1.1.0,
    markupsafe==1.1.1,
    flask-socketio==4.3.2,
    python-socketio==4.6.1,
    python-engineio==3.14.2,
    eventlet==0.33.3,
    greenlet==2.0.2,
    dnspython,
    telethon==1.29.0,
    pyaes,
    rsa,
    pyasn1,
    requests,
    certifi,
    urllib3,
    charset-normalizer,
    idna,
    beautifulsoup4,
    typing_extensions,
    android,
    jnius

# Assets (optional – place a 512x512 PNG as static/icon.png and
# a 720x1280 PNG as static/presplash.png to use custom branding)
# presplash.filename = %(source.dir)s/static/presplash.png
# icon.filename      = %(source.dir)s/static/icon.png

# Orientation and display
orientation = portrait
fullscreen = 0

# Android settings
android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.private_storage = True
android.accept_sdk_license = True
android.archs = arm64-v8a, armeabi-v7a

# Android 9+ blocks HTTP (cleartext) traffic by default, including to localhost.
# The app runs a local Flask server on http://127.0.0.1:3005 and opens it in a
# WebView, so cleartext traffic must be explicitly permitted for the loopback
# interface.
#
# NOTE: android.extra_manifest_application_arguments is broken in buildozer
# 1.5.0 (https://github.com/kivy/buildozer/issues/1611) — it wraps the file
# content in double-quotes, producing invalid XML.  Instead, the CI workflow
# patches AndroidManifest.xml directly via sed after the dist is created.
# android.extra_manifest_application_arguments = %(source.dir)s/extra_manifest_application.txt

# greenlet 2.x (required for Python 3.11) is a C++ extension that links
# against libc++_shared.so (the NDK shared C++ STL).  Android does not
# ship this as a system library; it must be bundled inside the APK.
# The workflow copies these from the runner's pre-installed NDK before
# the build (see step "Bundle libc++_shared.so for greenlet").
android.add_libs_armeabi_v7a = libs/armeabi-v7a/libc++_shared.so
android.add_libs_arm64_v8a = libs/arm64-v8a/libc++_shared.so

# Debug signing – use a stable keystore committed to the repo so that
# successive APK builds can be installed as updates (same cert = same app).
# This is a debug key only — no security concern.
android.debug_keystore = debug.keystore
android.debug_keystore_alias = androiddebugkey
android.debug_keystore_passwd = android

# Release/debug artifacts
android.debug_artifact = apk
android.release_artifact = aab

# Java entry point (standard Kivy)
# android.entrypoint = org.kivy.android.PythonActivity

# Enable AndroidX
android.enable_androidx = True

# Gradle extra source dirs (none needed)
# android.gradle_dependencies =

# logcat filters
android.logcat_filters = *:S python:D

[buildozer]

# Log level: 0=error, 1=info, 2=debug
log_level = 2

# Warn on mismatch
warn_on_root = 1
