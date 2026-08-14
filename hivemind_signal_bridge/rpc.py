"""Minimal JSON-RPC client for a running ``signal-cli`` daemon.

``signal-cli`` is a separate, already-maintained implementation of the
Signal protocol; this module does not reimplement any of it. It only
speaks JSON-RPC (newline-delimited JSON objects) to a daemon already
running as ``signal-cli -a +NUMBER daemon --socket <path>`` (unix socket)
or ``--tcp <host>:<port>`` (TCP). Incoming messages arrive as JSON-RPC
notifications with method ``"receive"`` pushed unprompted on the same
connection; outgoing messages are plain JSON-RPC requests with method
``"send"``.
"""
import json
import socket
import threading
from typing import Callable, Optional

from ovos_utils.log import LOG


class SignalRPCClient:
    """A tiny newline-delimited JSON-RPC client over a socket.

    Connects to a ``signal-cli`` JSON-RPC daemon, reads notifications
    (and responses) on a background thread, and lets callers send
    requests. This is intentionally minimal: it does not track
    request/response ids beyond fire-and-forget, since the bridge only
    needs to call ``send`` and does not act on the send confirmation.
    """

    def __init__(self, socket_path: Optional[str] = None,
                 host: Optional[str] = None, port: Optional[int] = None,
                 on_notification: Optional[Callable[[dict], None]] = None):
        if not socket_path and not (host and port):
            raise ValueError("either socket_path or host+port is required")
        self.socket_path = socket_path
        self.host = host
        self.port = port
        self.on_notification = on_notification
        self._sock: Optional[socket.socket] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._id = 0
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self.socket_path:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self.socket_path)
        else:
            sock = socket.create_connection((self.host, self.port))
        self._sock = sock
        self._stop.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        LOG.info("== connected to signal-cli JSON-RPC daemon")

    def close(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None

    def _read_loop(self) -> None:
        buf = b""
        while not self._stop.is_set() and self._sock is not None:
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    LOG.exception("bad JSON-RPC line from signal-cli")
                    continue
                if payload.get("method") == "receive" and self.on_notification:
                    self.on_notification(payload.get("params") or {})

    def send_text(self, recipient: str, message: str) -> None:
        """Send a text message to ``recipient`` (a phone number or group id)."""
        with self._lock:
            self._id += 1
            req_id = self._id
        request = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": {"recipient": [recipient], "message": message},
            "id": req_id,
        }
        self._write(request)

    def _write(self, request: dict) -> None:
        if self._sock is None:
            raise RuntimeError("not connected to signal-cli daemon")
        data = (json.dumps(request) + "\n").encode("utf-8")
        self._sock.sendall(data)


__all__ = ["SignalRPCClient"]
