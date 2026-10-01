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


# --- Final review (LLM security) F9: DNS rebinding and a wall-clock deadline -------------

def _no_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


def test_dns_rebinding_to_localhost_is_refused_on_connect(monkeypatch):
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b"INTERNAL-ONLY Vitamin C 80 mg Zinc 10 mg Supplement Facts serving size 1"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real_getaddrinfo = socket.getaddrinfo
    lookups = []

    def rebinding_getaddrinfo(host, port_, *args, **kwargs):
        if host != "rebind.attacker.example":
            return real_getaddrinfo(host, port_, *args, **kwargs)
        lookups.append(host)
        ip = "93.184.215.14" if len(lookups) == 1 else "127.0.0.1"  # TTL-0 rebinding
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port_))]

    _no_proxy(monkeypatch)
    monkeypatch.setattr(bb.socket, "getaddrinfo", rebinding_getaddrinfo)
    try:
        # Sanity: the local server is reachable without the guard ...
        assert b"INTERNAL-ONLY" in bb._HTTP_SESSION.get(f"http://127.0.0.1:{port}/", timeout=5, proxies={}).content
        # ... but the rebinding host never gets it through the guarded fetch.
        assert bb.fetch_clean_page_text(f"http://rebind.attacker.example:{port}/") == ""
        assert len(lookups) >= 2  # the vetting lookup passed; the connect-time one was caught
    finally:
        server.shutdown()


class _FakeSock:
    def __init__(self, ip):
        self.ip = ip
        self.closed = False

    def getpeername(self):
        return (self.ip, 80)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("ip, public", [("93.184.215.14", True), ("127.0.0.1", False), ("169.254.169.254", False),
                                         ("10.0.0.5", False), ("::1", False), ("2606:4700::1111", True)])
def test_connected_peer_must_be_public(ip, public):
    sock = _FakeSock(ip)
    if public:
        bb._assert_public_peer(sock)
        assert not sock.closed
    else:
        with pytest.raises(bb.NonPublicAddressError):
            bb._assert_public_peer(sock)
        assert sock.closed


def test_page_fetch_has_a_wall_clock_deadline(monkeypatch):
    import threading
    import time as _time

    _resolve_to(monkeypatch, {"slow.test": "93.184.216.34"})
    monkeypatch.setattr(bb, "_PAGE_FETCH_DEADLINE_S", 0.4)

    class BlockingResp(FakeResp):
        """A slow-drip body: the read blocks until the response is closed."""

        def __init__(self):
            super().__init__(200, {"content-type": "text/html"})
            self._closed = threading.Event()

        def iter_content(self, chunk_size=1):
            yield b"<html>Vitamin C"
            if not self._closed.wait(10):
                yield b" 80 mg</html>"
            raise ConnectionError("closed")

        def close(self):
            self._closed.set()

    monkeypatch.setattr(bb, "_http_get", lambda url, **k: BlockingResp())
    started = _time.monotonic()
    assert bb._safe_public_get("https://slow.test/p") is None
    assert _time.monotonic() - started < 3.0


def test_page_fetch_uses_the_guarded_session(monkeypatch):
    _resolve_to(monkeypatch, {"shop.test": "93.184.216.34"})
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs)
        return FakeResp(200, {"content-type": "text/html"}, b"<p>Vitamin C 90 mg</p>")

    monkeypatch.setattr(bb, "_http_get", fake_get)
    assert "Vitamin C 90 mg" in bb.fetch_clean_page_text("https://shop.test/p")
    assert seen["session"] is bb._PUBLIC_FETCH_SESSION and seen["allow_redirects"] is False
    adapter = bb._PUBLIC_FETCH_SESSION.get_adapter("https://shop.test/")
    assert isinstance(adapter, bb._PublicOnlyAdapter)
