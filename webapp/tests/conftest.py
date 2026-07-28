import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import analytics
import feedback
import app as app_module

# Tests exercise the same after_request traffic logger as production --
# pointed at a throwaway file instead of the real analytics.db so running
# the suite (locally or via a deploy's post-sync test run) never pollutes
# the live traffic dashboard with fake request rows.
analytics.DB_PATH = os.path.join(tempfile.gettempdir(), "degen_test_analytics.db")
analytics.init_db()

# Same reasoning for the feedback form -- never let test submissions land
# in the real feedback.db an admin actually reads.
feedback.DB_PATH = os.path.join(tempfile.gettempdir(), "degen_test_feedback.db")
feedback.init_db()


@pytest.fixture(scope="session")
def flask_app():
    app_module.app.config.update(TESTING=True)
    return app_module.app


@pytest.fixture()
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture(params=list(app_module.LEAGUES.keys()))
def league_key(request):
    """Parametrized fixture -- tests using this run once per league."""
    return request.param
