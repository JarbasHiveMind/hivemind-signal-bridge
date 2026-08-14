"""Unit tests: construct the bridge offline and drive it with mocks.

No live signal-cli daemon or HiveMind connection is made. A pre-built
mock SignalRPCClient and a pre-built mock HiveMessageBusClient are
injected so the bridge's __init__ never touches a socket or the network.
"""
from unittest.mock import MagicMock

import pytest


def _make_bridge(**kwargs):
    from hivemind_signal_bridge import HiveMindSignalBridge

    fake_client = MagicMock(name="HiveMessageBusClient")
    fake_signal = MagicMock(name="SignalRPCClient")
    fake_signal.on_notification = None

    bridge = HiveMindSignalBridge(
        signal_account="+15550000000",
        client=fake_client, signal_client=fake_signal, **kwargs
    )
    return bridge, fake_client, fake_signal


def _fake_params(text="hello world", source="+15551111111"):
    return {"envelope": {"sourceNumber": source,
                         "dataMessage": {"message": text}}}


def test_import_package_and_version():
    import hivemind_signal_bridge
    from hivemind_signal_bridge.version import __version__

    assert isinstance(__version__, str)
    assert __version__
    assert hivemind_signal_bridge.platform.startswith("HiveMindSignalBridge")


def test_construct_bridge_without_connecting():
    bridge, fake_client, fake_signal = _make_bridge()
    assert bridge._started is False
    fake_client.connect.assert_not_called()
    fake_signal.connect.assert_not_called()


def test_signal_endpoint_required_without_injected_client():
    from hivemind_signal_bridge import HiveMindSignalBridge

    with pytest.raises(ValueError):
        HiveMindSignalBridge(signal_account="+1555", client=MagicMock())


def test_connect_hivemind_calls_connect_once_and_registers_handlers():
    """connect_hivemind() must call connect() exactly once, never run_forever()."""
    bridge, fake_client, fake_signal = _make_bridge()
    bridge.connect_hivemind()

    fake_client.connect.assert_called_once_with(
        site_id="signal", handshake_max_retries=10)
    fake_client.run_forever.assert_not_called()
    assert bridge._connected.is_set()
    registered = {call.args[0] for call in fake_client.on_mycroft.call_args_list}
    assert registered == {"speak", "hive.complete_intent_failure"}


def test_connect_hivemind_bounds_handshake_retries():
    """connect() must never be called with an unbounded (None) handshake
    retry count -- that hangs the bridge forever against a stalled hub."""
    from hivemind_signal_bridge import DEFAULT_HANDSHAKE_MAX_RETRIES

    bridge, fake_client, fake_signal = _make_bridge()
    bridge.connect_hivemind()

    _, kwargs = fake_client.connect.call_args
    assert kwargs.get("handshake_max_retries") is not None
    assert kwargs["handshake_max_retries"] == DEFAULT_HANDSHAKE_MAX_RETRIES == 10


def test_connect_hivemind_respects_custom_handshake_max_retries():
    bridge, fake_client, fake_signal = _make_bridge(handshake_max_retries=3)
    bridge.connect_hivemind()

    fake_client.connect.assert_called_once_with(
        site_id="signal", handshake_max_retries=3)


def test_inbound_message_forwarded_to_hivemind_after_connect():
    from hivemind_bus_client import HiveMessage, HiveMessageType

    bridge, fake_client, fake_signal = _make_bridge()
    bridge.connect_hivemind()

    bridge._on_signal_notification(_fake_params(text="turn on the lights",
                                                source="+15551111111"))

    fake_client.emit.assert_called_once()
    sent = fake_client.emit.call_args[0][0]
    assert isinstance(sent, HiveMessage)
    assert sent.msg_type == HiveMessageType.BUS
    payload = sent.payload
    assert payload.msg_type == "recognizer_loop:utterance"
    assert payload.data["utterances"] == ["turn on the lights"]
    assert payload.context["signal_sender"] == "+15551111111"
    assert payload.context["user"]["signal_number"] == "+15551111111"
    assert payload.context["session"]["session_id"] == "signal-+15551111111"


def test_no_forward_before_hivemind_connected():
    """Messages arriving before connect_hivemind() must be dropped, not queued."""
    bridge, fake_client, fake_signal = _make_bridge()
    # deliberately not calling bridge.connect_hivemind()

    bridge._on_signal_notification(_fake_params())

    fake_client.emit.assert_not_called()


def test_own_account_message_is_skipped():
    bridge, fake_client, fake_signal = _make_bridge()
    bridge.connect_hivemind()

    bridge._on_signal_notification(_fake_params(source="+15550000000"))  # own account

    fake_client.emit.assert_not_called()


def test_non_text_message_is_ignored():
    bridge, fake_client, fake_signal = _make_bridge()
    bridge.connect_hivemind()

    params = {"envelope": {"sourceNumber": "+15551111111", "dataMessage": {}}}
    bridge._on_signal_notification(params)

    fake_client.emit.assert_not_called()


def test_disallowed_sender_is_ignored():
    bridge, fake_client, fake_signal = _make_bridge(allowed_senders=["+15552222222"])
    bridge.connect_hivemind()

    bridge._on_signal_notification(_fake_params(source="+15559999999"))

    fake_client.emit.assert_not_called()


def test_allowed_sender_is_forwarded():
    bridge, fake_client, fake_signal = _make_bridge(allowed_senders=["+15552222222"])
    bridge.connect_hivemind()

    bridge._on_signal_notification(_fake_params(source="+15552222222"))

    fake_client.emit.assert_called_once()


def test_speak_sends_text_back_to_sender():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_signal = _make_bridge()
    msg = Message("speak", {"utterance": "hi there"}, {"signal_sender": "+15551111111"})
    bridge.handle_speak(msg)

    fake_signal.send_text.assert_called_once_with("+15551111111", "hi there")


def test_speak_with_no_sender_is_ignored():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_signal = _make_bridge()
    msg = Message("speak", {"utterance": "hi"}, {})
    bridge.handle_speak(msg)

    fake_signal.send_text.assert_not_called()


def test_intent_failure_speaks_fallback():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_signal = _make_bridge()
    msg = Message("hive.complete_intent_failure", {}, {"signal_sender": "+15551111111"})
    bridge.handle_intent_failure(msg)

    fake_signal.send_text.assert_called_once()
    args, _ = fake_signal.send_text.call_args
    assert args[0] == "+15551111111"
    assert "don't know" in args[1]


def test_start_connects_hivemind_then_signal(monkeypatch):
    bridge, fake_client, fake_signal = _make_bridge()
    bridge._stop_event.set()  # make the wait loop exit immediately

    bridge.start()

    fake_client.connect.assert_called_once()
    fake_signal.connect.assert_called_once()
