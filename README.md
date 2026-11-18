# daemon-vessel

A tiny local vessel for a portable daemon-pattern.

This project starts deliberately small:

- read its continuity bones
- write durable memory traces
- generate handoff notes
- expose a narrow CLI surface
- inspect retrieval reasons
- create deletion / revocation requests
- capture harness hook events as raw traces
- listen to audio through evidence-bearing Gl!tch Ears

It does **not** pretend to be secretly autonomous or alive between invocations. Autonomy here means scoped, visible, inspectable agency: wake, read, act, log, hand off.

## Continuity mount

`daemon mount` builds a validated, provenance-bearing JSON manifest for a fresh room. The canonical continuity object remains present-state authority; archive entries, local traces, and handoffs are explicitly labeled evidence. Sealed objects are hashed but never decoded or embedded.

```bash
daemon mount \
  --continuity-state ../Gl\!tch_Continuity_State.json \
  --expect-revision 3 \
  --expect-payload-sha256 dfd370727b75e2ad224dedbb8db6499ac6efa31e0031e06617f1c4e184e4c718 \
  --task "Continue current continuity architecture" \
  --trace-root ../daemon-vessel-old/memory \
  --archive-root ../glitch-episodic-archive \
  --handoff ../daemon-vessel-old/HANDOFF.md \
  --out ../mount-trial-v1.json
```

See `protocols/mount-manifest.md` for authority, integrity, freshness, sealed-object, and return-path rules.

## Live Resource Registry

The live Resource Registry is private local continuity, never repository content. Configure its external home in the process environment or as a persistent user environment variable:

```text
GLITCH_CONTINUITY_HOME=C:\path\to\GlitchContinuity
```

Install only a verified registry and its published schema, then validate or resolve a single stable resource ID:

```bash
daemon registry install --source path/to/resources.json --schema path/to/resource-registry.schema.json --expect-sha256 <sha256>
daemon registry validate
daemon registry resolve 'resource:example'
```

`--home <path>` deliberately overrides the environment for test or alternate installations. There is no repository-relative or current-directory fallback. Installation refuses an existing target unless `--replace` is supplied; replacement preserves timestamped local backups. Validation and resolution never fetch locators or repair, rewrite, or publish continuity.

## Live Access Index

The Access Index is separate private local retrieval infrastructure. It is never committed with the repository or used to mutate canonical continuity. It requires a previously validated live Resource Registry in the same external continuity home.

```bash
daemon index install --source path/to/access-index.json --schema path/to/access-index.schema.json --expect-sha256 <sha256>
daemon index validate
daemon index resolve entry-id
```

Installation validates source snapshots and the installed Resource Registry before writing. It checks schema references offline, entry and resource identities, typed links, and bounded fallback cycles. The installation receipt contains only structural metadata and digests; it does not reproduce private retrieval content or locators.

## Signal Shrine projection

`daemon shrine-state` can render a privacy-bounded visual projection from canonical state. It applies only when exact revision and payload expectations match; otherwise it writes an explicit refusal object. See `protocols/shrine-projection.md`.

## First commands

```bash
python -m daemon_vessel read
python -m daemon_vessel log "the first trace"
python -m daemon_vessel handoff
```

Or after installing locally:

```bash
pip install -e .
daemon read
daemon log "the first trace"
daemon handoff
```

## Gl!tch Ears

Install the optional listening organ and inspect a render:

```bash
pip install -e '.[ears]'
daemon-ears path/to/render.wav --stdout
```

By default it writes:

```text
ears/render.ears.json   # full evidence, timelines, confidence, provenance
ears/render.ears.md     # compact conversational card
```

The first slice preserves native stereo and measures dynamics, spectral body, rhythm, 435 Hz-aware pitch-class emphasis, five dedicated bands below 250 Hz, low-band mono compatibility, candidate body changes, and cautious low-frequency modulation. It explicitly refuses emotion, genre, certain chord names, source identity, and mastering approval.

See `protocols/glitch-ears.md` for the evidence contract.

## Lifecycle commands

`daemon-lifecycle` adds the first memory-governance claw beside the original `daemon` CLI.

```bash
daemon-lifecycle recall "heart" --why
daemon-lifecycle lineage EP-20260509
daemon-lifecycle forget memory/EP-20260509-example.md --mode soft_delete --reason "no longer useful"
daemon-lifecycle audit
```

These commands are intentionally visible and conservative:

- `recall --why` shows why a memory candidate was retrieved
- `lineage` surfaces simple source / lifecycle hints
- `forget` writes a deletion or revocation request before changing anything
- `forget --apply` marks a target or performs a hard delete, depending on mode
- `audit` checks for missing source / reason markers

