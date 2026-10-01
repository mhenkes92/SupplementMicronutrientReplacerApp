"""The server-side product-page fetch must never reach internal addresses."""
from __future__ import annotations

import socket

import pytest

import blockbrain.app as bb


def _resolve_to(monkeypatch, mapping):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        ip = mapping[host]
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(bb.socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize(
    "url,ip",
    [
        ("http://metadata.test/latest/meta-data/", "169.254.169.254"),
        ("http://localhost.test:8501/", "127.0.0.1"),
        ("http://intranet.test/", "10.1.2.3"),
        ("http://v6.test/", "::1"),
    ],
)
def test_internal_addresses_are_refused(monkeypatch, url, ip):
    _resolve_to(monkeypatch, {url.split("//")[1].split("/")[0].split(":")[0]: ip})
    monkeypatch.setattr(bb, "_http_get", lambda *a, **k: pytest.fail("must not fetch"))
    assert bb._is_public_http_url(url) is False
    assert bb.fetch_clean_page_text(url) == ""


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", ""])
def test_non_http_schemes_are_refused(url):
    assert bb._is_public_http_url(url) is False


class FakeResp:
    def __init__(self, status, headers=None, body=b""):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.encoding = "utf-8"

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    def iter_content(self, chunk_size=1):
        yield self._body

    def close(self):
        pass


def test_redirect_to_internal_address_is_refused(monkeypatch):
    _resolve_to(monkeypatch, {"shop.test": "93.184.216.34", "evil.test": "169.254.169.254"})
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        assert kwargs.get("allow_redirects") is False
        return FakeResp(302, {"Location": "http://evil.test/latest/meta-data/"})

    monkeypatch.setattr(bb, "_http_get", fake_get)
    assert bb.fetch_clean_page_text("https://shop.test/product") == ""
    assert calls == ["https://shop.test/product"]


def test_public_page_is_fetched_and_capped(monkeypatch):
    _resolve_to(monkeypatch, {"shop.test": "93.184.216.34"})
    html = b"<html><body><h1>Supplement Facts</h1><p>Vitamin C 90 mg</p><script>x()</script></body></html>"
    monkeypatch.setattr(bb, "_http_get", lambda url, **k: FakeResp(200, {"content-TYPE": "text/html; charset=utf-8"}, html))
    text = bb.fetch_clean_page_text("https://shop.test/product")
    assert "Vitamin C 90 mg" in text and "x()" not in text
