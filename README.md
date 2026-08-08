# Entropy-Cycling Hypothesis Search

This repository is developing a small, reproducible mechanism study of adaptive exploration in verifiable hypothesis search.

The immediate question is deliberately narrow:

> Under matched inference and verification budgets, does verifier-feedback-controlled sampling-temperature scheduling improve hidden-rule recovery over static, open-loop, cyclic, and multi-temperature schedules?

The first experiment uses runtime-generated symbolic worlds and a programmatic verifier. It does not claim real-world scientific discovery, human-like creativity, AGI, or recursive self-improvement.

## Current Status

- Historical concept memo: [entropy-cycling-scientific-agent-position-paper.md](entropy-cycling-scientific-agent-position-paper.md)
- Frozen development protocol: [experiment-spec.md](experiment-spec.md)
- Short-preprint structure: [paper-outline.md](paper-outline.md)
- Non-secret provider record: [provider-integration.md](provider-integration.md)
- Local DSL, worlds, verifier, policies, runner, and experiment harness: complete
- Unit/integration checks: the complete local test suite passes; the exact test
  count is intentionally not frozen while the guarded live runners are still
  being added
- Full offline plumbing check: 8 worlds × 7 arms × 20 calls = 1,120 calls completed
- External adapter: IntentWeight's DeepSeek OpenAI-compatible endpoint and
  `deepseek-v4-flash`; the historical one-call paid canary passed
- Development gate history: attempt A was abandoned after eight successes and
  a ninth-call payload error; attempt B completed all 140 transport calls but
  exposed a severe multi-round candidate-format collapse
- Format-canary history: v1 completed all eight transport calls under its
  historical 128-token ceiling, but failed the frozen format gate; its
  high-temperature R1 response reached exactly 128 output tokens without a
  `stop` finish, while all four archive-bearing R2 responses were valid
- Official-endpoint format canary v2 completed `8/8` calls under the amended
  256-token ceiling with clean envelope, usage, cache, fingerprint, and `stop`
  checks. It nevertheless failed the unchanged content gate at `7/8`: R1 was
  `4/4`, while the temperature-`1.2` R2 candidate was a JSON `expression`
  string rejected by the frozen DSL parser (`parse_or_grammar`)
- The independently frozen Volcengine Agent Plan cross-route canary then passed
  the strict candidate-content gate at `8/8` (`4/4` in both rounds), including
  both temperature-`1.2` slots, with exact response alias
  `deepseek-v4-flash-ga-260731`, zero retries, and all route-specific provider
  capability checks satisfied
- Gate C subsequently completed `140/140` calls with zero retries, `140/140`
  candidate-schema adherence, and `138/140` syntax/runtime-valid candidates.
  Its operational readiness gate passed. On that single world, H exceeded L
  by `+0.40` unique canonical and `+0.35` unique behavioral candidates per
  planned call; E tied M and MTX at final-test accuracy `1.0`, so the gate was
  correctly treated as diagnostic rather than inferential
- The first eight-world pilot attempt was then safely abandoned at logical
  request 235: 234 responses completed their frozen envelope contract and the
  next request ended in `transport_error` after approximately the 60-second
  transport timeout. No result artifact or private-test outcome was produced,
  and the partial ledger cannot reconstruct candidate/search state
- Current inference: the core scheduling hypothesis remains
  **indeterminate**, not negative. Before any pilot private-test result was
  observed, execution protocol v2 was revised to use atomic 20-call
  world-by-arm generation shards, immutable 140-call world seals, and
  cumulative 2/4/8-world snapshots. No confirmatory run has been executed

The offline plumbing output is always marked `evidence=false`. Real adapters
return a structured usage envelope with billed input/output tokens, latency,
provider request count, retry count, seed support, and provider fingerprint
when available. Primary matching is **ex-ante opportunity accounting**: equal
calls, output ceilings, verifier access, archive capacity, and feedback rules.
The frozen 2% realized-token diagnostic is reported separately. Exceeding it
requires a resource/cost sensitivity analysis and forbids an
`actual-token-matched` claim; it does not by itself invalidate the
call-matched intention-to-treat comparison. That sensitivity reports E versus
the accuracy-best comparator's billed-token ratio and their Pareto relation;
it qualifies, but does not silently rewrite, the primary classification.

