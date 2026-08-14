"""CLI entry point for the HiveMind <-> Signal bridge.

HiveMind identity (key/password/host/port) defaults to the values stored
by ``hivemind-client set-identity``; flags override them.
"""
import click
from ovos_utils.log import LOG

from hivemind_signal_bridge import HiveMindSignalBridge


def connect_signal_to_hivemind(signal_account, signal_socket=None,
                               signal_host=None, signal_port=None,
                               key=None, password=None, host=None, port=5678,
                               self_signed=False, lang="en-us", site_id="signal",
                               allowed_senders=None):
    bridge = HiveMindSignalBridge(
        signal_account=signal_account, signal_socket=signal_socket,
        signal_host=signal_host, signal_port=signal_port,
        key=key, password=password, host=host, port=port,
        self_signed=self_signed, lang=lang, site_id=site_id,
        allowed_senders=allowed_senders,
    )
    bridge.start()
    return bridge


@click.command()
@click.option("--signal-account", required=True,
             help="the registered Signal phone number this bridge answers "
                  "as, e.g. +15551234567")
@click.option("--signal-socket", default=None,
             help="path to the signal-cli JSON-RPC unix socket, e.g. "
                  "/var/run/signal-cli/socket. Mutually exclusive with "
                  "--signal-host/--signal-port")
@click.option("--signal-host", default=None,
             help="signal-cli JSON-RPC TCP host, if running with `daemon --tcp`")
@click.option("--signal-port", type=int, default=None,
             help="signal-cli JSON-RPC TCP port")
@click.option("--allowed-sender", "allowed_senders", multiple=True,
             help="Signal phone number allowed to talk to the bridge "
                  "(repeatable); default: anyone who can message the account")
@click.option("--access-key", "key", default=None,
             help="HiveMind access key (default: from identity file)")
@click.option("--password", default=None,
             help="HiveMind password (default: from identity file)")
@click.option("--host", default=None,
             help="HiveMind host, e.g. ws://127.0.0.1 (default: from identity file)")
@click.option("--port", type=int, default=5678, help="HiveMind port (default: 5678)")
@click.option("--site-id", default="signal",
             help="this bridge's HiveMind site id (default: signal)")
@click.option("--self-signed", is_flag=True, help="accept self-signed SSL certificates")
@click.option("--lang", default="en-us", help="utterance language")
def main(signal_account, signal_socket, signal_host, signal_port,
        allowed_senders, key, password, host, port, site_id, self_signed, lang):
    """Bridge a signal-cli JSON-RPC daemon to a HiveMind node."""
    if not signal_socket and not (signal_host and signal_port):
        raise click.UsageError("pass --signal-socket or both --signal-host and --signal-port")

    if host and not host.startswith("ws://") and not host.startswith("wss://"):
        host = "ws://" + host

    LOG.info("bridge starting; press Ctrl-C to stop")
    try:
        connect_signal_to_hivemind(
            signal_account=signal_account, signal_socket=signal_socket,
            signal_host=signal_host, signal_port=signal_port,
            key=key, password=password, host=host, port=port,
            self_signed=self_signed, lang=lang, site_id=site_id,
            allowed_senders=list(allowed_senders) or None,
        )
    except KeyboardInterrupt:
        LOG.info("shutting down")


if __name__ == '__main__':
    main()
