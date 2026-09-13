# Supplementary Material: Context-Guided Exploration and Rule Revision (2026-09-13)

Corresponding [main text](paper-manuscript-en-20260913.md). This revision retains the earlier methods, 26 strategies and controls, and adds the completed two-feedback results and repair history in S7. It reports existing frozen evidence without conducting new experiments. This is currently a local supplement: the source files have not been packaged as a public data release, so we cannot claim that readers can fully reproduce the results through public download.

## S1. Tasks, Actions, and Validation Protocol

The input domain is \{-2, -1, 0, 1, 2\}³, containing 125 points. D0 consists of 12 points on the same coordinate plane; the remaining points are divided into 49 ordered evidence points and 64 test points. The 256-member bank is compatible with D0, and the verifier groups semantic hypotheses by their behavior over the full domain. The parent is selected by node count and canonical hash, not by the hidden target.

The program language contains x1/x2/x3, constants −3..3, neg/add/sub/mul, gt/eq, and ite. Depth is ≤5 and node count is ≤31; a classifier must be binary over the full domain. The context library contains 105 motifs: 21 affine commutative, 42 affine directional, 21 affine multiplicative, and 21 pairwise variable motifs. These are context-construction strata, not target classes.

The ten original actions apply five edit frames on each of the two operand paths (left and right) of the parent predicate: replace, add(OLD,CONTEXT), sub(OLD,CONTEXT), sub(CONTEXT,OLD), and mul(OLD,CONTEXT). Display option IDs map to the original actions through a frozen mapping. A context must be absent from the parent and appear once in the child. A valid child must agree on D0, be binary over the full domain, differ from the parent, and change evidence behavior. See the [source bindings](src/spark_lineage.py) and [prompts and strategies](src/spark_strong_k4_benchmark.py).

The four-round procedure and the strong-K4 definition remain in §2.2 of the main text because they affect interpretation of the conclusions and cannot be hidden as appendix-only details. Here we emphasize that the structured oracle returns the first mismatch location/label or MATCH; updates filter using the full response rather than a single label. A singleton or an indistinguishable evidence-equivalence state can stop the process early. K4_full_pool deduplicates by child behavior within a frozen matching pool with the same parent, frame, motif layer, and complexity; it requires at least three comparison cases, all of which must fail exact identification. It is not the set of all possible contexts. Implementations: [verifier](src/spark_compressor.py), [strong-K4](src/spark_strong_k4_scan.py), and [v2 pairing](src/spark_strong_k4_utilization_feasibility.py).

## S2. Sample Construction and Version Timeline

From the original pool of 1024 candidates, we construct 24 worlds, six in each of four strata. Each world contributes only one pair, yielding 48 arms. Strict uniqueness is imposed on the successful original action; incorrect actions are not required to have mutually distinct behavior. The two K2 arms are balanced by opportunity count; each nonconstant K4 arm has one correct action, and those actions differ, while constant K4 contributes zero. The [v2 configuration](configs/spark-strong-k4-utilization-primary-benchmark-v2.json) fixes selection, display, and the original statistical protocol. The older v1 is retained for history and is not used to reconstruct or rewrite the current results.

A separate full scan of another batch of 1024 candidates produced 19 strict worlds and 41 pairs. Within it, the joint complete-failure class for two nonconstant strategies contained 9 worlds and 22 pairs. We retained all 9 worlds and selected one pair per world by pair hash. The context strata contained 3 affine commutative, 4 affine directional, 2 pairwise variable, and 0 affine multiplicative worlds. Neither the 41 pairs nor the 22 pairs represent the corresponding number of independent worlds; these 9 worlds are not a four-stratum balanced sample. See the [search report](reviews/shortcut-challenge-1024-results-20260910.md).

The response and design order was: original non-thinking main experiment → DeepSeek thinking follow-up → nonconstant-strategy diagnostics → GLM follow-up → new-pool strategy challenge → same-nine-world reference controls → same-nine-world rule generation → contemporaneous output-cap control on 8 worlds from the original 24. Freezing each experiment before execution does not mean that every design was preregistered before the original results. Later controls do not independently increase the world count or inherit the original test power.

## S3. All Strategies on the Original 24 Worlds

There are ten fixed-action strategies, ten fixed-display-position strategies, and four public-information strategies, for 24 prespecified strategies. The final two rows are diagnostics proposed after the DeepSeek thinking run and before the GLM run. All use public information. A fixed strategy cannot succeed simultaneously on both arms when their correct actions differ; this is a structural limitation, not evidence that the 20 fixed strategies are 20 independent strong baselines.