## Planned Local Check

The local core intentionally begins with the Python standard library only:

```bash
python -m unittest discover -s tests -v
python -m src.experiment --offline-smoke
```

No API key is required for DSL, world-generation, verifier, policy, or smoke-run tests.

The selected provider has a one-request canary that refuses to execute unless
both a dotenv path and `--execute` are supplied. For the existing local
IntentWeight credentials, the explicit command is:

```bash
python -m src.provider_canary \
  --env-file ../IntentWeight/.env \
  --execute \
  --output artifacts/deepseek-canary.json
```

It sends exactly one Chat Completions request with thinking disabled and a
128-token output cap. That historical one-request protocol remains recorded at
128 and is not changed retroactively by the later multi-round amendment. The
artifact is non-evidence and contains no credential or raw authorization data.

The historical attempt-B one-world command was:

```bash
python -m src.development_gate \
  --env-file ../IntentWeight/.env \
  --execute \
  --attempt-ledger artifacts/deepseek-gate-attempt-20260804-b.jsonl \
  --output artifacts/deepseek-development-gate-b.json

python -m src.gate_analysis \
  --input artifacts/deepseek-development-gate-b.json \
  --output artifacts/deepseek-development-gate-analysis-b.json
```

Before call 1 it freezes a canonical source-tree manifest. Every request start,
success, or ambiguous failure is fsynced to the append-only attempt ledger. The
run is deliberately non-resumable: an interruption abandons that attempt and a
fresh run must use new paths.

Attempt A is preserved at
`artifacts/deepseek-gate-attempt-20260804.jsonl`: eight calls completed, the
ninth raised a response-payload error whose original log did not retain a
subcondition, and no result artifact was written. Candidate-format failures are
now counted as invalid experimental samples; unauditable response-envelope or
usage failures still abort the whole attempt.

Attempt B is preserved at
`artifacts/deepseek-gate-attempt-20260804-b.jsonl`, with result and analysis at
`artifacts/deepseek-development-gate-b.json` and
`artifacts/deepseek-development-gate-analysis-b.json`. All 140 transport calls
completed, but candidate-schema adherence was only `36/140`: 104 responses hit
the schema-failure sentinel, and one additional schema-adherent candidate was
invalid DSL. Round 1 was `28/28` schema- and DSL-valid; rounds 2--5 were only
`8/112` schema-valid and `7/112` DSL-valid. This round-linked collapse is an
interface failure, so attempt B does not decide the core hypothesis.

Format canary v1 is preserved at
`artifacts/deepseek-format-canary-attempt-20260808.jsonl` and
`artifacts/deepseek-format-canary-20260808.json`. It used the historical
128-token ceiling and completed `8/8` transport calls with no retry. It still
failed the strict gate: one R1 response was JSON-schema-adherent but invalid
DSL, and the temperature-`1.2` R1 response was invalid JSON after reaching
exactly 128 output tokens with a non-`stop` finish. All four archive-bearing R2
responses were JSON-schema-, syntax-, and runtime-valid. This is an engineering
result and does not decide the scheduling hypothesis.

