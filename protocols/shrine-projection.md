# Shrine Projection v1

Signal Shrine receives a narrow visual projection, never the canonical continuity object.

The vessel validates canonical schema, canary, and scoped payload integrity, then requires exact operator-supplied revision and payload SHA-256 expectations. Only a fully matched projection sets `applyAllowed: true`. Missing expectations, invalid integrity, or stale currency produce a written refusal object that the Shrine may display but must not apply.

The projection deliberately excludes participants, relational and interaction profiles, rituals, boundaries, private provenance, canonical source paths, and the continuity canary value. It exposes only a receipt, selected project summaries, thread subjects, affective trace titles, a bounded Signal Footprint, and visual weather.

```powershell
py -3 -m daemon_vessel shrine-state `
  --continuity-state "C:\path\to\Gl!tch_Continuity_State.json" `
  --expect-revision 4 `
  --expect-payload-sha256 af0e3a29d5a646c41b665c588eb4e219397a9495c95e9dea940ca08a1e2b63e8 `
  --out "..\signal-shrine-prototype\public\daemon\current-shrine-state.local.json"
```

The local output filename is ignored by the Shrine repository. Its committed fallback is an unbound refusal fixture, so absence can never masquerade as current state.