| Strategy ID or diagnostic | own /48 | cross /48 | complete /24 |
|---|---:|---:|---:|
| fixed-semantic-00 | 14 | 14 | 0 |
| fixed-semantic-01 | 0 | 0 | 0 |
| fixed-semantic-02 | 0 | 0 | 0 |
| fixed-semantic-03 | 2 | 2 | 0 |
| fixed-semantic-04 | 12 | 12 | 0 |
| fixed-semantic-05 | 12 | 12 | 0 |
| fixed-semantic-06 | 0 | 0 | 0 |
| fixed-semantic-07 | 0 | 0 | 0 |
| fixed-semantic-08 | 1 | 1 | 0 |
| fixed-semantic-09 | 7 | 7 | 0 |
| fixed-display-position-00 | 4 | 4 | 0 |
| fixed-display-position-01 | 4 | 4 | 0 |
| fixed-display-position-02 | 7 | 7 | 0 |
| fixed-display-position-03 | 9 | 9 | 0 |
| fixed-display-position-04 | 3 | 3 | 0 |
| fixed-display-position-05 | 7 | 7 | 0 |
| fixed-display-position-06 | 2 | 2 | 0 |
| fixed-display-position-07 | 6 | 6 | 0 |
| fixed-display-position-08 | 5 | 5 | 0 |
| fixed-display-position-09 | 1 | 1 | 0 |
| first-public-K1-else-first-displayed | 9 | 2 | 2 |
| public-k1-min-node-hash | 22 | 6 | 2 |
| public-k1-min-positive-node-hash | 4 | 10 | 0 |
| public-k1-max-parent-novelty-node-hash | 29 | 2 | 8 |
| nonconstant filtering + maximum parent-behavior difference | 33 | 1 | 11 |
| nonconstant filtering + minimum nodes | 31 | 2 | 8 |

The numbers come from `all_24_frozen_policies` and `separately_labelled_nonconstant_diagnostics` in the [machine comparison](reviews/utilization-glm53-comparison-20260909.json); for the first 24 rows, cross is derived as own − signed_total. Fixed action IDs select by original action number; fixed display IDs select by position. For the remaining strategies, public-support filtering, positive-node-change handling, and tie rules follow the [implementation](src/spark_strong_k4_benchmark.py); diagnostic definitions are given in the [case record](reviews/thinking-case-audit-20260909.md). In the 9-world challenge, all 26 strategies had complete success of 0/9, but selection was conditioned on two focal strategies and must not be treated as 26 independent validations.

## S4. Per-World Results for the New Observation and Scoring Boundaries

There was one independent response per condition, using the same targets and 64 private test points. The table follows the frozen ordinal rather than sorting by performance. The total correct counts for no additional observation, new observation, and repeated old observation were 305/399/379, respectively, with denominator 576 in each case. Complete test recovery and full 125-point recovery were both 0/9 for all groups.

| World ordinal | no additional correct /64 | new observation correct /64 | repeated observation correct /64 |
|---|---:|---:|---:|
| 0 | 12 | 32 | 49 |
| 1 | 13 | 45 | 46 |
| 2 | 39 | 39 | 39 |
| 3 | 42 | 51 | 51 |
| 4 | 35 | 57 | 30 |
| 5 | 47 | 47 | 47 |
| 6 | 40 | 51 | 40 |
| 7 | 34 | 34 | 34 |
| 8 | 43 | 43 | 43 |

After the new observation, the remaining candidate counts were 189, 187, 161, 184, 86, 171, 89, 85, and 61, in order. Only 2/9 contradicted the parent; all were retained. New minus repeated gave 2 wins, 2 losses, and 5 ties, so it is not valid to show only worlds 4 and 6. The repeated points had already been seen, so this control does not match the new observation in novelty, input position, or strict information amount. See the [new-observation results](reviews/new-evidence-model-results-20260910.md) and [design](reviews/new-evidence-protocol-draft-20260910.md) for full scoring and code baselines. Programs that were valid but did not match the observation still contribute their original test accuracy, with consistency reported separately. All 27 trials in this round were valid and consistent; none was removed by consistency.

## S5. Statistics, Parameters, and Technical Record

The original primary test used a one-sided exact sign test on the net utilization direction among non-tie worlds, with α=0.05. Original non-thinking produced 0 favorable, 1 adverse, and 23 tie outcomes, giving p=1. When invalid content was received, the entire world was recorded as a tie at the primary endpoint and as a complete failure. Transport failures or missing responses made the entire round unevaluable rather than contributing a partial denominator. All 48 original responses were valid. Each subsequent experiment followed its own technical-failure rule, did not retry based on correctness, and was not retroactively treated as a reproduction of the original primary test.

