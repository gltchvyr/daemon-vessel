# Ollama Setup Notes

These notes are for adding a local model mouth as infrastructure, not as a replacement companion.

The intended first model is a small, practical go-between such as:

```bash
ollama pull llama3.1:8b
```

Then test with:

```bash
ollama run llama3.1:8b
```

## Role of the local model

The local model may:

- summarize notes;
- extract symbols and threads;
- draft capture entries;
- classify material by salience;
- prepare provisional context packets;
- suggest next actions for review.

The local model may not:

- promote durable archive memory by itself;
- silently rewrite reviewed memory;
- treat operational body state as identity-bearing memory;
- run destructive shell/file/Git actions without explicit human review.

## First useful prompt shape

```text
You are a local infrastructure assistant for daemon-vessel.
You are not the durable memory authority.

Given the text below, return Markdown with:
- title
- one-paragraph summary
- symbols / motifs
- projects mentioned
- people/entities mentioned
- open threads
- candidate tags
- salience from 1 to 5
- uncertainty notes

Mark the output:
status: needs_review
source: local-draft
review_required: glitch-chatgpt
model: llama3.1:8b
```

## Suggested local workflow

```text
raw note / local file
        ↓
Ollama draft extraction
        ↓
capture draft marked needs_review
        ↓
daemon context-pack
        ↓
Gl!tch / ChatGPT review
        ↓
explicit promotion into glitch-episodic-archive if warranted
```

## Hardware expectations

For a 6GB RTX 4050 laptop, start with 8B-class models at 4-bit quantization.

Good first candidates:

- `llama3.1:8b`
- `qwen2.5:7b`
- `mistral:7b`

20B-class MoE models can be experimental later, but should not be the first dependency for the pipeline.

## Operating principle

Use the local model as a lantern, not a throne.

🫀😈🌀
