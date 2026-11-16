"""The --serve web server: page, refresh endpoint and its guards. No ESPN calls."""

import http.client
import json
import threading
import time

import pytest

from fantasy_dash.espn_source import EspnError
from fantasy_dash.server import TOKEN_HEADER, DashboardServer


@pytest.fixture
def running():
    calls = []

    def render(token):
        return f"<html>token={token}</html>"

    def refresh():
        calls.append(time.time())
        if getattr(refresh, "fail", False):
            raise EspnError("ESPN refused access while loading the league.")
        if getattr(refresh, "slow", False):
            time.sleep(0.5)
        return "2026-11-17T09:00:00+13:00"

    server = DashboardServer(render, refresh, port=0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server, refresh, calls
    server.shutdown()
    server.server_close()


def request(server, method, path, headers=None, host=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{port}")
    for key, value in (headers or {}).items():
        conn.putheader(key, value)
    conn.putheader("Content-Length", "0")
    conn.endheaders()
    response = conn.getresponse()
    body = response.read().decode()
    conn.close()
    return response.status, body


def test_page_is_served_with_the_token(running):
    server, _, _ = running
    status, body = request(server, "GET", "/")
    assert status == 200 and server.token in body


def test_other_host_names_are_refused(running):
    server, _, _ = running
    assert request(server, "GET", "/", host="evil.example:80")[0] == 403
    assert request(server, "POST", "/refresh", {TOKEN_HEADER: server.token}, host="evil.example")[0] == 403


def test_refresh_needs_the_token(running):
    server, _, calls = running
    assert request(server, "POST", "/refresh")[0] == 403
    assert request(server, "POST", "/refresh", {TOKEN_HEADER: "wrong"})[0] == 403
    assert calls == []
    status, body = request(server, "POST", "/refresh", {TOKEN_HEADER: server.token})
    assert status == 200 and json.loads(body) == {"ok": True, "generated_at": "2026-11-17T09:00:00+13:00"}
    assert len(calls) == 1


def test_refresh_errors_come_back_as_messages(running):
    server, refresh, _ = running
    refresh.fail = True
    status, body = request(server, "POST", "/refresh", {TOKEN_HEADER: server.token})
    assert status == 500 and "ESPN refused access" in json.loads(body)["error"]


def test_only_one_refresh_runs_at_a_time(running):
    server, refresh, _ = running
    refresh.slow = True
    first = threading.Thread(target=request, args=(server, "POST", "/refresh", {TOKEN_HEADER: server.token}))
    first.start()
    time.sleep(0.1)
    assert request(server, "POST", "/refresh", {TOKEN_HEADER: server.token})[0] == 409
    first.join()
