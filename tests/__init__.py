"""Tests always use fake credentials and offline services."""

import os
import tempfile

_test_data = tempfile.TemporaryDirectory(prefix="accounting-tests-")
os.environ.update(
    TELEGRAM_TOKEN="123456:offline_test_token",
    SPREADSHEET_NAME="offline-test-sheet",
    GOOGLE_SERVICE_ACCOUNT_FILE="/nonexistent/offline-test-credentials.json",
    ALLOWED_USER_IDS="42",
    VOICE_INPUT_ENABLED="false",
    VOICE_DATA_DIR=_test_data.name,
)
