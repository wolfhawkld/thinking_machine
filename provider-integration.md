# Provider Integration Record

This file records reproducible, non-secret provider metadata. It must never
contain an API key, account identifier, authorization header, or raw provider
payload that may include either.

## Selected development canary

The initial adapter target was taken from the sibling `IntentWeight` project's
local `.env` and its completed LLM smoke-test configuration:

- protocol: OpenAI-compatible Chat Completions;
- base URL: `https://api.deepseek.com`;
- model requested: `deepseek-v4-flash`;
- environment variables: `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`, and
  `DEEPSEEK_API_KEY`;
- credential state at inspection time: present, but deliberately not copied or
  printed;
- temperature condition: provider thinking mode must be disabled because
  temperature is the experimental treatment;
- JSON condition: request `response_format={"type":"json_object"}` and retain
  the prompt-level exact-schema instruction. This provider mode guarantees a
  JSON object but does not itself enforce the project-specific field schema;
- seed status: unsupported/unverified; the adapter omits the seed and records
  `seed_supported=false` rather than silently claiming common random numbers;
- backend identity: every new paid gate records `system_fingerprint`; a canary
  or gate passes only if it is non-empty and stable across all calls in that
  attempt. The requested/echoed model must also be exactly
  `deepseek-v4-flash`. A stable fingerprint is useful provenance, but is not
  described as a permanently frozen public model snapshot.

The candidate wire contract is exact: the response must be one JSON object
whose only key is `expression`, and that value must be one DSL string. Extra
keys, arrays/nested ASTs, null/empty content, invalid JSON, and non-string
expressions are closed candidate-format failure categories. The prompt archive
likewise renders candidate expressions as DSL strings, never nested JSON
arrays. Durable ledgers and result artifacts retain normalized categories,
parsed candidate fields, usage, and hashes; they do not retain an API key, raw
prompt, or raw response content.

## Canary result — 2026-08-04

The one-request canary completed successfully and is stored locally at
`artifacts/deepseek-canary.json` (the artifacts directory is git-ignored).

- provider requests: `1`;
- requested and response-echoed model: `deepseek-v4-flash`;
- thinking: disabled;
- assigned temperature: `0.7`;
- maximum output tokens: `128`; this historical one-request protocol is not
  changed retroactively by the later multi-round amendment;
- seed: not sent; adapter records seed support as false;
- finish reason: `stop`;
- billed usage: `481` input tokens and `16` output tokens;
- measured latency: `4029.146 ms`;
- response: valid JSON containing one valid DSL expression;
- candidate: `(add (var x2) (var x3))`;
- probe accuracy: `1/12`; this is an integration diagnostic, not an empirical
  result and was not used for protocol tuning.

The request demonstrates that this endpoint accepts the temperature-bearing,
thinking-disabled JSON call shape and returns auditable usage. It does not by
itself show that changing temperature changes realized diversity; that remains
the preregistered H1 manipulation check. The provider echoed the requested
alias rather than a more precise immutable revision, so confirmatory use should
record that limitation or select a versioned model identifier if one is made
available.

Seed `1000` is now permanently classified as operational calibration. It is
used by the historical one-call/one-world diagnostics and the multi-round
format canaries, excluded from the eight-world comparator-selection pilot, and
retained in the development seed registry so it can never enter confirmation.

## First development-gate attempt — 2026-08-04

The first fresh 140-slot attempt stopped safely after eight completed calls.
The ninth logical slot raised `ResponsePayloadError`; the original ledger
recorded the safe exception class but not its normalized subcondition, so it
cannot establish whether the defect was candidate content or another response
shape field. The failure is retained in
`artifacts/deepseek-gate-attempt-20260804.jsonl`, and no result artifact was
written. The partial attempt is permanently abandoned and is not merged into
another run.

