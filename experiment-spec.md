# Experiment Specification: Adaptive Entropy Scheduling for Verifiable Hypothesis Search

**Status:** Frozen development protocol v0.3 (2026-08-08 engineering amendment)

**Date:** 2026-08-08

**Intended artifact:** Short validation preprint and reproducible proof-of-mechanism repository

## 1. Decision Summary

This experiment tests one narrow question:

> Under matched inference and verification budgets, does verifier-feedback-controlled sampling-temperature scheduling improve verifiable hypothesis search over static, open-loop, cyclic, and multi-temperature schedules?

The experiment uses procedurally generated symbolic worlds with programmatic ground truth. It is a mechanism study, not a benchmark of real scientific discovery.

The main treatment is an explicit controller that lowers sampling temperature after verified progress and raises it after search stagnation. The language model does not decide the temperature itself.

## 2. Claim Boundary

### 2.1 Claims the experiment may support

- Sampling temperature changes realized structural or behavioral diversity in this task.
- Verifier feedback can be used to control exploration intensity.
- Under a fixed budget, adaptive scheduling can improve held-out rule recovery relative to specified baselines.
- The benefit, if present, is attributable to feedback-controlled scheduling rather than additional model calls or verifier access.

### 2.2 Claims the experiment cannot support

- Neural noise causes human creativity or scientific insight.
- Temperature is equivalent to biological, thermodynamic, token, semantic, or search-tree entropy.
- The method performs real biological, chemical, materials, or mathematical discovery.
- The method enables AGI or recursive self-improvement.
- Transformers cannot achieve AGI without this mechanism.
- The method is universally superior to existing AI Scientist systems.

Throughout this experiment, `sampling temperature` is the controlled variable and `realized candidate diversity` is the measured manipulation. The phrase `entropy scheduling` is shorthand, not a claim that these quantities are identical.

## 3. Research Questions and Preregistered Hypotheses

### RQ1: Manipulation validity

Does increasing sampling temperature measurably increase candidate diversity?

**H1:** Fixed-high produces more unique canonical structures **and** more unique
full-domain behaviors per planned generation call than fixed-low, aggregated
across the development worlds. Both strict inequalities (`H > L`) are required.
Rates conditional only on valid candidates are descriptive and cannot rescue a
failed per-planned-call manipulation. If H1 is not supported, temperature is
not a validated exploration proxy in this setup and the experiment is
indeterminate for the scheduling hypothesis.

### RQ2: Adaptive search performance

Does verifier-driven scheduling improve final hidden-test performance under matched budgets?

**H2:** Adaptive scheduling improves mean final-selected hidden-test accuracy over a comparator selected on development worlds and frozen before the confirmatory run.

### RQ3: Feedback necessity

Is feedback control useful beyond a predetermined high/low schedule?

**H3:** Adaptive scheduling improves final-selected hidden-test accuracy over fixed-cycle scheduling. If it does not, the experiment does not support the necessity of verifier-driven control.

### RQ4: Increment beyond multi-temperature exploration

Does adaptive scheduling add value beyond simultaneous multi-temperature search?

**H4:** Adaptive scheduling improves final-selected hidden-test accuracy or cost-normalized solved-world rate over the multi-temperature exchange baseline. If it does not, the result must be positioned as a limited schedule comparison rather than a novel alternative to recent multi-temperature hypothesis search.

## 4. Procedural Micro-Science World

### 4.1 Input domain

Each world defines an unknown integer-valued law over:

$$
D = \{-2,-1,0,1,2\}^3
$$

The three input variables are `x1`, `x2`, and `x3`. The full finite domain contains 125 input tuples, allowing exact behavioral evaluation.

### 4.2 Expression grammar

Hidden laws and candidate hypotheses use a bounded S-expression DSL:

```text
E ::= (var x1) | (var x2) | (var x3)
   | (const -3) | (const -2) | (const -1) | (const 0)
   | (const 1) | (const 2) | (const 3)
   | (add E E)
   | (sub E E)
   | (mul E E)
   | (neg E)
   | (ite P E E)

P ::= (gt E E)
   | (eq E E)
```

Frozen constraints:

- Maximum AST depth: 5.
- Maximum AST node count: 31.
- Output must be an integer with absolute value at most 100 over the full domain.
- Each hidden law must use at least two input variables.
- Each hidden law must contain at least two operator types.
- Constant functions and direct identity functions are rejected.
- Difficulty tiers use hidden-law depths 3, 4, and 5 in approximately equal proportions.
- `add` and `mul` children are canonically ordered.
- Canonical AST hashes identify exact structural duplicates.
- Full-domain behavior vectors identify behaviorally equivalent expressions.

The grammar, depth, node-count, and output-bound rules apply to every
candidate. The two-variable, two-operator, non-constant, and non-identity rules
apply only to hidden-law generation; they are not extra candidate-validity
requirements.

