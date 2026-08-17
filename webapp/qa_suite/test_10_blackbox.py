"""Black-box tests — treat the site as an opaque HTTP service."""
import pytest

from qa_suite.catalog import PUBLIC_SMOKE_PATHS


pytestmark = pytest.mark.blackbox


PUBLIC_MUST_BE_200 = (
    "/",
    "/sports",
    "/todays-picks",
    "/faq",
    "/terms",
    "/privacy",
    "/accuracy",
    "/robots.txt",
    "/sitemap.xml",
)


@pytest.mark.parametrize("path", PUBLIC_MUST_BE_200)
def test_anonymous_get_succeeds(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert len(resp.data) > 20


def test_unknown_url_is_client_error_not_500(client):
    resp = client.get("/this-page-does-not-exist-xyz")
    assert resp.status_code in (404, 301, 302, 308)
    assert resp.status_code != 500


def test_html_pages_declare_charset_and_brand(client):
    resp = client.get("/sports")
    body = resp.data.decode().lower()
    assert "charset" in body
    assert "degen predictions" in body


def test_public_pages_are_not_admin_console(client):
    for path in ("/", "/sports", "/todays-picks", "/nfl/fixtures"):
        body = client.get(path).data.decode()
        assert "Admin Console" not in body
        assert "WWW-Authenticate" not in (client.get(path).headers.get("WWW-Authenticate") or "")


def test_methods_on_homepage(client):
    assert client.get("/").status_code == 200
    assert client.post("/").status_code in (405, 404, 400)
    assert client.put("/").status_code in (405, 404, 400)


def test_legal_pages_exist(client):
    terms = client.get("/terms").data.decode().lower()
    privacy = client.get("/privacy").data.decode().lower()
    assert "term" in terms
    assert "privacy" in privacy


@pytest.mark.parametrize("path", PUBLIC_SMOKE_PATHS[:8])
def test_no_server_error_banner(client, path):
    resp = client.get(path)
    if resp.status_code == 200 and "text/html" in (resp.content_type or ""):
        text = resp.data.decode()
        assert "Internal Server Error" not in text
        assert "Traceback (most recent call last)" not in text