The audit exposed an integration-policy bug that could independently cause this
failure class: malformed model candidate text had been treated as an adapter
failure rather than a parse/grammar outcome for its paid slot. Malformed outer
response JSON, missing usage, invalid model identity, or other unauditable
envelope failures must still abort the attempt. The adapter and offline tests
now enforce that boundary, and future ledgers include only a closed failure
category (plus a validated integer HTTP status when applicable), never
exception text. A fresh attempt uses new ledger and result paths and a new
frozen source manifest.

## Second development-gate attempt — 2026-08-04

Attempt B completed all `140/140` transport calls and is preserved at:

- `artifacts/deepseek-gate-attempt-20260804-b.jsonl`;
- `artifacts/deepseek-development-gate-b.json`;
- `artifacts/deepseek-development-gate-analysis-b.json`.

The response envelope, exact echoed model, `finish_reason=stop`, and billed
usage were present for all calls. The attempt used 92,292 input tokens and
4,630 output tokens with zero retries. However, the candidate interface
collapsed after round 1: 104 of 140 calls produced the schema-failure sentinel,
leaving 36 schema-adherent candidates, one of which was additionally invalid
DSL. Round 1 was `28/28` schema- and DSL-valid; rounds 2--5 were only `8/112`
schema-valid and `7/112` DSL-valid.

The round boundary strongly implicates the multi-round archive representation:
the archived AST had been exposed as a nested JSON array while the required
wire field was a DSL string. Because raw provider content was intentionally not
retained, that exact malformed response shape cannot be reconstructed from the
artifact and is stated as a diagnosis, not a proved fact about every failed
call. Attempt B is therefore `interface-broken / core-hypothesis-indeterminate`,
not a negative test of adaptive scheduling. Its legacy analysis artifact is
preserved rather than overwritten by later analysis-schema revisions.

Attempt B's realized mean-token relative range was `14.69%`, above the frozen
2% diagnostic. The primary design matches ex-ante opportunities (calls, output
ceilings, verifier access, archive capacity, and feedback rules), not realized
provider tokens, which are partly downstream of treatment and context. The
exceedance requires a frozen resource/cost sensitivity analysis and forbids an
`actual-token-matched` claim; it does not automatically invalidate the
call-matched intention-to-treat contrast.

## Multi-round format canary and Gate C

Format canary v1 is preserved at
`artifacts/deepseek-format-canary-attempt-20260808.jsonl` and
`artifacts/deepseek-format-canary-20260808.json`. It used the historical
128-token ceiling and completed all `8/8` transport calls with zero retries,
but did not pass the frozen format gate. In R1, one response satisfied the JSON
field schema but contained invalid DSL; the temperature-`1.2` response reached
exactly 128 output tokens, had a non-`stop` finish, and was invalid JSON. All
four archive-bearing R2 responses were JSON-schema-, syntax-, and runtime-valid.
The archive repair therefore behaved correctly in R2, while the canary as a
whole remains a failed engineering check and does not test the core hypothesis.

### Protocol amendment — 2026-08-08

DeepSeek's [official JSON Output guidance](https://api-docs.deepseek.com/guides/json_mode/)
advises setting `max_tokens` reasonably to reduce incomplete JSON. Based on
that guidance and the directly observed v1 cap hit, the output ceiling is
uniformly frozen at `256` for format canary v2, Gate C, the eight-world
development pilot, and any prospective confirmatory run. This amendment was
made before any core Gate C or pilot outcome was observed. It is an engineering
calibration only: the strict `8/8` canary requirement, exact JSON/DSL contract,
syntax/runtime checks, model/finish/usage/cache/fingerprint checks,
thinking-disabled mode, and no-retry/no-resume rules are unchanged.

Official-endpoint format canary v2 then ran with the following frozen design:

- retired seed `1000`, hidden-law depth 3, MTX only;
- two rounds by four candidates, with `[0.2, 0.7, 0.7, 1.2]` in each round;
- archive capacity 4 and at most two released counterexamples per round;
- maximum 256 output tokens, provider thinking disabled;
- no retry and no resume; an interrupted/ambiguous attempt is abandoned.