World generation uses only a frozen seed and generator version. Formal-world seeds are not used during prompt or controller development.

### 4.3 Data split

For each world, non-overlapping points are sampled from the full domain:

- `X_train`: 12 labeled examples visible from round 1.
- `X_probe`: 12 private verifier points used for iterative feedback.
- `X_test`: 64 private test points used only after the generation barrier for
  a predeclared observation stage has been durably committed. Historical
  monolithic protocol v1 had one 1,120-call barrier; staged execution protocol
  v2 has cumulative barriers at 2, 4, and 8 worlds.

Labels are deterministic and noise-free in this protocol. Neither the hidden
law nor probe/test labels appear in the model prompt except when the verifier
deliberately releases a probe counterexample.

### 4.4 Contamination control

- Laws and splits are generated at runtime after model training.
- Prompts have no web or literature access.
- Test labels are never used for candidate selection, temperature control, or prompt construction.
- Development and confirmatory seeds are disjoint and stored in separate frozen config files.
- Seed `1000`, already exposed by the provider canary, historical one-world
  gates, and the multi-round format canaries, is retired from comparator
  selection. The fresh one-world Gate C uses seed `2000`, depth 3. The
  eight-world development pilot uses seeds `1001` through `1008`; all used or
  inspected development seeds are retained in
  `configs/development-seed-registry.json` and excluded from confirmation.
- The provider adapter is treatment-blind: it receives the prompt, current
  sampling temperature, output-token cap, and a shared call seed, but no arm ID,
  schedule name, hidden law, split seed, or private world identifier.
- When the provider supports seeding, the same call-seed sequence is reused
  across arms (and across worlds) so arm identity does not introduce a second
  randomness treatment. The frozen base seed is `1729` and is independent of
  config, world, and arm identifiers; unsupported seeding is logged explicitly.
- Provider-side thinking/reasoning modes that suppress or reinterpret
  temperature are disabled. A provider/model combination that cannot expose a
  genuine sampling-temperature control is ineligible for the primary test.
- The configured model identifier and the model identifier echoed by every API
  response are logged. New paid gates require exact compliance with the
  provider capability contract frozen before that attempt: a stable non-empty
  `system_fingerprint` when the route reports it, or normalized-unavailable on
  every call when the route was pre-audited as not reporting it. The latter has
  weaker backend-continuity provenance and cannot be represented as equivalent
  to a stable fingerprint. Neither an echoed movable alias nor a fingerprint is
  described as a permanently frozen public snapshot.
- Credentials are loaded from a local environment or an explicitly supplied
  environment file. They are never copied into configs, summaries, artifacts,
  prompts, or version control.
- Serial arm order is counterbalanced against provider-time drift with the
  frozen cyclic base row `[L, H, M, MTX, E, A, C]`. World `i` uses a rotation
  by `i mod 7`, so every arm occupies every serial position once per complete
  seven-world block. The realized order is recorded for each world.
- Historical execution protocol v1 was globally two-phase: every configured
  generation call across all worlds and arms had to finish before any selected
  candidate was evaluated on the test split. It ended before that barrier and
  exposed no private-test result. Staged execution protocol v2 instead freezes
  cumulative barriers at 2, 4, and 8 worlds. Every generation shard required
  by a boundary must be durably sealed before any test evaluation for that
  boundary. Later generation never reads a snapshot artifact, and test values
  never enter prompts, archives, policies, provider failure handling, or
  progress logs. Human continuation after an interim look is disclosed as
  optional stopping and keeps the whole campaign development-only.
- Every paid operational attempt begins with a canonical manifest of source,
  configs, tests, protocol documents, Python environment, and Git HEAD. An
  append-only fsynced ledger records request starts, successes, and
  failed/ambiguous outcomes. Interrupted attempts are abandoned, never merged
  through an unsafe pseudo-resume. The ledger and result artifact never retain
  an API key, raw prompt, or raw provider-response content.

## 5. Candidate Representation and Validation

### 5.1 Model output

Each model call returns exactly one candidate AST through this exact wire
contract: one JSON object containing only the key `expression`, whose value is
one DSL string. Extra keys, a non-string expression, arrays/nested ASTs,
null/empty content, invalid JSON, or any other shape are candidate-format
failures. Provider JSON-object mode does not by itself enforce this field
schema, so the prompt states the exact contract and the adapter reports a
closed `candidate_format` category. A fallback parser may reject an expression
that satisfies the JSON contract but violates the DSL; syntax validity is then
reported separately.

Malformed, out-of-grammar, out-of-bounds, or runtime-failing candidates receive probe score `0.0` and are excluded from the valid archive. If a round contains no valid candidate, the archive remains unchanged and the adaptive controller observes no improvement.

Example output:

```json
{
  "expression": "(add (mul (var x1) (var x2)) (neg (var x3)))"
}
```

