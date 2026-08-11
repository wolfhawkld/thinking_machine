# V3 Independent Development Specification

**Status:** design-frozen template; model routes must be bound before execution
**Scope:** new development evidence only; never confirmatory evidence
**Config template:** `configs/v3-development.template.json`

**Pre-gate operational amendment (2026-08-12):** The first route-bound gate
attempt stopped during gate shard 3, slot 12, before any compatibility-screen
classification or main-grid request, because an accepted 2xx response exhausted
the 256-token cap and did not report `finish_reason=stop`. No arm-level gate
metric was inspected. The response contract had incorrectly treated every
non-`stop` finish as an accounting failure. V3 now prospectively accepts the
closed set `{stop, length}`. A `length` response consumes its original logical
slot exactly once; its returned content proceeds through the same JSON/DSL
validity rules and is never regenerated. The incomplete first gate attempt is
retained as calibration history and excluded from the restarted campaign.

**Pre-main route substitutions (2026-08-12):** The complete restarted screen
passed on DeepSeek but failed on the Volcengine GLM-5.2 route: all 80 responses
had the required JSON envelope, but only 66/80 expressions were valid under the
frozen DSL. GLM-5.2 was prospectively replaced by Volcengine Agent Plan MiniMax
M3. The subsequent MiniMax screen produced 80/80 search-valid expressions, but
the H-L differences were `-3` for unique canonical yield and `-3` for unique
behavioral yield, so the manipulation requirement and compatibility screen
failed. The main grid and private test were never started. At the user's
direction, the second route is now replaced
prospectively by Volcengine Agent Plan Kimi K3. The prior GLM and MiniMax screen
data remain calibration history only and are not pooled with the Kimi screen or
any later main-grid estimate. Seeds `2001--2012` remain untouched.

## 1. Purpose and separation from staged v2

V3 asks whether the validity-aware, novelty-aware adaptive controller E2
improves final-selected hidden-test accuracy over the preselected fixed-cycle
reference C across two model strata.

V3 is a new, independent development campaign. It does not continue, amend,
reanalyze, or replace the sealed staged-v2 S3 campaign. Seeds `1000--1008` and
all v2 outcomes are calibration history and cannot enter V3 estimates. V3 uses
gate seed `2000` and fresh development seeds `2001--2012`; all are excluded
from any later confirmation by `configs/development-seed-registry.json`.

The two planned model strata are:

1. an official DeepSeek v4 route; and
2. a Volcengine Agent Plan Kimi K3 route.

The template deliberately does not bind either route to the currently
available endpoint. After a clean route-specific canary and before the first
gate request, each stratum must freeze its exact provider, request model,
snapshot/response-model identity, sanitized endpoint and static-request hashes,
request capabilities, the one-shot transport profile
`stdlib-urllib-one-shot-v1`, and every field of the accepted-response contract.
The canary artifact hash and pass status must bind the same derived
route-contract hash. An offline live preflight recomputes that hash from the
configured generator and accepted-response contract; a null field, route swap,
or drift fails before transport is invoked. The instantiated config, source
manifest, and complete execution plan are then hashed. A missing identity,
post-gate substitution, or provider-contract drift stops the campaign as
engineering-indeterminate.

## 2. Frozen design

### 2.1 Worlds and accounting

The operational gate uses seed `2000`, depth 3, separately in each model
stratum. Gate data are calibration only and never enter development outcomes.

The development grid uses the same twelve fresh worlds in both model strata:

| Seeds | Depth |
|---|---:|
| 2001, 2004, 2007, 2010 | 3 |
| 2002, 2005, 2008, 2011 | 4 |
| 2003, 2006, 2009, 2012 | 5 |

Every model-world-arm episode has five rounds, four candidate slots per round,
and therefore 20 logical calls. It retains the v2 archive size, counterexample
release rule, 256-token output ceiling, prompts, DSL, verifier, and train/probe/
test construction unchanged. Any change requires a new protocol version and
turns data already generated under this specification into calibration data.
The main grid is:

`2 models x 12 worlds x 4 arms x 20 calls = 1,920 logical calls`.

The gate is a separate maximum of 160 logical calls. Its candidates and probe
diagnostics are never pooled into the 1,920-call development grid.

No V3 private-test result is released until all required development episodes
for both model strata are durably committed and the complete generation
barrier verifies. There are no outcome-bearing interim looks. Execution order
must be fixed and hashed before gate call 1 and cannot depend on gate or
development content. The runner sampling base seed is frozen at `1729` and is
included in every plan entry and its deterministic identity hash.

The campaign manifest is a self-contained provenance envelope: it embeds the
complete frozen config, the file-by-file source manifest, all sanitized route
contracts and canary bindings, and the complete execution plan. Its validator
recomputes the config, source-file-list, plan, and per-entry hashes before a
shard transaction identity may be derived.

### 2.2 Arms

Only four arms are run:

- **L:** fixed temperature `0.2`.
- **H:** fixed temperature `1.2`.
- **C:** the predetermined reference schedule
  `[1.2, 0.2, 1.2, 0.2, 0.2]`.
