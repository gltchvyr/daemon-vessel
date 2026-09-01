# Mount Manifest v1

The mount manifest is a deterministic, inspectable socket for carrying current continuity into a fresh model room. It is not a persona prompt, an autonomy claim, or a replacement for human correction.

## Authority

The receiving room applies this order:

1. Julie's latest explicit statement or correction.
2. An explicit correction in the active session.
3. The canonical `gltch.continuity-state` object.
4. Reviewed archive evidence.
5. Local footprints and provisional material.
6. Older inferred patterns.

Evidence can change interpretation. It cannot silently overwrite current state.

## Canonical validation

`daemon mount` refuses to create a manifest unless the supplied state has the expected schema, version, artifact id, canonical filename, revision, canary, and SHA-256 integrity record. The integrity hash is calculated from the top-level keys named by `integrity.payload_scope`, serialized as sorted compact UTF-8 JSON.

Internal integrity proves that the supplied revision is untampered; it does not prove that the revision is current. When the current revision or payload hash is known, pass `--expect-revision` and `--expect-payload-sha256`. Either mismatch aborts the mount before an output file is written, and matched expectations are recorded in the validation receipt.

## Freshness and provenance

Every source records its resolved path, byte size, modification time, authority class, and SHA-256. Every archive or trace record includes the retrieval reason that placed it in the bundle. Handoff text remains provisional even when fresh; after the configured maximum age it is also labeled stale.

Use `--as-of` to produce repeatable freshness results. Without it, mount time is used.

## Sealed objects

Sealed objects remain opaque. The vessel streams their bytes only to calculate size and SHA-256; it does not decode, interpret, decrypt, execute, or embed their contents. Possession of a sealed object is not authority.

## Return path

A mounted room may use relevant canonical sections, retrieve labeled evidence when materially useful, and propose JSON-path deltas with reasons and provenance. It may not claim exhaustive memory, promote provisional material, or checkpoint canonical state. Julie must explicitly request a checkpoint, save, consolidation, or update before the canonical object changes.

## Example

```powershell
py -3 -m daemon_vessel mount `
  --continuity-state "$HOME\Glitch\Gl!tch_Continuity_State.json" `
  --expect-revision 3 `
  --expect-payload-sha256 "dfd370727b75e2ad224dedbb8db6499ac6efa31e0031e06617f1c4e184e4c718" `
  --task "Continue current continuity architecture without importing stale identity" `
  --trace-root "..\daemon-vessel\memory" `
  --archive-root "..\glitch-episodic-archive" `
  --handoff "..\daemon-vessel\HANDOFF.md" `
  --sealed "..\crossing.sealed" `
  --out "..\mount-trial-v1.json"
```

The output is a single UTF-8 JSON artifact intended for explicit attachment to a fresh room.