Free-form rationale and every other extra field are excluded from the primary
experiment to reduce token variance and prevent verbal persuasiveness from
affecting validation. The durable record stores the normalized format category
and parsed candidate expression, not the raw prompt or raw response content.

The candidate-schema gate is frozen at least `0.90` adherence over all
planned calls and at least `0.80` within every arm. Missing planned responses
count against these denominators. Falling below either threshold is an
interface/engineering failure and yields `indeterminate`, not evidence against
the scheduling mechanism.

### 5.2 Programmatic verifier

The verifier:

1. parses and validates the AST;
2. executes it in a sandboxed interpreter;
3. computes accuracy on `X_probe`;
4. records syntax, runtime, depth, and bounds failures;
5. returns candidate scores and releases at most two previously undisclosed
   counterexamples from `X_probe` per round.

No LLM judge is used for validity.

### 5.3 Archive and final selection

After each round, the prompt includes a bounded archive of the four
highest-ranked candidates seen so far, their probe scores, and released
counterexamples. Every archived expression is rendered as a DSL string; an AST
tuple/list is never serialized into the prompt as a nested JSON array.

Final selection is frozen as:

1. highest probe accuracy;
2. lower AST node count;
3. lexicographically smaller canonical hash.

`X_test` is never consulted in this selection.

## 6. Episode Protocol

Each arm runs five rounds. Each round makes four independent API calls, each producing one AST.

Therefore every arm receives exactly:

- 5 rounds;
- 4 candidate calls per round;
- 20 candidate calls per world;
- 20 candidate hypotheses per world;
- the same probe set;
- the same maximum number of released counterexamples;
- the same archive capacity and history-window rule.

Round procedure:

```text
for round in 1..5:
    construct the frozen prompt from train data, archive, and released counterexamples
    generate 4 independent candidates according to the arm's temperature policy
    parse and score all candidates with the programmatic verifier
    update the archive using probe score and frozen tie-breaking
    release at most 2 new counterexamples against the updated global archive elite
    update the arm state, if the arm has state

select one final candidate using probe data only
evaluate it once on X_test without feedback
```

No arm may stop early.

### 6.1 Eight-call multi-round format canary

Historical format canary v1 used a 128-token ceiling. It completed all `8/8`
transport calls with zero retries but failed the strict gate: one R1 response
was JSON-schema-adherent but invalid DSL, and the temperature-`1.2` R1 response
reached exactly 128 output tokens with a non-`stop` finish and invalid JSON.
All four archive-bearing R2 responses were JSON-schema-, syntax-, and
runtime-valid. V1 is preserved as an engineering failure and does not decide
the scheduling hypothesis.

