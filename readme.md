# HiveMind Signal Bridge

This bridges Signal messages to a HiveMind node. A HiveMind bridge is a
satellite whose input and output are a chat platform instead of a
microphone: messages sent to your Signal number become HiveMind
utterances, and the hub's spoken replies are sent back as Signal
messages to the same conversation.

This bridge does not talk to Signal's servers itself. It talks to
[`signal-cli`](https://github.com/AsamK/signal-cli), a separate,
actively maintained command-line Signal client, over its JSON-RPC
interface. `signal-cli` does all the protocol and encryption work;
this bridge only forwards text in and out of it.

## The one real blocker: you need a phone number

Signal requires every account to be a real, working phone number that
can receive an SMS (or a voice call) to verify. There is no way around
this — you cannot register a Signal bridge account without one. Options
people commonly use:

- A spare SIM card or an old phone number you still control.
- A number from a VoIP provider that supports SMS verification (not
  all of them do, and Signal actively blocks some VoIP ranges).
- **Do not** reuse the phone number of a Signal account you already use
  day-to-day — registering it here would move that account to this
  bridge and log you out everywhere else.

If you do not have a number you are willing to dedicate to this, stop
here — everything below assumes you do.

## Installing signal-cli

`signal-cli` needs a Java runtime (17+) and is not part of this
package. Follow the
[official install instructions](https://github.com/AsamK/signal-cli/blob/master/man/signal-cli.1.adoc)
for your platform, or use a pre-built native image if your platform has
one. Confirm it works:

```bash
signal-cli --version
```

## Registering your number

With `+15551234567` replaced by your real number, including the `+`
and country code:

```bash
signal-cli -a +15551234567 register
```

Signal texts a 6-digit code to that number within a minute or two. Once
it arrives:

```bash
signal-cli -a +15551234567 verify 123456
```

If Signal refuses to send an SMS (some VoIP numbers get flagged), retry
with `--voice` on the `register` command to get a phone call instead.

Send yourself a test message to confirm registration succeeded:

```bash
signal-cli -a +15551234567 send -m "hello from signal-cli" +15551234567
```

## Running signal-cli as a JSON-RPC daemon

This bridge expects `signal-cli` to already be running in the
background as a JSON-RPC daemon, not invoked per-message. Over a unix
socket (simplest on a single machine):

```bash
signal-cli -a +15551234567 daemon --socket /var/run/signal-cli/socket
```

or over TCP, if the bridge runs on a different host or in a different
container:

```bash
signal-cli -a +15551234567 daemon --tcp 127.0.0.1:7583
```

Leave this running (as a systemd unit, a supervisor process, or a
sidecar container — see `docker-compose.yml`). This bridge connects to
it, it does not start it.

## Registering the bridge on the HiveMind hub

Every HiveMind client needs credentials and, separately, permission to
send the message types it uses. On the machine running `hivemind-core`:

```bash
hivemind-core add-client
```

This prints an access key and password; pass them to the bridge as
`--access-key` / `--password` (or store them once with
`hivemind-client set-identity` and omit the flags).

A freshly added client is denied every message type by default. The
bridge needs at least:

```bash
hivemind-core allow-msg recognizer_loop:utterance <client_id>
hivemind-core allow-msg speak <client_id>
```

`<client_id>` is printed by `add-client` (and by `hivemind-core
list-clients` afterwards). Skipping this step is the single most common
reason a bridge "connects fine" but nothing ever seems to happen: the hub
silently drops every message the client sends until it is whitelisted.

## Running the bridge

```bash
pip install .
hivemind-signal-bridge \
  --signal-account +15551234567 \
  --signal-socket /var/run/signal-cli/socket \
  --access-key <key> --password <password> \
  --host ws://127.0.0.1 --port 5678
```

or, if `signal-cli` is running its daemon over TCP instead:

```bash
hivemind-signal-bridge \
  --signal-account +15551234567 \
  --signal-host 127.0.0.1 --signal-port 7583 \
  --access-key <key> --password <password> \
  --host ws://127.0.0.1 --port 5678
```

By default the bridge answers anyone who messages the registered
number. Pass `--allowed-sender <phone_number>` (repeatable) to restrict
it to specific senders.

Useful flags:

- `--site-id`: this bridge's HiveMind site id. If you run more than one
  bridge (or more than one instance of this bridge) on the same host,
  give each a distinct site id — otherwise they collide over the same
  identity file and pinned peer keys.
- `--self-signed`: accept a self-signed TLS certificate on `wss://` hubs.
- `--lang`: the language tag attached to forwarded utterances (default
  `en-us`).

Run `hivemind-signal-bridge --help` for the full list.

## Docker

The Docker image in this repository is the bridge only — it does not
bundle `signal-cli`. Run `signal-cli`'s own daemon as a separate
container (or process) and share a socket volume or a TCP endpoint with
it; `docker-compose.yml` sketches that layout with a placeholder
`signal-cli` service image. You will need to already have a registered
account before either container is useful — registration is an
interactive, one-time step (see above) that this compose file does not
do for you.

```bash
docker build -t hivemind-signal-bridge .
docker run --rm \
  -e SIGNAL_ACCOUNT=+15551234567 \
  -e SIGNAL_SOCKET=/signal-cli/socket \
  -v /var/run/signal-cli:/signal-cli \
  -e HIVEMIND_ACCESS_KEY=... \
  -e HIVEMIND_PASSWORD=... \
  -e HIVEMIND_HOST=ws://hivemind-core \
  hivemind-signal-bridge
```

## What this bridge does, precisely

- Connects to a running `signal-cli` JSON-RPC daemon over a unix socket
  or TCP; connects to the HiveMind hub with
  `hivemind_bus_client.HiveMessageBusClient`.
- Skips messages from the bridge's own registered account (so it can
  never talk to itself) and any notification with no text body.
- Only forwards messages once the HiveMind handshake has completed —
  forwarding earlier would get the connection killed by the hub instead
  of just failing the one message.
- Forwards each remaining message as a `recognizer_loop:utterance` bus
  message, carrying the sender's Signal phone number in the message
  context so the hub's `speak` reply can be routed back to the right
  conversation.
- Sends `speak` replies (and a fixed fallback line on
  `hive.complete_intent_failure`) back to the originating sender via
  `signal-cli`'s `send` JSON-RPC method.

## Testing

```bash
pip install -e .[test]
pytest tests/
```

The test suite mocks both the signal-cli JSON-RPC client and the
HiveMind `HiveMessageBusClient`, so it runs without a live socket, a
registered account, or a hub. It has not been exercised against a real
`signal-cli` daemon or a real HiveMind hub — that needs a registered
phone number, which this repository does not have.
