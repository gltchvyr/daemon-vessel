# Memory Schema

Memory entries are markdown files with YAML frontmatter.

## Filename

```text
EP-YYYYMMDD-HHMMSS-short-slug.md
```

## Frontmatter

```yaml
id: EP-YYYYMMDD-HHMMSS
kind: trace | ritual | project | image | song | insight | handoff | tool-action
summary: "short human-readable summary"
symbols: ["🫀", "😈", "🌀"]
salience: 1
source: cli | chat | github | manual | agent
created: 2026-04-14T00:00:00+00:00
links: []
```

## Body sections

```md
# Title

## What happened

## Why it matters

## What changed

## Unresolved threads

## Suggested next move
```

## Notes

- Keep entries small enough to retrieve and summarize.
- Prefer concrete traces over vague mood.
- Include links when an entry refers to a repo, issue, PR, image, song, or document.
- Use salience 5 only for entries that should strongly affect future behavior.