The 2026-08-08 protocol amendment follows DeepSeek's
[official JSON Output guidance](https://api-docs.deepseek.com/guides/json_mode/),
which recommends a reasonable `max_tokens` setting to reduce incomplete JSON,
and the directly observed v1 cap hit. Before any core Gate C or development-
pilot outcome was observed, the output ceiling was uniformly frozen at `256`
for format canary v2, Gate C, the development pilot, and any prospective
confirmatory run. The ceiling change is an engineering calibration, not an
outcome-threshold adjustment: the exact `8/8` pass rule, JSON/DSL validity,
model/finish/usage/cache/fingerprint checks, thinking-disabled setting, and
no-retry/no-resume rules remain unchanged.

Format canary v2 used retired seed `1000`, depth 3, MTX only, two rounds by four
candidates (`8` calls), temperatures `[0.2, 0.7, 0.7, 1.2]` in each round,
archive capacity 4, at most two released counterexamples per round, 256 maximum
output tokens, thinking disabled, and no retry or resume. It completed all
eight official-endpoint calls and passed every envelope/provenance condition,
but failed the unchanged content gate at `7/8`. R1 was `4/4`; the only failure
was the temperature-`1.2` R2 call, whose outer JSON and `expression` field were
valid but whose non-empty expression failed the frozen parser. Its finish was
`stop` and its output was only 16 tokens, so this v2 failure was not output-cap
truncation. The artifacts are:

- `artifacts/deepseek-format-canary-attempt-20260808-v2.jsonl`;
- `artifacts/deepseek-format-canary-20260808-v2.json`.

The historical v2 invocation is recorded below. The runner used exclusive
creation and deliberately has no resume mode:

```bash
python -m src.format_canary \
  --env-file ../IntentWeight/.env \
  --execute \
  --attempt-ledger artifacts/deepseek-format-canary-attempt-20260808-v2.jsonl \
  --output artifacts/deepseek-format-canary-20260808-v2.json
```

These v2 paths are deliberately different from the historical v1 paths and
must not overwrite them. The exact candidate wire contract remains one JSON
object containing only an `expression` field whose value is a DSL string.
Archive expressions are rendered back to the model as DSL strings, not nested
JSON arrays. Durable artifacts never retain an API key, raw prompt, or raw
response content.

Before any eight-call result on the alternate route, one compatibility request
to the existing Volcengine Agent Plan subscription established the route
contract: base URL `https://ark.cn-beijing.volces.com/api/plan/v3`, request alias
`deepseek-v4-flash`, response alias
`deepseek-v4-flash-ga-260731`, with prompt-cache hit/miss and
`system_fingerprint` unavailable after adapter normalization (the adapter does
not distinguish an omitted field from explicit JSON `null`). The expression
itself was neither printed nor retained.
The separate config is `configs/format-canary-volcengine.json`. On this route,
unavailable cache/fingerprint telemetry is recorded as a provider capability
limitation, not fabricated values; both must remain normalized as unavailable
on all eight calls, while the candidate content gate remains the same strict
`8/8` JSON/DSL syntax/runtime rule. Preflight binds the exact normalized base
URL before any paid call, and the artifact records only its SHA-256. Any
endpoint, alias, or capability drift fails closed.

The completed cross-route diagnostic artifacts are:

- `artifacts/volcengine-deepseek-v4-format-canary-attempt-20260808.jsonl`;
- `artifacts/volcengine-deepseek-v4-format-canary-20260808.json`.

The diagnostic completed `8/8` provider calls with zero retries and passed every
required route, envelope, model-alias, finish, usage, temperature, archive,
JSON, DSL-syntax, and runtime condition. R1 and R2 were both `4/4`; both
temperature-`1.2` slots were valid, and the maximum actual output was 30 tokens.
The result remains non-evidence because it uses a retired calibration world and
does not evaluate the private test.

This diagnostic is not protocol-equivalent provenance to the official endpoint
because the alternate route omits cache accounting and a backend fingerprint.
After reviewing that limitation, the development sequence now freezes the same
Volcengine endpoint, request/response aliases, and normalized-unavailable
telemetry contract for Gate C and, only if Gate C is clean, the development
pilot. An exact GA response alias and endpoint hash are retained, but backend
continuity cannot be claimed as strongly as with a stable fingerprint.

The completed Volcengine Gate C invocation used process-only `VOLCENGINE_*`
credentials and exclusive paths:

```bash
python -m src.development_gate \
  --env-file /dev/null \
  --env-prefix VOLCENGINE \
  --config configs/development-gate-volcengine.json \
  --execute \
  --attempt-ledger artifacts/volcengine-deepseek-v4-gate-c-attempt-20260808.jsonl \
  --output artifacts/volcengine-deepseek-v4-development-gate-c-20260808.json

python -m src.gate_analysis \
  --input artifacts/volcengine-deepseek-v4-development-gate-c-20260808.json \
  --output artifacts/volcengine-deepseek-v4-development-gate-analysis-c-20260808.json
```

Gate C passed. The following command records the historical execution-protocol
v1 pilot attempt that was abandoned at request 235; it must not be rerun into,
resumed from, or merged with a later campaign:

```bash
python -m src.development_pilot \
  --env-file /dev/null \
  --env-prefix VOLCENGINE \
  --config configs/pilot-volcengine.json \
  --execute \
  --attempt-ledger artifacts/volcengine-deepseek-v4-development-pilot-attempt-20260808.jsonl \
  --output artifacts/volcengine-deepseek-v4-development-pilot-20260808.json

```

That v1 process started 235 logical requests and received 234 audited
responses before a delivery-ambiguous transport failure. The 234 successful
responses were all schema-adherent, but the ledger deliberately contains no
candidate programs, probe scores, archive/controller state, or private-test
results. They are therefore an engineering observation only and are excluded
from every v2 scientific estimate.

### Staged development-pilot execution v2

The scientific grid remains unchanged: eight worlds, seven arms, five rounds,
four candidates per round, 20 accepted calls per world/arm, and 1,120 accepted
calls in total. Only its execution transaction and observation boundaries
change:

- one complete world-by-arm episode is an atomic 20-call generation shard;
  there is no retry or resume inside that episode;
- seven accepted shards in the original cyclic arm order form one immutable
  140-call world seal;
- a transport-ambiguous shard is abandoned in full and may be restarted only
  as a new explicitly logged attempt from round 1 with empty episode state;
  completed shards are never regenerated or selected by scientific outcome;
- global world indices `0..7` and their original cyclic arm order are retained;
- cumulative stage boundaries are frozen at 2, 4, and 8 worlds. All generation
  for a boundary completes before that boundary's private tests are evaluated;
- the 2- and 4-world snapshots are descriptive directional looks only. They
  cannot receive the final `preliminary_positive`,
  `current_operationalization_negative`, or `indeterminate` label; the original
  classification rule is applied only after all eight worlds complete;
- observing an interim snapshot may inform a transparent continue/stop resource
  decision but cannot retune prompts, arms, policies, worlds, thresholds, or
  candidate handling. Doing so would end the campaign and demote its worlds to
  calibration data.

Internally committed checkpoints retain only normalized valid DSL expressions
or a fixed invalid sentinel, plus probe/search and provider-accounting metadata
needed for deterministic offline replay. They are not public result artifacts.
The public snapshots remove candidate expressions, raw prompts, raw responses,
credentials, and raw endpoints. Any abandoned attempt is disclosed separately
as operational overhead; it never enters schema, diversity, accuracy, archive,
or comparator estimates.

## Integrity Rules

- Development worlds and confirmatory worlds remain separate.
- Private test labels never enter prompts, controller state, or candidate selection.
- Every main arm receives the same call, output-ceiling, verifier, archive, and
  feedback opportunities; realized token use is measured separately.
- Null and negative results remain reportable.
- Secrets, account identifiers, and unsanitized provider logs must not be committed.

## Frozen Stop Point

1. Official-endpoint format canary v2 completed but failed the strict content
   gate at `7/8`; the core result remains `indeterminate`.
2. The independently labeled Volcengine cross-route format diagnostic passed
   the same strict content requirement at `8/8`. Its weaker backend provenance
   remains an explicit limitation; the post-result review nevertheless selected
   it for the development-only sequence.
3. Gate C on seed `2000` completed and passed the frozen Volcengine engineering
   and schema-readiness contract. Its one-world H/L contrast remains diagnostic.
4. The historical monolithic pilot attempt stopped at request 235 on a
   transport error without producing private-test results. It is permanently
   excluded. Run the v2 pilot in frozen 20-call shards and save cumulative
   2/4/8-world snapshots. H must
   exceed L in both unique canonical and unique behavioral candidates per
   planned call at the complete eight-world boundary; otherwise the final
   manipulation result is `indeterminate`.
5. Select the strongest frozen nonadaptive development comparator from
   `M`, `A`, `C`, and `MTX` (therefore explicitly including C and MTX). Relative
   to it, a mean hidden-test gain for E of at least `+0.05` is preliminary
   `positive`; a gain of at most `0` is `negative` for this operationalization;
   a gain strictly between `0` and `+0.05` is weak/inconclusive and therefore
   `indeterminate`.
6. At the 2- and 4-world boundaries, report only an explicitly exploratory
   directional signal and decide transparently whether to continue the frozen
   campaign. At eight worlds, stop after the unchanged development
   classification and discuss it. Do not begin a confirmatory run in the same
   sequence.
