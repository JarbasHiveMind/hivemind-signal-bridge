"""HiveMind <-> Signal bridge.

A HiveMind bridge is a satellite whose input and output are a chat
platform instead of a microphone. This one uses
:class:`~hivemind_bus_client.HiveMessageBusClient` directly (same shape
as ``hivemind-telegram-bridge``'s ``HiveMindTelegramBridge``) and a
running ``signal-cli`` JSON-RPC daemon
(:class:`hivemind_signal_bridge.rpc.SignalRPCClient`) for the Signal
side. This bridge does not reimplement the Signal protocol -- it talks
to ``signal-cli``, which does.

Connection lifecycle, spelled out because getting it wrong is the
recurring bug across every HiveMind bridge written so far:

- ``HiveMessageBusClient.connect()`` already starts and owns the
  reconnect worker in a background thread, and blocks synchronously
  until the handshake completes (or fails). Call it exactly once, from
  ``start()``. Do not also call ``run_forever()`` afterwards -- there is
  nothing left to start, the connection is already live in its own
  thread for as long as the process runs.
- Nothing is forwarded to HiveMind before ``connect()`` returns, and the
  bridge's own outgoing sends never get echoed back to itself by
  signal-cli, so no feedback loop reaches the hub.
- host/port are configuration, not constants (``ws://127.0.0.1:5678``
  is only ever a default value).
- a freshly registered HiveMind client is denied every message type
  until a hub admin runs ``hivemind-core allow-msg
  recognizer_loop:utterance <client_id>`` (and usually ``speak`` too);
  this bridge cannot do that step itself. See the README.
"""
import threading
import time
from typing import Optional

from hivemind_bus_client import (
    HiveMessage,
    HiveMessageType,
    HiveMessageBusClient,
)
from ovos_bus_client.message import Message
from ovos_utils.log import LOG

from hivemind_signal_bridge.rpc import SignalRPCClient

platform = "HiveMindSignalBridgeV0.1"


