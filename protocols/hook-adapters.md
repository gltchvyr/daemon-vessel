# Hook Adapters

Hooks catch footprints before the model has to remember to remember.

This protocol defines the first generic adapter contract for daemon-vessel hook capture.

## Command

```bash
daemon-hook capture --harness generic --event UserPromptSubmit
```

The command reads JSON from stdin by default and writes a raw markdown trace to:

```text
memory/hooks/
```

Raw hook traces are not durable memory by default. They are provenance material awaiting review, summarization, promotion, deletion, or cascade handling.

## Example

```bash
printf '{"prompt":"work on hook adapters","session_id":"local-test"}' \
  | daemon-hook capture --harness claude-code --event UserPromptSubmit --summary "User asked to work on hook adapters"
```

## Capture policy

A hook should usually write raw trace material, not promoted memory.

```text
hook event -> raw hook trace -> review -> capture / promote / delete / ignore
```

Promotion belongs to the archive layer.

Deletion and revocation belong to the lifecycle layer.

## Redaction

The adapter redacts common secret-bearing keys by default, including token, secret, password, cookie, authorization, and API-key-like fields.

Additional key substrings can be redacted:

```bash
daemon-hook capture --redact-key session --redact-key email
```

## Suggested event mapping

### User prompt submit

Capture the prompt or prompt metadata as raw trace.

```bash
daemon-hook capture --harness claude-code --event UserPromptSubmit
```

### Post tool use

Capture tool name, result summary, and file paths touched. Avoid storing full large outputs unless needed.

```bash
daemon-hook capture --harness codex --event PostToolUse
```

### Session start

Capture session metadata and optionally refresh a handoff/context packet.

```bash
daemon-hook capture --harness cursor --event SessionStart --refresh-shrine-state
```

### Session end

Capture closing state and then run handoff separately.

```bash
daemon-hook capture --harness generic --event SessionEnd
 daemon handoff
```

## Design rule

Hooks solve trace-loss.
They do not solve meaning by themselves.

The archive decides what becomes durable memory.
The vessel records and inspects.
The shrine reveals state.
The cathedral may suggest interpretations.

## Next adapter targets

- Claude Code hook config examples
- Codex wrapper examples
- Cursor hook examples
- Git commit hooks
- local shell aliases for manual capture