The 2026-08-08 protocol amendment uses DeepSeek's
[official JSON Output guidance](https://api-docs.deepseek.com/guides/json_mode/),
which advises setting `max_tokens` reasonably to reduce incomplete JSON, plus
the directly observed v1 cap hit. Before any core Gate C or development-pilot
outcome was observed, the per-call ceiling was uniformly frozen at `256` for
format canary v2, Gate C, the development pilot, and any prospective
confirmatory run. This is a prospective engineering calibration. It changes no
outcome threshold or treatment: the strict `8/8` rule, exact JSON/DSL contract,
syntax/runtime checks, model/finish/usage/cache/fingerprint checks,
thinking-disabled mode, and no-retry/no-resume rules remain unchanged.
The historical one-request provider canary also remains recorded under its
original 128-token ceiling; the amendment does not rewrite prior protocols or
artifacts.

Before another full one-world gate, the repaired multi-round interface must
pass development-only format canary v2 using retired seed `1000`, hidden-law
depth 3, and MTX only:

- 2 rounds × 4 candidates = exactly 8 provider calls;
- per-round temperatures `[0.2, 0.7, 0.7, 1.2]`;
- archive capacity 4, at most 2 counterexamples released per round;
- 256 maximum output tokens per call and provider thinking disabled;
- no retry and no resume.

It passes only if all `8/8` calls echo the exact requested model, finish with
`stop`, report complete usage, satisfy
`prompt_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens`, and share
one stable non-empty `system_fingerprint`. Every candidate in both round 1 and
round 2 must report `candidate_format=json_expression` and pass DSL syntax and
runtime validation. An envelope, usage, identity, fingerprint, format, syntax,
or runtime failure stops the sequence as engineering `indeterminate`.

Official-endpoint v2 completed all `8/8` calls with zero retries and passed the
model, `stop`, usage/cache, output-cap, reasoning, and stable-fingerprint
requirements. Candidate content was `4/4` valid in R1 and `3/4` in R2. The only
failure was the temperature-`1.2` R2 slot: the provider returned a non-empty
string in the exact outer JSON `expression` schema, but the frozen parser
classified it as `parse_or_grammar`; syntax and runtime validity were false.
That call finished with `stop` after 16 output tokens, so v2 rules out the
historical 128-token truncation mechanism for this failure. V2 failed the
unchanged `8/8` engineering gate and does not decide the scheduling hypothesis.

### 6.2 Pre-observation cross-route diagnostic amendment — 2026-08-08

After the official-endpoint v2 failure and before any eight-call outcome on an
alternate provider route, one non-retained compatibility request established a
Volcengine Agent Plan contract. This is a provider comparison diagnostic, not a
retroactive relaxation or a protocol-equivalent replacement for Gate 2.

The diagnostic reuses exactly the Section 6.1 world, prompts, two-round MTX
schedule, 256-token cap, thinking-disabled setting, call count, no-retry/no-
resume rule, and no-private-test rule. Its candidate gate is unchanged: all
`8/8` calls must contain the exact JSON `expression` field and pass the same
frozen DSL syntax and runtime checks.

Only provider telemetry differs, based on the one compatibility request made
before the eight-call result:

- normalized base URL must exactly equal
  `https://ark.cn-beijing.volces.com/api/plan/v3`; preflight checks it before
  any paid call and the artifact retains only its SHA-256;
- request alias `deepseek-v4-flash` must return exact alias
  `deepseek-v4-flash-ga-260731` on every call;
- input and output usage remain required;
- prompt-cache hit and miss values were unavailable after adapter normalization
  and must remain unavailable on every call; values are never synthesized;
- `system_fingerprint` was likewise unavailable after adapter normalization and
  must remain unavailable on every call;
- any partial/unexpected telemetry value or alias drift fails closed.

The adapter maps both wire-level omission and explicit JSON `null` to `None`,
so this diagnostic does not claim to distinguish those two wire encodings.

The config is `configs/format-canary-volcengine.json`, and the new result and
ledger paths contain the `volcengine-deepseek-v4` label. The capability gaps
must be explicit in the artifact. The diagnostic completed `8/8` calls with
zero retries and passed every required condition: R1 and R2 were both `4/4`
JSON-expression, DSL-syntax-, and runtime-valid, including both temperature-
`1.2` calls. The maximum actual output was 30 tokens. It remains an operational
non-evidence result on a retired calibration world.

### 6.3 Post-diagnostic development provider freeze — 2026-08-08

After stopping and reviewing the passed cross-route diagnostic, the remaining
development sequence selects the same Volcengine Agent Plan route for Gate C
and, only after a clean Gate C, the eight-world pilot. This provider amendment
is frozen before any seed-`2000` Gate C or seeds-`1001`--`1008` pilot outcome.
It changes no world, arm, prompt, temperature, controller, budget, format
threshold, manipulation rule, comparator, or performance margin.

Gate C and the pilot must enforce the route contract prospectively:

- request alias `deepseek-v4-flash`, exact response alias
  `deepseek-v4-flash-ga-260731`, and the SHA-256-bound normalized endpoint from
  Section 6.2;
- exactly one request per planned call, no retry/resume, `finish_reason=stop`,
  input/output usage present, output at most 256 tokens, provider seed omitted,
  and reasoning disabled;
- cache hit/miss and `system_fingerprint` remain `None` after adapter
  normalization on every call; a value appearing on either field is contract
  drift, not extra evidence, and fails closed;
- no zero cache values or synthetic fingerprint may be invented.

The missing fingerprint weakens backend-continuity provenance: exact endpoint
and GA response alias are auditable, but an unannounced backend change that
preserves both may be undetectable. Gate C and pilot therefore remain
development-only/non-evidence, and this limitation must accompany any result.
It does not alter the call-matched primary comparison because all arms share the
same frozen route and cyclic order, while input/output token totals remain
available for resource sensitivity.

Gate C uses `configs/development-gate-volcengine.json` and runs seed `2000`,
depth 3, all seven arms, and five rounds by four candidates: exactly 140 calls
and a 35,840-token output ceiling. It remains an operational, non-inferential
gate and applies the overall and per-arm schema thresholds in Section 5.1. Its
H-versus-L contrast is reported as a one-world diagnostic, but the manipulation
stopping rule is evaluated only after pooling the eight frozen development
worlds; one noisy Gate C contrast does not by itself block the pilot when
engineering and schema checks are clean.

Gate C completed `140/140` calls with zero retries, `140/140` candidate-schema
adherence, and `138/140` syntax/runtime-valid candidates. Its development-pilot
readiness check passed. H exceeded L by `+0.40` unique canonical and `+0.35`
unique behavioral candidates per planned call on that diagnostic world; E,
M, and MTX each had final-test accuracy `1.0`, so Gate C did not decide the
performance hypothesis.

The ensuing protocol-v1 pilot used `configs/pilot-volcengine.json`. It started
235 logical requests and obtained 234 audited responses before request 235
ended in `transport_error`; no result artifact or private-test evaluation was
produced. The failure occurred inside world seed `1002`, arm A, after 14 of its
20 calls had completed. Because the append-only ledger intentionally contains
no candidate programs, probe results, archive, or policy state, neither those
14 calls nor the earlier 11 complete episodes can be reconstructed or merged
into another scientific result. The attempt is permanently abandoned.

### 6.4 Pre-private-test staged execution amendment — 2026-08-08

After the protocol-v1 transport abort and before any eight-world private-test
outcome was observed, execution protocol v2 changes only transaction,
recovery, and observation boundaries. It does not change any world, arm,
prompt, temperature, controller, call opportunity, output ceiling, candidate
handling rule, schema threshold, manipulation inequality, comparator set, or
performance margin.

There is no scientific search state across world/arm pairs: each pair creates a
fresh generator wrapper, policy, verifier, archive, and counterexample state.
The minimum recoverable transaction is therefore one complete 20-call
world-by-arm episode. State does evolve within those 20 calls, so a failed
episode is never resumed at a call or round boundary.

Protocol v2 freezes these rules:

- the 56 world/arm episodes retain their original global run indices and the
  cyclic arm order defined in Section 4.4;
- a generation shard starts at round 1 with empty episode state, makes exactly
  20 accepted one-request calls, performs no private-test evaluation, and is
  committed atomically only after its entire contract validates;
- seven accepted shards form an immutable 140-call world seal. A cumulative
  snapshot may use only a contiguous prefix of sealed worlds;
- the predeclared cumulative boundaries are `S1=2 worlds/280 calls`,
  `S2=4 worlds/560 calls`, and `S3=8 worlds/1,120 calls`. S1 adds seeds
  1001--1002, S2 adds 1003--1004, and S3 adds 1005--1008;
- all new generation required for a boundary is sealed before that boundary's
  test split is evaluated. Snapshot/finalization is offline and cannot load a
  provider credential or construct a model generator;
- a delivery-ambiguous network-transport failure abandons the current shard in
  full. A later explicit resume may start a new attempt for that same shard
  from empty episode state. There is no automatic per-request retry, and the
  first fully completed attempt is accepted without inspecting candidate or
  probe quality;
- HTTP status, response/usage payload, endpoint/model alias, finish,
  reasoning, output-cap, or provider capability-contract drift is
  campaign-fatal rather than restartable;
- committed checkpoints contain only normalized valid DSL expressions or a
  fixed invalid sentinel plus replay/accounting metadata. They never contain a
  raw prompt, raw provider response, malformed assistant text, credential, raw
  endpoint, or test label/result. Public snapshots also remove the normalized
  candidate expressions;
- a campaign manifest binds the complete 56-run plan, config/source manifest,
  provider contract, legacy-attempt exclusion record, and checkpoint hash chain. Any
  drift ends that campaign; an observed stage can never be followed by method
  retuning inside the same campaign.

Replacement affects operational cost reporting, not the accepted scientific
grid. The accepted grid still contains exactly 20 calls per world/arm. Every
abandoned shard, discarded successful response, and delivery-ambiguous request
is disclosed separately. Known gross tokens form only a lower bound when a
delivery-ambiguous request has no usage response; any recovery use forbids an
actual-token-matched claim and requires resource sensitivity. Discarded shard
candidates never enter schema, diversity, accuracy, archive, manipulation, or
comparator estimates.

The 2- and 4-world snapshots are predeclared descriptive looks, not completed
pilot classifications. They report `promising_signal` for a reference delta
of at least `+0.05`, `unfavorable_signal` for a delta at most `0`, and
`weak_signal` between those values only when the current engineering, schema,
and H/L manipulation diagnostics are interpretable. Otherwise they report the
specific engineering/schema/manipulation reason for non-interpretability.
These terms have scope `exploratory_development_interim`; no interim comparator
is frozen and no interim positive/negative claim is allowed. The unchanged
eight-world rule in Section 10.2 is the only development classification.

The worlds are execution-independent deterministic seed realizations, but they
are not literally identically distributed: depths are stratified and provider
time/backend drift may differ across stages. Cumulative looks are overlapping,
not independent replications. Four disjoint two-world batches may be shown for
heterogeneity diagnostics, but they are not votes or separate confirmations.

## 7. Experimental Arms

The main experiment contains **six baseline arms plus one proposed arm, seven arms total**.

| ID | Arm | Temperature policy | Purpose |
|---|---|---|---|
| L | Fixed-Low | `0.2` for all rounds | Near-deterministic exploitation |
| M | Fixed-Mid | `0.7` for all rounds | Tests whether a static intermediate regime is sufficient |
| H | Fixed-High | `1.2` for all rounds | Unconverged exploration |
| A | Annealing | `[1.2, 0.95, 0.7, 0.45, 0.2]` | Open-loop exploration followed by convergence |
| C | Fixed-Cycle | `[1.2, 0.2, 1.2, 0.2, 0.2]` | Cyclic scheduling without feedback |
| MTX | Multi-Temperature Exchange | four calls per round at `[0.2, 0.7, 0.7, 1.2]` | Simultaneous multi-temperature search with elite exchange |
| E | Adaptive | verifier-controlled, bounded to `[0.2, 1.2]` | Proposed feedback controller |

### 7.1 Adaptive controller

The proposed controller is deterministic given verifier scores:

```text
temperature = 1.0
best_score = 0.0

for round in 1..5:
    generate 4 candidates at temperature
    round_best = max(probe_score(candidate))

    if round_best > best_score:
        best_score = round_best
        temperature = max(0.2, temperature - 0.2)
    else:
        temperature = min(1.2, temperature + 0.3)
```

A tie counts as no improvement. The controller may not inspect natural-language rationales, test labels, or task identity.

### 7.2 Multi-temperature exchange baseline

MTX is inspired by multi-temperature and parallel-tempering approaches but is not described as formal parallel tempering.

- Four candidate slots use temperatures `[0.2, 0.7, 0.7, 1.2]` every round.
- Each temperature stream retains its own best candidate.
- The global probe-score elite is copied into each stream's next prompt as a shared stepping stone.
- Each stream's next-round context contains its own best, the global elite, and
  then the strongest remaining global-archive members, with duplicates removed
  and the total context still capped at four candidates.
- All streams receive the same released counterexamples.
- Total calls, candidates, verifier evaluations, and feedback are identical to other arms.

If an official implementation of a directly comparable multi-temperature scientific-search method becomes available, reproducing it is a planned secondary experiment rather than silently treating MTX as equivalent.

The direct related-work anchor is [Towards Diverse Scientific Hypothesis Search with Large Language Models](https://arxiv.org/abs/2606.10587).

## 8. Budget Matching

The primary budget design is **ex-ante opportunity accounting**. Per arm and
world:

- API calls: exactly 20.
- Maximum output tokens: 256 per call.
- Candidate count: exactly 20.
- Probe evaluations: exactly 240 candidate-point executions.
- Feedback rounds: exactly 5.
- Counterexamples released: at most 10, with an identical release rule.
- Model identifier, backend attempt, system prompt, task prompt schema, and
  context truncation: identical.

For every run, record billed input tokens, output tokens, cache-hit/cache-miss
input tokens, latency, retries, and normalized errors. The target relative range
in mean realized billed tokens across arms is at most 2%. This is a diagnostic,
not the definition of the primary matched design: realized input length can be
downstream of candidate validity, archive content, and treatment. If the range
exceeds 2%, a prespecified resource/cost sensitivity analysis is required and
the report may not claim the run was `actual-token matched`. The exceedance does
not by itself invalidate the call-matched intention-to-treat comparison, and
realized tokens are not added post hoc as a primary-outcome covariate.

The frozen sensitivity is descriptive and Pareto-based. Compare E with `B*`
on mean billed tokens per call, report their token ratio, and use the cheapest
member if multiple comparators tie for `B*` on accuracy. If E has higher
accuracy without higher realized tokens, the direction is resource-robust; if
E has no higher accuracy and no lower realized tokens, E is resource-dominated;
all other cases are an unresolved accuracy/resource tradeoff. This sensitivity
qualifies but never silently changes the primary call-matched classification.

A run without actual billed-token and retry metadata may be used as a plumbing
or development smoke test, but it cannot be marked as confirmatory evidence for
the matched-budget claim.

Wall-clock time is reported but is not a primary budget because provider-side scheduling is not controlled.

## 9. Metrics

### 9.1 Primary endpoint

`final_selected_test_accuracy`: accuracy of the probe-selected final candidate on 64 private test points.

### 9.2 Confirmatory secondary endpoint

`world_solved`: `final_selected_test_accuracy == 1.0`. With 64 test points, this is exact held-out behavioral recovery.

### 9.3 Search-efficiency endpoints

- Area under the best-probe-score-by-round curve.
- Round of first behaviorally correct candidate.
- Solved worlds per 1,000 billed tokens.
- Total candidates generated before the first correct candidate.

### 9.4 Manipulation and mechanism endpoints

- Pairwise normalized AST tree-edit distance.
- Full-domain behavioral disagreement between candidates.
- Number of canonical structural and behavioral clusters.
- Syntax-valid rate.
- Exact candidate-schema adherence overall and by arm.
- Closed `candidate_format` distribution overall, by arm, and by round.
- Parser and runtime failure rate.
- Correlation between assigned temperature and realized diversity.
- Temperature trajectory for E.

This single-law task does not treat multiple equivalent candidates as multiple scientific discoveries. Candidate diversity is a mechanism diagnostic, not a claim of real-world novelty.

## 10. Development, Sample Size, and Statistics

### 10.1 Development stage

- Eight development worlds.
- Used for the frozen preliminary decision, manipulation check, difficulty
  diagnostics, and selection of the strongest preregistered comparator; paid
  pilot outcomes may not retune prompts, policies, thresholds, or worlds.
- Development results are clearly separated from confirmatory results.

Historical operational attempts do not enter the eight-world estimate.
Attempt A on 2026-08-04 completed eight calls, then raised a response-payload
error on the ninth; it is permanently abandoned. Attempt B completed all
`140/140` transports, but its candidate format collapsed after round 1 (only
`36/140` schema-adherent and `35/140` valid DSL candidates). Attempt B is an
interface failure and leaves the core hypothesis indeterminate; it is not
classified as a negative scheduling result.

Gate C later passed its operational readiness rule. The first monolithic
eight-world pilot attempt then ended at request 235 with one delivery-ambiguous
transport failure after 234 successful responses. It produced no result or
private-test outcome and is also excluded. Protocol-v2 stage snapshots are
cumulative at 2, 4, and 8 worlds. An interim stop must record
`final_classification_eligible=false`, `pilot_completion_status=stopped_early`,
and `core_hypothesis_status=not_decided`. Interim looks may govern resource
continuation but may not retune the frozen method or be described as independent
evidence.

### 10.2 Frozen development decision and stopping rule

Paid execution is sequential in the frozen global run order. Under protocol
v2, only a delivery-ambiguous network-transport failure may abandon and restart
one complete 20-call shard under the predeclared recovery policy. Any other
engineering, response-envelope, usage, provider capability-contract, or
format-gate failure produces `indeterminate` and stops. A provider's
pre-frozen `capability_missing` state is not itself a failure, but any drift
from it is. After a clean format canary and Gate C, the complete eight-world
development pilot must satisfy H1's two strict per-planned-call manipulation
inequalities. Failure produces `indeterminate` and stops before interpreting
E's final performance.

If the interface and manipulation checks pass, choose `B*`, the strongest
frozen nonadaptive comparator by mean development hidden-test accuracy, from
`M`, `A`, `C`, and `MTX`. This set explicitly includes both the fixed-cycle and
multi-temperature baselines. Define:

$$
\Delta_{dev} = \operatorname{meanAccuracy}_{test}(E)
             - \operatorname{meanAccuracy}_{test}(B^*)
$$

The frozen preliminary classification is:

- `positive` if $\Delta_{dev} \ge +0.05$;
- `negative` if $\Delta_{dev} \le 0$;
- `indeterminate` (weak/inconclusive) if $0 < \Delta_{dev} < +0.05$.

These labels apply only to this task, model/backend attempt, controller, and
comparator set. In particular, `negative` is not a general rejection of
adaptive exploration or RSI-related ideas. Stop after this development-set
classification and discuss the result; do not launch a confirmatory run in the
same execution sequence.

### 10.3 Confirmatory stage

- Target: 40 new world seeds, stratified approximately equally across depths 3, 4, and 5.
- Minimum pilot release: 24 new worlds, explicitly labeled as underpowered for small effects.
- All arms use exactly the same worlds.
- A second independent generation replicate per arm/world is desirable if budget permits.
- A second model family is a replication stage, not required for the first mechanism preprint.

### 10.4 Primary comparator

The strongest comparator among `M`, `A`, `C`, and `MTX` is selected by mean
primary-endpoint performance on development worlds and frozen as `B*` before
any confirmatory run. Development test labels may select this comparator but
may not tune prompts, policies, thresholds, worlds, or the adaptive controller.

The primary confirmatory contrast is:

$$
\Delta = \operatorname{Accuracy}(E) - \operatorname{Accuracy}(B^*)
$$

Additional arm-level comparisons are reported with Holm correction. `E` versus `C` is the mechanism comparison for feedback necessity; `E` versus `MTX` is the comparison against simultaneous multi-temperature exploration.

### 10.5 Statistical reporting

- Paired world-level mean and median differences.
- Paired bootstrap 95% confidence intervals.
- Paired permutation test for the continuous primary endpoint.
- McNemar or paired permutation analysis for `world_solved`.
- Holm correction for the confirmatory family of secondary pairwise comparisons.
- Seed-level results and effect distributions, not only aggregate means.

Candidates are not treated as independent samples. The statistical unit is a world seed.

### 10.6 Minimum important effect

Before the confirmatory run, freeze one of the following as the practical threshold:

- at least `+0.05` absolute hidden-test accuracy; or
- at least `+10` percentage points in solved-world rate.

An effect smaller than the threshold may be reported but cannot support the practical-improvement claim.

## 11. Preregistered Interpretation and Failure Rules

| Observation | Interpretation |
|---|---|
| `H` does not strictly exceed `L` in both canonical and behavioral unique yield per planned call | Manipulation failed; scheduling result is indeterminate |
| Overall schema adherence is below 0.90 or any arm is below 0.80 | Interface/engineering failure; core hypothesis is indeterminate |
| `E > L/H` only | Some scheduling may help, but no evidence beyond a tuned static regime |
| `E > M` but not `A/C` | Static middle temperature is insufficient, but feedback necessity is unsupported |
| `E > C` | Supports feedback control over a predetermined cycle |
| `E > MTX` | Supports an increment beyond this multi-temperature exchange baseline |
| Diversity rises but test accuracy does not | Exploration expanded candidates without improving verified discovery |
| Syntax failures explain the difference | Formatting robustness, not hypothesis-space exploration, drove the result |
| Mean realized-token relative range exceeds 2% | Require resource/cost sensitivity and do not claim actual-token matching; primary call-matched ITT is not automatically invalidated |
| Best arm solves at least 95% of worlds | Task is likely too easy for discrimination |
| All arms remain near chance | Task is too hard or the interface/controller is broken |
| Clean development pilot has $\Delta_{dev} \ge +0.05$ | Preliminary positive for this operationalization; stop and discuss |
| Clean development pilot has $\Delta_{dev} \le 0$ | Negative for this operationalization only; stop and discuss |
| Clean development pilot has $0 < \Delta_{dev} < +0.05$ | Weak/inconclusive, classified indeterminate; stop and discuss |

Formal-world seeds are not removed after the run because they are inconvenient. Difficulty-tier analyses are prespecified and all seeds remain visible in the report.

## 12. Required Logs and Artifacts

Every run stores:

- frozen config and config hash;
- world seed, split seed, and world hash;
- generator and verifier versions;
- prompt-template version and hash, but no raw runtime prompt;
- model/API name, echoed model, and provider `system_fingerprint`;
- provider random seed when supported;
- assigned temperature;
- input/output/cache-hit/cache-miss token usage and latency;
- closed candidate-format category plus parser, schema, and runtime failures;
- candidate AST, canonical hash, node count, and behavior hash;
- probe scores and released counterexamples;
- archive state and temperature history;
- final private-test result;
- git commit and environment lock hash.

Release bundle:

- world generator;
- DSL parser and sandboxed interpreter;
- verifier;
- prompt template;
- all policies/controllers;
- frozen development and confirmatory configs;
- preregistration snapshot;
- sanitized structured ledgers and result artifacts, without raw prompts,
  raw provider-response content, API keys, or account identifiers;
- analysis notebook/script;
- figures and result tables;
- one-command reproduction instructions.

## 13. Planned Repository Layout

```text
configs/
  pilot.json
  confirmatory.template.json
src/
  world_generator.py
  dsl.py
  verifier.py
  prompts.py
  policies.py
  runner.py
  experiment.py
analysis/
  analyze.py
  figures.py
artifacts/
  .gitkeep
tests/
  test_dsl.py
  test_generator.py
  test_verifier.py
  test_policies.py
  test_runner.py
  test_experiment.py
experiment-spec.md
paper-outline.md
README.md
```

Raw prompts, raw provider-response content, API keys, account identifiers, and
authorization data are not stored in the durable experiment artifacts and must
never be committed.

## 14. Execution Gates

1. **Gate 0 — specification freeze:** DSL, policies, metrics, wire contract, and
   failure/decision rules reviewed.
2. **Gate 1 — unit correctness:** the complete local DSL/interpreter, adapter,
   provenance, and split test suite passes.
3. **Gate 2 — official-endpoint eight-call format canary v2:** completed `8/8`
   envelope/provenance checks under the amended 256-token ceiling but failed the
   unchanged candidate-content condition at `7/8`. Historical v1 and v2 remain
   failed engineering records and are never overwritten.
4. **Gate 2b — Volcengine cross-route diagnostic:** completed under the
   separately frozen provider contract and passed the same strict candidate-
   content condition at `8/8`; post-result review selected this route for the
   remaining development sequence with an explicit provenance limitation.
5. **Gate 3 — Gate C:** fresh seed 2000 completes 140 calls under that provider
   capability contract and passes overall
   `0.90` and per-arm `0.80` schema thresholds plus operational review.
6. **Gate 4 — development pilot:** run seeds 1001--1008, validate H1, select
   `B*`, assign the frozen preliminary classification, then stop and discuss.
7. **Gate 5 — preregistration freeze:** only after a separate decision to
   continue, freeze confirmatory seeds, config hashes, comparator, and analysis.
8. **Gate 6 — confirmatory run:** no test-driven retuning.
9. **Gate 7 — independent reproduction:** clean rerun from the published command.
10. **Gate 8 — preprint release:** claim wording follows the interpretation table.
