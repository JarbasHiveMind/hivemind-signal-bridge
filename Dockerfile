FROM python:3.14-slim

WORKDIR /app
COPY . /app

# force the current hivemind-bus-client alpha rather than whatever a stale
# base layer might already have cached, since this bridge depends on the
# run_forever()-after-connect() fix and the current identity/handshake API
RUN pip install --no-cache-dir --upgrade "hivemind-bus-client>=1.0.13a1" \
    && pip install --no-cache-dir .

# This image does NOT run signal-cli itself. signal-cli must already be
# running elsewhere (its own container or process) as a JSON-RPC daemon,
# e.g. `signal-cli -a +NUMBER daemon --socket /signal-cli/socket`, with
# that socket bind-mounted into this container, or reachable over TCP.
# The account must already be registered with Signal -- see the README,
# registration needs a real phone number that can receive an SMS.
ENV HIVEMIND_HOST=ws://127.0.0.1 \
    HIVEMIND_PORT=5678 \
    HIVEMIND_SITE_ID=signal \
    HIVEMIND_LANG=en-us

ENTRYPOINT ["sh", "-c", "exec hivemind-signal-bridge \
  --signal-account \"$SIGNAL_ACCOUNT\" \
  ${SIGNAL_SOCKET:+--signal-socket \"$SIGNAL_SOCKET\"} \
  ${SIGNAL_HOST:+--signal-host \"$SIGNAL_HOST\"} \
  ${SIGNAL_PORT:+--signal-port \"$SIGNAL_PORT\"} \
  --access-key \"$HIVEMIND_ACCESS_KEY\" \
  --password \"$HIVEMIND_PASSWORD\" \
  --host \"$HIVEMIND_HOST\" \
  --port \"$HIVEMIND_PORT\" \
  --site-id \"$HIVEMIND_SITE_ID\" \
  --lang \"$HIVEMIND_LANG\" \
  $EXTRA_ARGS"]
