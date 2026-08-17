"""Shared fixtures for the QA suite (isolated from production SQLite)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import analytics
import feedback
import app as app_module

analytics.DB_PATH = os.path.join(tempfile.gettempdir(), "degen_qa_analytics.db")
analytics.init_db()
feedback.DB_PATH = os.path.join(tempfile.gettempdir(), "degen_qa_feedback.db")
feedback.init_db()


@pytest.fixture(scope="session")
def flask_app():
    app_module.app.config.update(TESTING=True)
    return app_module.app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()