Passing required exactly `8/8` successful responses with the exact model,
`finish_reason=stop`, complete input/output/cache usage, the equality
`prompt_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens`, and one
stable non-empty `system_fingerprint`. In both rounds every candidate must have
`candidate_format=json_expression` and pass syntax and runtime validation.
Any failure stops the paid sequence as engineering-format `indeterminate`.

The guarded entry point is `python -m src.format_canary`, whose default config
is `configs/format-canary.json`. The completed v2 invocation was:

```bash
python -m src.format_canary \
  --env-file ../IntentWeight/.env \
  --execute \
  --attempt-ledger artifacts/deepseek-format-canary-attempt-20260808-v2.jsonl \
  --output artifacts/deepseek-format-canary-20260808-v2.json
```

The ledger and output paths were distinct and did not overwrite the historical
v1 artifacts. V2 completed `8/8` requests with zero retries. Exact model,
`stop`, input/output/cache usage, stable fingerprint, disabled reasoning, and
the 256-token ceiling all passed. R1 content passed `4/4`; R2 passed `3/4`.
The sole failure was the temperature-`1.2` R2 response: it had a valid outer
JSON `expression` string and `stop` finish, but that non-empty string was
rejected by the frozen DSL parser as `parse_or_grammar`. Its output was 16
tokens, so the failure was not truncation. V2 therefore failed the strict
`8/8` content gate and did not authorize Gate C.

## Volcengine Agent Plan cross-route diagnostic — frozen before eight-call run

The existing user-owned Hermes configuration exposes an OpenAI-compatible
Volcengine Agent Plan route. Volcengine's official material identifies
DeepSeek V4 Flash as supported by Agent/Coding Plan, and its compatibility
documentation describes the same Chat Completions request shape:

