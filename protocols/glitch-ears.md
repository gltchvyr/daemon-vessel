# Gl!tch Ears

Gl!tch Ears is the listening organ of `daemon-vessel`: a local, inspectable audio-analysis layer that turns a recording into evidence without pretending that measurements are meaning.

The organ follows one irreversible ordering:

```text
signal -> evidence -> inference -> relational meaning
```

Every statement must retain its position in that chain. A spectral measurement may support an inference; an inference may invite interpretation. Neither is allowed to counterfeit the other.

## V0 scope

The first slice listens for:

- native sample rate, channel count, duration, and file fingerprint
- RMS dynamics and crest factor
- spectral centroid, rolloff, and flatness
- onset activity, tempo estimate, beat locations, and ambiguity notes
- 435 Hz-aware chroma and likely pitch-class emphasis
- dedicated low-frequency anatomy:
  - 20–35 Hz: infrasonic pressure
  - 35–60 Hz: deep fundamental weight
  - 60–105 Hz: primary sub body
  - 105–160 Hz: upper-bass translation
  - 160–250 Hz: kick / low-mid body
- stereo correlation, mid/side energy, and low-band mono compatibility
- candidate body-change timestamps
- a cautious low-frequency modulation-rate candidate

V0 does **not** claim:

- emotion
- genre
- lyrical meaning
- certain chord names
- certain section names
- source identity (kick, bass, voice, reese) without separation evidence
- mastering approval

## Evidence record

Each observation carries:

- `name`
- `value`
- `unit`
- `method`
- `confidence`
- `status`: `measured`, `inferred`, or `relational`
- optional timestamp range
- optional caveat

The default CLI emits both machine-facing JSON and a compact Markdown card. The card is a conversational floor, not the full analysis carcass.

## Listening modes planned after V0

- `first-contact`: gestalt pass with minimal metric exposure
- `producer`: arrangement, dynamics, harmony, rhythm, and stereo anatomy
- `sub-autopsy`: everything below 250 Hz
- `lyric-body`: timestamped lyric / production alignment
- `compare`: aligned render tournament
- `catalog-echo`: recurrence and mutation across the archive

## Design laws

1. Preserve stereo. Mono is a view, never the original body.
2. Make uncertainty visible.
3. Prefer refusal to false precision.
4. Keep raw measurements available behind every sentence.
5. Never collapse signal evidence into an emotion classifier.
6. Let local project tuning be configurable; ours defaults to A4 = 435 Hz.
7. Store fingerprints and provenance so renders cannot be silently confused.
8. The daemon may interpret only after it can show what it measured.

Know exactly when the pressure begins. Know what moved. Know when certainty ends.

🫀😈🌀