- **E2:** controller version `validity-novelty-v2`, with initial/minimum/maximum
  temperatures `1.0/0.2/1.2`, improvement step `-0.2`, stagnation step `+0.3`,
  minimum valid candidates `3` of `4`, minimum useful new behaviors `1`, and
  useful-novelty score tolerance `1/12`.

C is frozen before V3 outcomes and is the sole classification reference. No
post-outcome best-comparator selection is permitted. L and H validate the
temperature/diversity manipulation; they are not candidate references for the
primary classification.

### 2.3 Exact E2 observation and transition rule

A candidate is search-valid iff `syntax_valid && runtime_valid`. For a round,
`new behaviors` are distinct search-valid full-domain behavior hashes not seen
among search-valid candidates in earlier rounds of that episode. A new behavior
is useful iff its probe score is greater than zero and is at least
`pre_round_best_score - 1/12`.

After each round, E2 applies the following precedence exactly once:

1. fewer than three search-valid candidates: lower temperature by `0.2`
   (`low_validity`);
2. otherwise, probe best improved: lower by `0.2` (`probe_improved`);
3. otherwise, episode best probe score is `1.0`: hold (`probe_ceiling`);
4. otherwise, at least one useful new behavior appeared: hold
   (`useful_novelty`);
5. otherwise: raise by `0.3` (`stale_search`).

Every update is clipped to `[0.2, 1.2]`. The controller receives only scalar
counts, scores, and booleans. Behavior-hash sets remain runner-owned; prompts,
candidate expressions, predictions, examples, counterexamples, world identity,
and private-test information are not controller inputs. Public/durable E2
traces are restricted to the predeclared sanitized scalar whitelist.

## 3. Compatibility screen and construct diagnostics

Seed `2000` is an outcome-observed operational-calibration seed, not independent
evidence. Its gate is therefore named a **route compatibility screen** and is
evaluated independently for each model before committing the main-grid cost. A
route passes only if:

- the full gate grid completes under the frozen provider and accounting
  contract;
- overall search-valid yield is at least `0.95` per planned call;
- every arm's search-valid yield is at least `0.90` per planned call; and
- H strictly exceeds L in both unique canonical and unique behavioral yield
  per planned call.

Both routes must pass this calibration screen before any main-grid request. A
screen failure is `compatibility_screen_failed`; no main-grid performance label
is possible because the fresh development grid was not run. It is not a
negative scientific result.

In the completed twelve-world grid, the same search-valid thresholds and H/L
inequalities are recomputed as **construct diagnostics**, not post-treatment
engineering gates. Search validity is itself affected by temperature and is a
target of E2; therefore low validity can never erase an unfavorable E2 result.
Falling below a search-valid threshold produces `construct_validity_warning`.
Failure of either H>L inequality produces `manipulation_indeterminate` for an
entropy/diversity mechanism claim. In both cases the preregistered E2-versus-C
performance estimate and two-route development label are still reported.
Envelope, usage, source/provenance, accounting, generation-barrier, or private-
test execution failure instead yields `engineering_indeterminate` and no
complete-grid performance label. JSON response-schema adherence, failure codes,
and all-invalid concentration are always reported.

Difficulty seeds are never dropped, replaced, or rerun because their outcomes
or validity rates are inconvenient.

## 4. Prospective all-invalid endpoint

An all-invalid episode is one in which all 20 planned candidates fail the
search-valid definition and no final candidate exists. V3 prospectively
defines this as a primary endpoint failure:

- observed private-test accuracy remains `null`, because no candidate was
  evaluated;
- the primary intention-to-treat analysis score is exactly `0.0`;
- `world_solved` is false;
- the episode remains in every arm/model denominator; and
- the artifact reports the failure separately and labels zero as an analysis
  score, never as observed accuracy.

A named terminal-zero sensitivity also assigns `0.0`; it is frozen in advance
for audit continuity with the v2 post-hoc diagnostic. Bounds with the missing
accuracy in `[0,1]` and success-only summaries may be reported as secondary
sensitivities, but neither can replace or revise the primary zero-score rule.

Invalid assistant schema, DSL, depth/node/output-bound, or runtime content is a
scientific content result for its planned slot. It receives the frozen invalid
score/eligibility handling and is never regenerated.

An accepted HTTP response with `finish_reason=length` is handled identically:
it is a paid content result, not a transport or accounting failure. If its
truncated content is malformed, the ordinary invalid-candidate rule applies.

## 5. Request attempts and retry boundary

A logical slot prepares its prompt, policy observation, and exact request body
bytes once. All physical attempts for that slot reuse those bytes and the same
`request_body_sha256`. The maximum is three physical attempts, including the
first, each with a 120-second timeout.

The retryable set is closed:

- typed transport failures `timeout`, `dns`, `tls`, `connection_refused`,
  `connection_reset`, and `network_io`; or
- HTTP status `429` or `500--599`.

