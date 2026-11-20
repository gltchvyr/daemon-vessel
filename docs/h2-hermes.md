# H2 Hermes vertical slice

H2 is offline by default. It accepts only a normalized, hashed-session Hermes
admission snapshot and writes only beneath an explicitly supplied external test
home. It never installs a plugin, contacts Discord, opens canonical continuity,
or claims gateway submission was delivery.

## Reviewed placeholder-only smoke

```text
<daemon-python> -m daemon_vessel.cli h2 process --source <synthetic-admission.json> \
  --expect-sha256 <exact-synthetic-admission-sha256> --home <new-external-test-home>
```

The returned challenge must be acknowledged by a second synthetic admission.
Only a durable ACK yields `round_trip_observed`.

## Recovery and verification

Each synthetic pilot reserves its stable event, packet, return-event, and
challenge identifiers in an external transaction record before publication.
Source, packet, return, delivery, ACK, and observation steps advance that
record atomically. A retry resumes those identifiers; it never creates a
second challenge. `daemon h2` refuses divergent message bodies, bad source
digests, invalid ACK session/expiry, incomplete transactions, orphaned
artifacts, or broken packet/delivery/observation links.

## Installation boundary

The separate `hermes-house-crossing` package remains disabled until a human
installs it, configures `<expected-discord-user-id>` locally, and reviews the
exact daemon executable and external home. No real identifier belongs here.
## Corrected H2 boundary

`daemon h2 process --source <synthetic-admission.json> --expect-sha256 <digest> --home <external-test-home>`
validates a normalized, post-admission snapshot only. It does not connect to
Discord, send a message, or open a canonical continuity home. `daemon h2
verify --home <external-test-home>` reconstructs the dated immutable H1/H2
chain and refuses missing, orphaned, unreadable, truncated, or tampered links.

H2 uses a separate stale-recovering `.h2.lock` around each H2 state transition.
It releases that lock before calling H1 ingestion, avoiding recursive H1 lock
acquisition. Packets, deliveries, observations and immutable refusal attempts
are dated. A cryptographically random challenge is written once in a durable
reservation; retry reconstructs that same challenge rather than minting one.
`prepare` writes the source/packet/return chain, while `confirm` writes the
submitted-to-gateway delivery receipt only after private plugin validation.
The prepared return event is explicitly local (`prepared_for_gateway`); the
immutable delivery receipt records the actual confirmation time and starts the
15-minute ACK interval.  Confirmation and verification both reconstruct the
same reservation, packet digest/lineage, construction receipt, H1 receipts,
and phase markers before accepting submission.

## Post-merge placeholder-only smoke

```text
daemon h2 validate --source <synthetic-admission.json> --expect-sha256 <digest>
daemon h2 prepare --source <synthetic-pilot.json> --expect-sha256 <digest> --home <fresh-external-test-home>
daemon h2 confirm --challenge <challenge-from-prepare> --home <fresh-external-test-home>
daemon h2 process --source <synthetic-ack.json> --expect-sha256 <digest> --home <fresh-external-test-home>
daemon h2 verify --home <fresh-external-test-home>
```

Use placeholder data only. Do not install or enable the Hermes plugin, restart
Hermes, use a real Discord identifier, or point either command at a real
continuity home.