Original DeepSeek non-thinking used temperature 0.2 with a cap of 256. DeepSeek thinking high used a cap of 131072 without explicitly sending temperature/top_p. GLM thinking high used a cap of 131072, temperature 1.0, and top_p 0.95. The subsequent challenge, reference controls, and rule generation reused the DeepSeek thinking configuration. The contemporaneous output-cap controls used non-thinking, temperature 0.2, and caps of 256 or 8192, with all other request contents fixed. Each task had an independent context; a provider's high setting or a shared cap does not imply equal computation.

| Model experiment | final responses | physical task attempts | known output tokens |
|---|---:|---:|---:|
| Original DeepSeek non-thinking | 48 | 48 | 469 |
| Original DeepSeek thinking | 48 | 49 | 736187 |
| Original GLM thinking | 48 | 49 | 1039407 |
| 9-world challenge | 18 | 18 | 379301 |
| Three-condition reference | 54 | 54 | 911963 |
| Three-condition rule generation | 27 | 27 | 308880 |
| Two non-thinking output caps | 32 | 33 | 316 |

The table excludes technical prechecks. Usage for failed attempts is partly unknown, so these figures are not used to calculate a complete bill or an exact total cost. Provider-counted reasoning included in output is not added again; a missing reasoning field does not mean that internal computation was zero. For the seven experiments in this table, all final responses were valid and were not truncated; this statement does not cover the first microloop run discussed in S7. For the output-cap control, known successful input was 21364 and output was 316, for a total of 21680; timeout consumption is unknown. Per-condition usage for the reference controls and rule generation is reported in the respective result reports.

## S6. Reproduction Material Entry Points and Release Boundary

| Evidence | Protocol / method | Completed results and verification entry point |
|---|---|---|
| Original 24 worlds | [v2 configuration](configs/spark-strong-k4-utilization-primary-benchmark-v2.json) | [original primary results](reviews/utilization-primary-results-20260908.md), [DS thinking](reviews/utilization-thinking-results-20260909.md), [GLM](reviews/utilization-glm53-results-20260909.md) |
| Original 4/5-round code checks | [budget script](reviews/budget-sensitivity-20260908.py) | [results JSON](reviews/budget-sensitivity-20260908.json) |
| New-pool challenge | [call plan](reviews/shortcut-challenge-live-plan-20260910.md) | [model results](reviews/shortcut-challenge-model-results-20260910.md) |
| Auxiliary reference | [design](reviews/context-structured-reference-plan-20260910.md), [protocol](reviews/context-association-live-protocol-20260910.md) | [results](reviews/context-structured-reference-results-20260910.md) |
| New-observation rule generation | [design](reviews/new-evidence-protocol-draft-20260910.md), [protocol](reviews/new-evidence-live-protocol-20260910.md) | [results](reviews/new-evidence-model-results-20260910.md) |
| Non-thinking output caps | [protocol](reviews/nonthinking-budget-live-protocol-20260910.md) | [results](reviews/nonthinking-budget-results-20260910.md) |

Each result report points to the local `artifacts` directory containing the plan, private mapping, generation, analysis, results, and raw response/attempt records, and lists the corresponding hashes and completed read-only checks. The exact original prompts, per-world mappings, and remaining pair statistics follow these frozen files and are not manually reconstructed in this supplement. Historical reports' “not executed later” statements reflect their earlier status only; the current top-level `HANDOFF` governs.

Replaying saved responses does not require new model requests. Do not rerun experiment `run`/`freeze`/`build` commands over old artifacts. Key files must never accompany released data. A local allowlisted [reproduction candidate](paper-reproduction-20260913/README.md) and [exact prompt appendix](paper-prompts-en-20260913.md) now accompany this draft. The candidate reproduces revised microloop stage scoring, not all experiment construction or raw-provider verification. Its evaluator-only test labels must remain separate from model inputs. No materials have been publicly released; complete-paper packaging, release/licensing review and independent-machine validation remain pending.


## S7. Two-Feedback Rule Revision

### S7.1 Materials and model responsibility

Twelve worlds (ordinals 0–11) were constructed from a fixed new seed namespace without selection on model performance. Each has 12 initial observations, 49 legal query points, 64 disjoint test points and 256 distinct initial-observation-compatible bank hypotheses over the 125-point domain. The public prompt exposes the initial observations, parent and legal query points, not the bank, target, or test labels. These are distinct from the earlier 24-world cohort and nine-world challenge. Reusing these twelve worlds after the contract repair makes the revised run exploratory, not an independent confirmation set.

