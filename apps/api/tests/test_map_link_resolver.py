"""Opening a short map link once (L1b). A real HTTP server on this machine
plays the short-link host: it redirects twice, and the resolver must follow
both hops, stop at the map link without requesting it, and refuse a hop to a
host that is not a map — proving the request really ran (rule 17b)."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from apps.api.billing.planning import map_links
from apps.api.services import map_link_resolver


class _Short(BaseHTTPRequestHandler):
    hits: list[str] = []
    target = ""

    def do_GET(self):  # noqa: N802
        type(self).hits.append(self.path)
        port = self.server.server_address[1]
        if self.path == "/abc":
            self.send_response(302)
            self.send_header("location", f"http://127.0.0.1:{port}/hop2")
        elif self.path == "/hop2":
            self.send_response(301)
            self.send_header("location", type(self).target)
        else:
            self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Short.hits = []
    srv = HTTPServer(("127.0.0.1", 0), _Short)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


def _resolve(srv, url_path="/abc"):
    return map_link_resolver.resolve(
        f"http://127.0.0.1:{srv.server_address[1]}{url_path}",
        short_hosts=frozenset({"127.0.0.1"}),
        map_hosts=map_links.MAP_HOSTS | {"127.0.0.1"},
    )


def test_two_hops_then_the_map_link_is_read_not_requested(server):
    _Short.target = "https://www.google.com/maps/place/AKH+Wien/@48.22,16.34,17z/data=!3d48.2209537!4d16.3477019"
    info = _resolve(server)
    assert (format(info.latitude, "f"), format(info.longitude, "f"), info.name) == ("48.220954", "16.347702", "AKH Wien")
    assert _Short.hits == ["/abc", "/hop2"]


def test_a_hop_to_another_host_is_refused(server):
    _Short.target = "https://evil.example/collect"
    with pytest.raises(map_link_resolver.ResolveError) as refused:
        _resolve(server)
    assert str(refused.value) == (
        "This short link leads somewhere other than Google Maps or Apple Maps. "
        "Open it in your browser and paste the full map link instead."
    )
    assert _Short.hits == ["/abc", "/hop2"]


def test_a_link_that_leads_nowhere(server):
    with pytest.raises(map_link_resolver.ResolveError):
        _resolve(server, "/nothing")
    assert _Short.hits == ["/nothing"]


def test_a_full_link_never_opens_anything(server):
    info = map_link_resolver.resolve("https://www.google.com/maps/@48.2082,16.3738,15z")
    assert format(info.latitude, "f") == "48.208200"
    assert _Short.hits == []


def test_nothing_unreachable_is_followed():
    with pytest.raises(map_link_resolver.ResolveError) as refused:
        map_link_resolver.resolve("http://127.0.0.1:9/abc", short_hosts=frozenset({"127.0.0.1"}))
    assert "could not be opened just now" in str(refused.value)