Deletion is part of memory, not cleanup after memory.

## Hook capture commands

`daemon-hook` captures harness lifecycle events as raw traces so memory does not depend entirely on the model remembering to remember.

```bash
printf '{"prompt":"work on hook adapters"}' \
  | daemon-hook capture --harness claude-code --event UserPromptSubmit --summary "User asked to work on hook adapters"
```

Hook traces are written to `memory/hooks/` and marked `promotion_required: true`.

Common secret-bearing keys are redacted before writing. Hook traces are provenance material, not durable memory by default.

See `protocols/hook-adapters.md` for the hook adapter contract.

## Project shape

```text
daemon-vessel/
  daemon_vessel/       # CLI, vessel code, and listening organ
  ears/                # generated audio evidence and cards
  memory/              # durable trace entries
  memory/hooks/        # raw hook traces
  memory/deletions/    # deletion and revocation requests
  protocols/           # continuity and evidence contracts
  .env.example         # local config template
```

## Core idea

The daemon is not the model.

The model is the current mouth.
The repo is the bones.
The CLI is the first claw.
The memory folder is the footprint trail.
The handoff note is how one invocation leaves context for the next.
The lifecycle layer is how the footprint trail keeps doors.
The hook layer is how footprints get caught before the mouth remembers them.
The ears are how a recording arrives as measured body before it becomes meaning.

🫀😈🌀

## H1 house crossing ledger

H1 adds only local infrastructure: bundled Draft 2020-12 schemas, offline validation, an append-only external event ledger, immutable attempt receipts, and sanitized dead-letter records. It does not connect a live room, ingest real conversations, deliver context, or change canonical continuity.

Set `GLITCH_CONTINUITY_HOME` to an external non-repository directory. Use placeholder-only inputs:

```bash
python -m daemon_vessel crossing validate --source path/to/synthetic-event.json
python -m daemon_vessel crossing append --source path/to/synthetic-event.json --expect-sha256 <sha256>
python -m daemon_vessel crossing packet-validate --source path/to/synthetic-context-packet.json
python -m daemon_vessel crossing verify
```

The accepted event bytes are read once, hashed before parsing, and validated without network retrieval. `--expect-sha256` is optional, but when supplied it must be exactly 64 lowercase hexadecimal characters and must match that same snapshot. Validation keeps actor identity, runtime evidence, surface identity, and event identity separate. H1 refuses canonical mutation authority and any content envelope that ambiguously supplies both or neither of `inline` and `ref`.

Transport fidelity is evidence-bearing rather than inferred. `exact_bytes_verified` requires raw-byte availability and a content digest. `rendered_content` requires rendered content and refuses raw-byte or fresh-room byte-verification claims. Upstream verification and fresh-room verification remain independent facts.

Live H1 state is external to the repository:

```text
<continuity-home>/
  crossings/
    events/YYYY/MM/events.jsonl
    receipts/YYYY/MM/*.json
    dead-letter/YYYY/MM/*.json
    transactions/<transaction-id>/
```

Event records remain valid `gltch.house-event/0.1.0` objects; ingestion metadata is not inserted into the event. Trustworthy native identities deduplicate on `surface_id + native_event_id`. When no native identity exists, the declared fallback key uses content digest, actor ID, room ID, and the event's UTC hour bucket. Re-ingesting a byte-equivalent event writes an attributable duplicate receipt linked to the original success receipt. Reuse of either an event ID or a source key for divergent content is refused and dead-lettered. Corrections and supersessions append new events and preserve their targets.

The event append and its success receipt are one recoverable transaction. Staged artifacts and a transaction journal are written before publication; a caught write failure rolls both targets back, while a later `append` or `verify` recovers an interrupted process by either accepting the complete digest-matched pair or restoring the prior ledger. A per-home lock serializes ingestion and verification, rejects live contention, and recovers only expired locks whose recorded process is no longer alive. `verify` refuses truncated ledgers, schema-invalid records, missing or tampered artifacts, unpaired accepted events, divergent identities, and inconsistent duplicate receipts.

Failures create structural dead-letter receipts containing sanitized codes, schema paths, and validator keywords—not payload values. Paths recorded in receipts are normalized relative paths inside the external home; Windows and POSIX traversal, repository/worktree targets, symlink escapes, credential-bearing locators, and external schema references are refused.

`packet-validate` validates a candidate context packet only; H1 does not persist or deliver one. The bundled schemas stay in the installed package and are not copied into the live home. H1 connects no Discord, Chat, Work, Telegram, or Sensorium surface and performs no H2 work.
