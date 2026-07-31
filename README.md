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