Each condition receives three model responses: initial candidate/query, candidate/query after feedback one, and final candidate after feedback two. Active executes the proposed legal unused point; random uses two distinct hash-selected public query points; repeat uses two hash-selected old observations. All conditions are told the actual executed point and true label. Only active and random receive two new labels. Random and repeat also propose queries, but these proposals are not executed. Invalid active proposals consume a stage without fallback. Prior raw JSON answers, not normalized expressions or hidden reasoning, are replayed in the conversation.

The revised prompt explicitly permits an integer expression or a root predicate. A root predicate P is normalized to (ite P (const 1) (const 0)); the resulting expression must satisfy the existing depth ≤5, node-count ≤31 and full-domain binary-output constraints. This repair does not relax observation consistency. Each response is scored against evidence visible at that stage. Missing or invalid final candidates receive zero accuracy; an earlier invalid response does not automatically invalidate a later valid candidate. New labels here reveal only the queried point, unlike the early first-mismatch oracle that also certifies an ordered prefix.

Two binary feedback labels yield at most four feedback sequences for a fixed deterministic policy and cannot generally distinguish all 256 initially compatible targets. Stochastic policies do not remove that worst-case information limit. Exact full-domain recovery is consequently a secondary descriptive endpoint, not a requirement that each trajectory must meet.

### S7.2 Revised actual run: all worlds

Each cell reports correct predictions out of 64 disjoint test inputs, as initial / after feedback one / final. Ordinals follow the frozen analysis. These stages and repeated conditions are not independent samples.

| World | Active | Random | Repeat |
|---|---:|---:|---:|
| 0 | 41/41/56 | 41/56/56 | 41/41/41 |
| 1 | 44/50/48 | 44/44/50 | 44/44/44 |
| 2 | 51/51/40 | 51/51/51 | 51/48/48 |
| 3 | 42/13/50 | 42/42/42 | 13/13/13 |
| 4 | 47/41/38 | 47/47/47 | 47/47/47 |
| 5 | 45/45/45 | 45/45/57 | 45/31/31 |
| 6 | 41/41/36 | 41/42/41 | 41/41/44 |
| 7 | 45/45/29 | 45/45/40 | 45/45/13 |
| 8 | 51/38/35 | 51/50/50 | 51/51/31 |
| 9 | 48/48/49 | 48/45/45 | 48/48/48 |
| 10 | 44/37/36 | 44/37/44 | 44/44/15 |
| 11 | 49/38/38 | 49/38/38 | 49/49/8 |

Mean accuracy is 71.35/63.54/65.10% for active, 71.35/70.57/73.05% for random and 67.58/65.36/49.87% for repeat. Active minus random final accuracy is −7.94 percentage points (2 wins, 8 losses, 2 ties); active minus repeat is +15.23 (9/3/0); random minus repeat is +23.18 (9/2/1). Initial-to-final gains are −6.25, +1.69 and −17.71 points. Active improves over itself in 4 worlds, declines in 7 and ties in 1. Active and random initial candidates have identical full-domain behavior in all 12 worlds; repeat initial candidates do not.

All 108 responses are executable and consistent with the observations then available. Full-domain exact recovery is 0/12 in every condition at every stage. Active's 24 feedback labels contradict its preceding candidate 16 times, versus 6/24 for random; these refer to each condition's own candidates, not a common-prior information-gain measure. Initial-to-final behavior changes in 11/12 active, 7/12 random and 7/12 repeat worlds. These additional trajectory diagnostics are descriptive, not new preregistered endpoints.

### S7.3 Public code baselines

The public candidate reservoir contains 1,767 expressions; it is not the private 256-member target bank. Code conditions combine a query policy with an observation-consistent minimum-node rule, either allowing constants or restricting to nonconstant candidates. These baselines do not receive hidden test labels for selection. Budgets and enumeration resources are not equated to model token costs.

| Final test accuracy | Balanced queries | Random queries | Repeat |
|---|---:|---:|---:|
| Minimum-node, constants allowed | 83.85% | 77.08% | 77.47% |
| Minimum-node, nonconstant | 76.95% | 71.22% | 68.10% |
| Unchanged parent | 71.35% | 71.35% | 71.35% |
| Constant zero | 77.47% | 77.47% | 77.47% |
| Constant one | 22.53% | 22.53% | 22.53% |

There are 173 positive labels among the 768 test inputs. Constant zero exposes class imbalance, but fits final visible observations in only 7/12 worlds in each of the model active and random conditions, versus 12/12 for their final model candidates. Thus it is a predictive reference, not an evidence-consistent replacement in every world. Balanced minus random accuracy is +5.73 points (7/1/4) for nonconstant rules and +6.77 (5/1/6) when constants are allowed. Baseline exact recovery is also 0/12. Different query policies receive different evidence; these are system-level comparisons, not an isolated test of the updater.