Local request/contract errors, arbitrary injected exceptions, and every other
HTTP status are fatal. The physical-attempt sequence is irrevocably assigned to
the **first durably recorded HTTP success** (HTTP 2xx). That response is not
necessarily an accepted scientific sample: response-envelope, usage, alias,
fingerprint/capability, or other provider-contract failure after the HTTP
success is campaign-fatal, not retryable. No later physical response may
replace the first durably recorded HTTP success. Assistant schema, DSL, or
runtime invalidity is committed as content and `content_retry_count` must
remain zero.

If a physical request start is durable but neither a typed outcome nor a call
checkpoint is durable, the slot is unresolved: it is not automatically retried
and the campaign stops engineering-indeterminate. This conservative crash rule
avoids silently replacing a response that may have reached the process before
it failed. A durably recorded retryable outcome authorizes the next ordinal;
exhausting all three attempts without a durable HTTP success likewise makes the
campaign engineering-indeterminate. Checkpoint replay does not start a new
physical request. Any retry or unresolved attempt makes gross usage only a
lower bound and forbids an actual-token-matched claim.

## 6. Outcomes and two-route development classification

For model stratum `m`, compute equal-world means using the primary analysis
score and define:

`delta_m = mean_score(E2, m) - mean_score(C, m)`.

The two-route summary is the equal-stratum mean
`delta_bar = (delta_DeepSeek + delta_Kimi) / 2`; a model with more tokens or
valid candidates receives no extra weight. Report both paired world-level
contrasts, both model means, the equal-stratum mean, all-invalid counts, and
uncertainty intervals. The frozen development-only classification is:

- `two_route_development_promising` iff both model-specific deltas are strictly positive
  and `delta_bar >= 0.05`;
- `two_route_nonpositive_development_signal` iff both model-specific deltas are
  `<= 0`;
- `mixed_or_small_development_signal` otherwise, including sign heterogeneity or a
  positive equal-stratum mean below `0.05`; or
- `engineering_indeterminate` only if the complete main grid cannot be validly
  committed, replayed, or evaluated under the frozen engineering/provenance
  contract.

The promising label means only: both fixed routes were directionally positive
and their equal-route mean reached five percentage points. It is not evidence
of cross-model generalization or statistical replication. The nonpositive
label is reported as "this development screen did not observe an E2 advantage,"
not as falsification or exclusion of a small effect. These labels concern this
task, E2 controller, two frozen routes, and C reference only. They do not revise
S3 and do not justify a general claim about entropy, adaptive search, or real
scientific discovery. After classification, stop and discuss; any confirmation
requires new preregistered seeds and frozen model identities.

### 6.1 Frozen statistical analysis plan

The primary replication and uncertainty unit is the procedural world (`n=12`),
not candidates, 64 test points, model-world pairs (`n=24`), or the two route
summaries. The same twelve worlds occur on both routes, so every resample keeps
both route strata and the E2/C pairing together.

For each route, world, and arm, store the integer number correct out of 64.
All-invalid episodes and selected-candidate private-test runtime failures receive
primary analysis correct-count zero while observed accuracy remains null and the
failure type is reported. Any other no-selection state contradicts the frozen
runner and is engineering-indeterminate. A partial campaign has no performance
classification and releases no private-test results.

Primary contrasts are first computed as paired integer-count differences within
each route and world, then divided by 64 and averaged over twelve worlds. There
is no floating comparison tolerance: the sign and five-point threshold use the
exact rational numerator. `delta_bar` gives each route weight one half.

Uncertainty is descriptive. A depth-stratified world-cluster bootstrap uses
100,000 replicates and RNG seed `20260809`: independently within each of depths
3, 4, and 5, resample four world indices with replacement; for every selected
world retain both routes and all arms. Report percentile 95% intervals for both
route deltas and `delta_bar`, using nearest-rank order statistics at 2.5% and
97.5%. Also report an exact two-sided paired sign-flip
randomization p-value over all `2^12=4096` world sign patterns for each route
and for the equal-route per-world contrast. These p-values are exploratory,
uncorrected, and never override the development label. Per-depth estimates
(`n=4`) and retried-slot-excluded estimates are descriptive sensitivities only.

## 7. Resource and integrity reporting

The primary design is logical-call matched within each model. Report billed
input/output tokens, retries, discarded or unresolved physical attempts, and
latency by model and arm. Realized-token comparisons are made within model, not
by pooling incomparable provider tokenizers. A greater than 2% within-model
arm range forbids an actual-token-matched claim and triggers an E2-versus-C
Pareto sensitivity; it does not silently alter the call-matched classification.

Raw prompts, provider responses, candidate expressions, credentials, raw
endpoints, private examples/labels, and candidate-linked private-test scores
must not appear in public artifacts. Manifests bind sanitized checkpoints,
world seals, provider contracts, exact model identities, request-body hashes,
attempt ledgers, the instantiated config, this specification, and source tree.

Every episode seal is computed from a deterministic replay of its 20 committed
logical-call checkpoints. Stored generation metrics and the E2 controller trace
must exactly match that replay before the seal can advance the campaign or enter
the compatibility screen. The finalized development snapshot has one fixed,
exclusive path inside the campaign directory; a production finalization cannot
inject an alternate evaluator or publish to a second path.
