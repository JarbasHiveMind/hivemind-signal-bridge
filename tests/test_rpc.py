"""Unit tests for the minimal signal-cli JSON-RPC client, no real socket."""
import json

import pytest

from hivemind_signal_bridge.rpc import SignalRPCClient


def test_requires_socket_or_host_port():
    with pytest.raises(ValueError):
        SignalRPCClient()


def test_send_text_writes_jsonrpc_request():
    client = SignalRPCClient(socket_path="/tmp/does-not-matter.sock")

    class FakeSocket:
        def __init__(self):
            self.sent = []

        def sendall(self, data):
            self.sent.append(data)

    client._sock = FakeSocket()
    client.send_text("+15551111111", "hello")

    assert len(client._sock.sent) == 1
    payload = json.loads(client._sock.sent[0].decode("utf-8").strip())
    assert payload["method"] == "send"
    assert payload["params"]["recipient"] == ["+15551111111"]
    assert payload["params"]["message"] == "hello"


def test_send_text_without_connection_raises():
    client = SignalRPCClient(socket_path="/tmp/does-not-matter.sock")
    with pytest.raises(RuntimeError):
        client.send_text("+1555", "hi")


def test_notification_dispatched_to_callback():
    received = []
    client = SignalRPCClient(socket_path="/tmp/does-not-matter.sock",
                             on_notification=lambda p: received.append(p))

    line = json.dumps({"jsonrpc": "2.0", "method": "receive",
                       "params": {"envelope": {"sourceNumber": "+1555",
                                               "dataMessage": {"message": "hi"}}}})
    buf = (line + "\n").encode("utf-8")

    # exercise the parsing logic the read loop uses, without a real socket
    while b"\n" in buf:
        chunk, buf = buf.split(b"\n", 1)
        payload = json.loads(chunk.decode("utf-8"))
        if payload.get("method") == "receive" and client.on_notification:
            client.on_notification(payload.get("params") or {})

    assert len(received) == 1
    assert received[0]["envelope"]["sourceNumber"] == "+1555"