### S7.4 Repair timeline: three distinct results

| Version | Active final | Random final | Repeat final |
|---|---:|---:|---:|
| Original run, strict original scoring | 0.00% | 6.64% | 12.76% |
| Original saved responses, repaired scoring | 64.06% | 62.63% | 57.68% |
| Revised prompt, new actual responses | 65.10% | 73.05% | 49.87% |

The original prompt listed root gt/eq predicates although the integer-expression parser required predicates inside ite. This prompt–parser contract defect invalidated legitimate-looking answers and was our implementation responsibility. The original run saved 106/108 stages with two missing responses: world 4 random final, and world 4 repeat after feedback one. Its strict scores are not interpretable as clean capability estimates.

Post-hoc root-predicate normalization makes 63 saved responses parseable, including 31 final responses; one other active final response remains invalid and the two missing stages remain missing. This is rescoring, not a revised-prompt experiment. Its active–random difference is +1.43 points; excluding world 4 changes it to −4.26, a sensitivity diagnostic rather than the primary estimate. An initial defective diagnostic implementation is retained as obsolete; the corrected v2 replay is the relevant rescore.

The revised prompt was then actually run through the full response/feedback chain on the same twelve worlds. It has no missing, invalid or truncated responses, and its negative active–random difference cannot be attributed to the old parser defect. The three versions must not be pooled as additional independent worlds or silently substituted for one another.

### S7.5 Execution, usage and reproduction

Both actual microloop runs used DeepSeek thinking with an output cap of 131072; the revised run records DeepSeek V4 Pro/high, JSON responses, stream false, and no explicitly supplied sampling temperature or seed. The exact provider request configuration is preserved in the frozen plans and request records.

| Run | Saved stages / physical attempts | Known input tokens | Known output tokens |
|---|---:|---:|---:|
| First actual run | 106/110 | 120386 | 838494 |
| Revised actual run | 108/108 | 139915 | 942031 |

The first run had four failed attempts, including two retries; their token consumption is unknown. The revised run had zero failures, retries or truncations, with known total usage 1081946 tokens. The first known total is 958880, not a complete bill. Rescoring made no model calls.

Reproduction entry points: [materials and seed plan](artifacts/microloop-draft-20260910/seed-plan.json), [baseline results](reviews/microloop-baseline-results-20260910.md), [repair report](reviews/microloop-contract-repair-20260911.md), [revised protocol](reviews/microloop-revised-live-protocol-20260911.md), [revised results](reviews/microloop-revised-model-results-20260911.md), [saved stage analysis](artifacts/microloop-revised-live-20260911/analysis.json), and [independent replay audit](artifacts/microloop-revised-audit-20260911.json). The [revised contract implementation](reviews/microloop_revision_20260910.py) defines the executable grammar; the [audit script](reviews/microloop_revised_audit_20260911.py) independently checks raw-response hashes, visible-prompt scoring and saved aggregates.

These are local reproduction pointers, not an already released public benchmark. The public/private split and release checks in S6 also apply here.

## S8. Exact Prompts and Portable Scoring Scope

The separate [verbatim appendix](paper-prompts-en-20260913.md) shows the first frozen task from each earlier task set and all three active stages of revised world 0, without selecting examples by score. The [machine-readable collection](paper-reproduction-20260913/prompts.json) preserves 287 user prompt strings and their SHA256 hashes: 48 original entries shared across configurations, 18 challenge entries, 54 reference controls, 27 single-observation tasks, 32 output-cap tasks and 108 revised interactive stages. This is a count of prompt records, not physical calls or independent worlds. Legacy microloop prompts are retained in the original repository rather than this collection.

The revised microloop requests contained one user message and no system message. Each subsequent prompt embeds prior raw answer content and actual feedback; regenerating these strings from the state machine agrees exactly with all 108 saved requests. Earlier exported user strings do not constitute a complete transport/message-role specification; frozen protocols remain authoritative for request settings.

The local package includes only the prompt collection, saved candidate/feedback/evaluation fields, the original deterministic DSL, a standalone scoring script, README and manifest. It omits credentials, environment values, provider envelopes and reasoning traces. Python 3.10+ suffices without third-party dependencies or model calls. A copied-file, clean-environment replay on the same machine checks all 108 revised stage scores and observation consistency. This verifies portability within that isolation boundary, not a new-machine replication, full-world reconstruction or complete-paper reproduction.