- [Agent Plan model support](https://developer.volcengine.com/articles/7645133105870667830);
- [OpenAI-compatible calling guidance](https://www.volcengine.com/docs/6492/2192012?lang=en).

Before any eight-call outcome on this route, exactly one minimal compatibility
request was made. It established and froze this route-specific contract:

- exact base URL: `https://ark.cn-beijing.volces.com/api/plan/v3`;
- request model alias: `deepseek-v4-flash`;
- exact response model alias: `deepseek-v4-flash-ga-260731`;
- thinking disabled, `finish_reason=stop`, one provider request;
- input/output usage present, but prompt-cache hit/miss values unavailable after
  adapter normalization;
- `system_fingerprint` unavailable after adapter normalization;
- outer JSON and DSL syntax/runtime valid for that compatibility request;
- the generated expression was neither printed nor retained.

The adapter maps both a wire-level omitted field and explicit JSON `null` to
Python `None`, so the diagnostic makes no stronger wire-level absence claim.
The new eight-call config is `configs/format-canary-volcengine.json`. Provider
telemetry is handled by a separately frozen capability contract: both cache
values and the fingerprint must remain normalized as unavailable on every
call, and any partial/unexpected value or response-alias drift fails closed.
This does not relax the candidate test: both rounds still require `4/4`, hence
`8/8`, valid JSON `expression` strings with DSL syntax and runtime validity. No
API key, endpoint credential, raw prompt, or raw response is persisted.
Preflight rejects a different normalized base URL before constructing any paid
slot; the result artifact records only the normalized URL's SHA-256 and whether
the frozen endpoint contract was satisfied.

The alternate route lacks the official endpoint's cache-accounting and stable-
fingerprint provenance. Its result is therefore a cross-provider diagnostic,
not a protocol-equivalent replacement for v2. It used fresh exclusive paths:

- `artifacts/volcengine-deepseek-v4-format-canary-attempt-20260808.jsonl`;
- `artifacts/volcengine-deepseek-v4-format-canary-20260808.json`.

The diagnostic completed all `8/8` calls with zero retries and passed every
frozen required criterion. Both R1 and archive-bearing R2 were `4/4` JSON-
schema-, DSL-syntax-, and runtime-valid; both temperature-`1.2` calls were
valid. All responses echoed `deepseek-v4-flash-ga-260731`, finished with
`stop`, retained normalized-unavailable cache/fingerprint telemetry, and stayed
within the 256-token ceiling (maximum actual output: 30 tokens). The source
manifest was stable, and no credential or candidate expression was persisted.

### Post-diagnostic development-route decision — 2026-08-08

After the required stop and review, the Volcengine route was selected for Gate
C and, only if Gate C was clean, the eight-world development pilot. This was a
prospective provider-selection amendment made before either seed-`2000` Gate C
or seeds-`1001`--`1008` pilot outcomes were observed. It changed no arm,
temperature, world, threshold, budget, or candidate handling rule.

Both runners must enforce the same route contract before and during execution:

- exact normalized endpoint hash for
  `https://ark.cn-beijing.volces.com/api/plan/v3`;
- request alias `deepseek-v4-flash` and exact response alias
  `deepseek-v4-flash-ga-260731` on every call;
- input/output usage, one request, `stop`, disabled reasoning, and the 256-token
  cap on every call;
- cache hit/miss and `system_fingerprint` remain unavailable after adapter
  normalization on every call; they are not synthesized as zero or a fake ID;
- any endpoint, alias, partial telemetry, unexpected telemetry, retry, finish,
  output-cap, or reasoning-mode drift fails closed.

The missing fingerprint means backend continuity during a long run cannot be
proved as strongly as on the official endpoint. Exact endpoint and GA alias
improve identity resolution, but all resulting Gate C and pilot artifacts
remain development-only/non-evidence and must carry this provenance caveat.
The primary arm comparison remains call-matched; reported input/output tokens
still support the frozen resource sensitivity even without cache attribution.

The historical development paths are:

- Gate C config: `configs/development-gate-volcengine.json`;
- Gate C ledger/result/analysis:
  `artifacts/volcengine-deepseek-v4-gate-c-attempt-20260808.jsonl`,
  `artifacts/volcengine-deepseek-v4-development-gate-c-20260808.json`, and
  `artifacts/volcengine-deepseek-v4-development-gate-analysis-c-20260808.json`;
- pilot config: `configs/pilot-volcengine.json`;
- pilot ledger/result/analysis:
  `artifacts/volcengine-deepseek-v4-development-pilot-attempt-20260808.jsonl`,
  `artifacts/volcengine-deepseek-v4-development-pilot-20260808.json`, and
  `artifacts/volcengine-deepseek-v4-development-pilot-analysis-20260808.json`.

Gate C completed `140/140` calls with zero retries and passed the frozen
engineering/schema readiness rule. The protocol-v1 pilot then started 235
calls and completed 234 response contracts before the 235th request ended in
`transport_error` after a duration consistent with the configured 60-second
timeout. The failed request has unknown delivery/billing status. No pilot
result or analysis path was created. The append-only v1 ledger is permanently
closed and cannot be resumed or merged into another result.

## Staged pilot transport-recovery contract v2

The post-abort review occurred before any pilot private-test result existed.
Execution protocol v2 therefore makes a prospective engineering amendment
without changing the scientific grid or decision thresholds.

The campaign contains the same 56 ordered world/arm episodes. Each episode is
an atomic generation shard:

- it begins with a new generator, policy, verifier, archive, and feedback
  state;
- it makes exactly 20 accepted calls in five rounds by four candidates;
- it has no per-request retry, call-level checkpoint, or intra-episode resume;
- it performs no private-test evaluation;
- only a fully validated, atomically committed checkpoint counts as accepted.

The checkpoint is the commit authority. A request-start or success ledger event
alone never makes a partial episode eligible. A checkpoint retains normalized
valid DSL expressions (or a fixed invalid sentinel), prompt hashes, probe/search
state, closed provider telemetry, and usage needed for deterministic offline
replay. It is permission-restricted internal state, not a public result.
Secrets, raw prompts, raw responses, malformed assistant text, raw endpoints,
and private-test labels/results are forbidden.

Only a delivery-ambiguous network-exchange failure is shard-restartable. The
entire episode attempt is abandoned, all its successful calls are operational
overhead, and a later explicitly authorized process may start the same episode
again at slot 1 with empty state. It never retries the ambiguous request. The
first fully complete attempt is accepted without inspecting its scientific
content. HTTP status, response/usage payload, local transport-contract,
endpoint/model, finish, cache/fingerprint, reasoning, or output-cap drift is
campaign-fatal rather than restartable.

Seven accepted shards in the original cyclic order create an immutable world
seal. Only the continuous prefix of sealed worlds is recoverable. Stage
boundaries are cumulative worlds 2, 4, and 8 (280, 560, and 1,120 accepted
calls). At each boundary, generation and credential-bearing processes close
before an independent offline finalizer verifies every checkpoint/seal,
replays the frozen episode state, and evaluates the boundary's private tests.
The finalizer cannot load an API key or construct a provider generator. Later
generation consumes only the immutable campaign manifest/checkpoint prefix; it
never reads a stage snapshot.

The 2- and 4-world snapshots are exploratory directional looks. Seeing them
creates explicit optional stopping and may guide only a continue/stop resource
decision. It cannot retune the method, freeze a comparator, or support a final
positive/negative claim. The unchanged final classification is eligible only
at eight worlds. Any method change after a look terminates the campaign and
demotes its existing worlds to calibration.

Recovery changes gross operations, not the accepted 1,120-call grid. Public
execution audit fields separately report accepted calls, physical request
starts, abandoned attempts, discarded successes, ambiguous deliveries, known
gross-token lower bounds, and whether gross usage is complete. If recovery is
used, actual-token matching cannot be claimed; discarded candidates never
enter scientific metrics. The missing Volcengine `system_fingerprint` remains
an independent provenance limitation across processes and stages.

## Safe execution stages

1. Run the complete local test suite without network access. **Required before
   every paid attempt.**
2. Historical one-call canary. **Completed successfully.**
3. Historical seed-1000 Gate A. **Abandoned after eight successes and the ninth
   payload error.**
4. Historical seed-1000 Gate B. **Completed 140/140 transports, but failed the
   multi-round candidate-format interface; core result indeterminate.**
5. Historical 128-token format canary v1. **Completed 8/8 transports but failed
   the strict engineering-format gate; core result indeterminate.**
6. Amended official-endpoint 256-token format canary v2. **Completed 8/8
   transports and passed all envelope/provenance checks, but failed the strict
   content gate at 7/8; core result indeterminate.**
7. Separately labeled Volcengine cross-route eight-call diagnostic. **Completed
   8/8 and passed; post-result review selected this route for the remaining
   development sequence.**
8. Fresh seed-2000 Gate C completed `140/140`, passed its operational review,
   and authorized the development pilot. **Completed.**
9. Historical protocol-v1 pilot stopped at request 235 on one transport error
   after 234 successful response contracts. It produced no result or
   private-test outcome and is permanently excluded. **Abandoned.**
10. Initialize a fresh protocol-v2 staged campaign. Commit 20-call shards and
    140-call world seals in the original order; observe cumulative offline
    snapshots at 2 and 4 worlds only as directional/resource decision points.
11. If the frozen campaign continues, complete all eight worlds and apply the
    unchanged preliminary `positive`, `negative`, or `indeterminate`
    classification. Stop and discuss; do not automatically begin confirmation.

No live call is implied by selecting or configuring an adapter. Networked
execution remains an explicit action because it consumes an external API
budget.
