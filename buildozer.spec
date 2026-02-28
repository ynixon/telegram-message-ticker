[app]

# App metadata
title = Telegram Message Ticker
package.name = telegramticker
package.domain = com.ynixon

# Source
source.dir = .
source.include_exts = py,png,jpg,jpeg,gif,kv,atlas,json,css,js,html,txt
source.include_patterns = templates/*,static/*,translations/*,*.json,*.example

# Version
version = 1.0

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
    eventlet,
    greenlet,
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
    lxml,
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
android.ndk_api = 21
android.private_storage = True
android.accept_sdk_license = True
android.archs = arm64-v8a, armeabi-v7a

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