class HiveMindSignalBridge:
    """Bridge a signal-cli JSON-RPC daemon to a HiveMind node."""

    def __init__(self,
                 signal_account: Optional[str] = None,
                 signal_socket: Optional[str] = None,
                 signal_host: Optional[str] = None,
                 signal_port: Optional[int] = None,
                 key: Optional[str] = None,
                 password: Optional[str] = None,
                 host: Optional[str] = None,
                 port: int = 5678,
                 self_signed: bool = False,
                 lang: str = "en-us",
                 site_id: str = "signal",
                 allowed_senders: Optional[list] = None,
                 *,
                 client: Optional[HiveMessageBusClient] = None,
                 signal_client: Optional[SignalRPCClient] = None):
        """
        Parameters
        ----------
        signal_account: the registered Signal phone number this bridge
            answers as, e.g. ``+15551234567``. Required (for logging /
            self-message filtering) unless ``signal_client`` is injected.
        signal_socket: path to the signal-cli JSON-RPC unix socket.
        signal_host, signal_port: alternative to ``signal_socket`` -- a
            TCP JSON-RPC endpoint (``signal-cli ... daemon --tcp``).
        key, password, host, port, self_signed: HiveMind hub connection.
        lang: default utterance language tag.
        site_id: this bridge's HiveMind site id.
        allowed_senders: if given, only messages from these Signal phone
            numbers are forwarded; leave unset to accept anyone who can
            message the account.
        client: pre-built HiveMessageBusClient (tests / advanced setups).
        signal_client: pre-built SignalRPCClient (tests).
        """
        if signal_client is None and not (signal_socket or (signal_host and signal_port)):
            raise ValueError("signal_socket or signal_host+signal_port is required "
                             "unless a SignalRPCClient is injected")

        self.signal_account = signal_account
        self.lang = lang
        self.site_id = site_id
        self.allowed_senders = set(allowed_senders) if allowed_senders else None

        self.signal = signal_client or SignalRPCClient(
            socket_path=signal_socket, host=signal_host, port=signal_port,
            on_notification=self._on_signal_notification,
        )
        if signal_client is not None:
            # tests may inject a client without on_notification wired;
            # make sure our handler is the one invoked either way.
            self.signal.on_notification = self._on_signal_notification

        self.client = client or HiveMessageBusClient(
            key=key,
            password=password,
            host=host,
            port=port,
            useragent=platform,
            self_signed=self_signed,
        )

        self._started = False
        self._connected = threading.Event()
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def connect_hivemind(self) -> None:
        """Connect to the HiveMind hub and wait for the handshake.

        Calls ``HiveMessageBusClient.connect()`` exactly once; that call
        already starts and owns the reconnect worker thread. Never call
        ``run_forever()`` in addition to this.
        """
        self.client.connect(site_id=self.site_id)
        self.client.on_mycroft("speak", self.handle_speak)
        self.client.on_mycroft("hive.complete_intent_failure",
                               self.handle_intent_failure)
        self._connected.set()
        LOG.info("== connected to HiveMind")

    def start(self) -> None:
        """Connect to HiveMind and signal-cli, then block until stopped.

        Intended to be the last call in a ``__main__``.
        """
        if self._started:
            return
        self._started = True
        self.connect_hivemind()
        LOG.warning(
            "a freshly registered HiveMind client is denied every message "
            "type until an admin runs `hivemind-core allow-msg "
            "recognizer_loop:utterance <client_id>` on the hub (and "
            "usually `speak` too). If messages seem to vanish silently, "
            "check that first."
        )
        self.signal.connect()
        LOG.info("== listening to Signal")
        try:
            while not self._stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    def stop(self) -> None:
        """Stop listening to signal-cli and close the HiveMind connection."""
        self._stop_event.set()
        try:
            self.signal.close()
        except Exception:
            LOG.exception("error closing signal-cli connection")
        try:
            self.client.close()
        except Exception:
            LOG.exception("error closing HiveMind client")
        self._started = False
        self._connected.clear()

    # ------------------------------------------------------------------
    # Signal -> HiveMind
    # ------------------------------------------------------------------
    def _on_signal_notification(self, params: dict) -> None:
        """Handle a ``receive`` JSON-RPC notification pushed by signal-cli.

        Drops the message instead of forwarding when: there is no text
        body, it came from this bridge's own account (would otherwise
        create a feedback loop), it is outside ``allowed_senders`` (when
        configured), or HiveMind has not completed its handshake yet
        (forwarding before that point gets the connection killed by the
        hub).
        """
        envelope = (params or {}).get("envelope") or {}
        source = envelope.get("sourceNumber") or envelope.get("source")
        data_message = envelope.get("dataMessage") or {}
        text = data_message.get("message")

        if not text or not source:
            return
        if self.signal_account and source == self.signal_account:
            return
        if self.allowed_senders is not None and source not in self.allowed_senders:
            LOG.debug(f"ignoring message from non-allowed sender {source}")
            return
        if not self._connected.is_set():
            LOG.warning("dropping Signal message, not connected to "
                       "HiveMind yet")
            return

        self.forward_to_hivemind(text, source)

    def forward_to_hivemind(self, text: str, sender: str) -> None:
        msg = Message(
            "recognizer_loop:utterance",
            {"utterances": [text], "lang": self.lang},
            {
                "source": platform,
                "destination": "HiveMind",
                "platform": platform,
                "signal_sender": sender,
                "user": {"signal_number": sender},
                "session": {"session_id": f"signal-{sender}"},
            },
        )
        self.client.emit(HiveMessage(HiveMessageType.BUS, msg))

    # ------------------------------------------------------------------
    # HiveMind -> Signal
    # ------------------------------------------------------------------
    def handle_speak(self, message: Message) -> None:
        sender = message.context.get("signal_sender")
        if sender is None:
            return
        utterance = message.data.get("utterance")
        if not utterance:
            return
        self.speak(utterance, sender)

    def handle_intent_failure(self, message: Message) -> None:
        sender = message.context.get("signal_sender")
        if sender is None:
            return
        LOG.error("complete intent failure")
        self.speak("I don't know how to answer that", sender)

    def speak(self, text: str, sender: str) -> None:
        """Post ``text`` back to ``sender`` over Signal."""
        LOG.debug(f"Sending message to Signal recipient {sender}: {text}")
        try:
            self.signal.send_text(sender, text)
        except Exception:
            LOG.exception(f"failed to send Signal message to {sender}")


__all__ = ["HiveMindSignalBridge", "platform"]
