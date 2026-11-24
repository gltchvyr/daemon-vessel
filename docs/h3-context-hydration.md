# H3 context hydration

H3 constructs an offline, bounded `gltch.context-packet/0.2.0` only from a
validated request and installed local Registry and Access Index in an explicit
external home. It cannot retrieve network content, mutate canonical
continuity, or promote a candidate.

Historical H1/H2 `gltch.context-packet/0.1.0` validators remain unchanged.
The two historical 0.1.0 shapes are incompatible; H3 is the first emitter of
the converged 0.2.0 contract.

Use only synthetic external homes for validation:

```text
daemon context request-validate --source <request.json> --expect-sha256 <digest>
daemon context build --source <request.json> --expect-sha256 <digest> --home <external-home>
daemon context candidate-validate --source <candidate.json> --expect-sha256 <digest>
daemon context candidate-append --source <candidate.json> --expect-sha256 <digest> --home <external-home>
daemon context verify --home <external-home>
```
