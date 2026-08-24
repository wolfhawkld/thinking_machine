# Fair-choice post-hoc diagnosis and next-design constraints

Status: post-outcome exploratory design input only. This note does not change the frozen formal result, create a new p-value, or constitute evidence for or against the general spark hypothesis.

Bindings:

- Formal analysis file SHA-256: `89d6edae10314ccda36ea0d50d5b0acceb648adc6163680eccaaa17074605b31`
- Post-hoc diagnostic file SHA-256: `50e6ddfa994aacf79976368d195b034c16560a6ba964370eaff2c6f0c3991e32`
- Post-hoc diagnostic inner SHA-256: `4f4898b0946e2a289107885aa605e0dd701b933c287c1b802d19439bde7ff432`
- Frozen joint classification: `effect_not_observed_under_frozen_protocol`

## What the completed experiment shows descriptively

The formal experiment remains valid: all 192 calls completed under the sealed protocol, and the post-hoc checks found no interface, masking, schedule, or scoring failure. The diagnostics explain why the result was non-positive rather than invalid.

- Factual K4 opportunities existed in all 32 pairs, but Flash, Pro, and GLM selected one in only 7, 4, and 7 pairs. Their factual opportunity misses were 25, 28, and 25.
- The models often reused the same semantic action for factual and sham: 23/32 pairs for Flash, 22/32 for Pro, and 18/32 for GLM.
- Flash and GLM favored direct `replace` actions; Pro showed the strongest display-position-0 preference.
- Of 18 route-level factual K4 hits, 16 occurred in worlds also hit by the frozen B* policy, and 16 produced the same constant all-zero behavior. This is compatible with easy-world or structural explanations, but does not establish that a model internally copied B*.
- Factual/sham K4 opportunity totals were 53/22 actions and 32/14 worlds. Most of the asymmetry came from `affine_multiplicative` (19/1 actions, 8/1 worlds) and `pairwise_variable` (11/1, 8/1); `affine_directional` was much closer (10/10, 8/7).

The paired endpoint decomposition is especially important:

| Route | K2 factual-only | Same raw / changed raw | K4 factual-only | Same raw / changed raw |
|---|---:|---:|---:|---:|
| DeepSeek Flash | 6 | 5 / 1 | 4 | 3 / 1 |
| DeepSeek Pro | 5 | 4 / 1 | 4 | 3 / 1 |
| GLM-5.2 | 6 | 2 / 4 | 5 | 1 / 4 |

For Flash and Pro, most positive discordances came from applying the same selected frame to a different context, not from an observed switch to a new action. This is a real context-dependent endpoint change inside the DSL, but it is weaker than evidence that the model used the context to redirect exploration. A same raw action also does not imply identical internal reasoning.

## Split the next study into two questions

1. **Opportunity creation:** does inserting a candidate context change which useful or strong children are reachable under a fixed action universe? This is a deterministic, model-free census question. It measures properties of the constructed DSL landscape, not model discovery.
2. **Opportunity utilization:** when factual and sham menus contain equally strong opportunities, does a blind model select the context-appropriate nonconstant K4 action more often than frozen choice-bias baselines? This is the next model experiment.

The two questions must not share one conjunctive label. The first can explain how context changes available descendants; only the second tests context-sensitive selection.

## Constraints for a fresh-world utilization benchmark

Before opening any new model outputs, freeze a new finite world/target namespace, scan cap, deterministic matching rule, and cohort-size rule. All worlds opened during construction remain development worlds and cannot later be presented as a natural-population confirmation sample.

Each admitted pair should satisfy, as far as feasibility permits:

- Both factual and sham arms have the same number of qualifying K2 and nonconstant `K4_full_pool` actions; the preferred target is exactly one nonconstant K4 action per arm.
- The qualifying factual and sham actions use different raw semantic frames within a pair, so a fixed raw-action policy cannot succeed in both arms without responding to context.
- Correct raw-action family, edit path, display position, construction stratum, child behavior, and control-bundle diversity are counterbalanced across the full cohort.
- Constant-child K4 actions are excluded from the primary endpoint or placed in a separately preregistered secondary stratum; they cannot satisfy the primary breadth claim.
- Opaque IDs, neutral wording, pair-shared option order, stateless one-shot calls, the private-key barrier, and deterministic endpoint scoring remain unchanged.
- The paired four-cell result and its same-raw/changed-raw split are preregistered mechanism outputs rather than post-hoc diagnostics.

The primary null should reflect equalized menu opportunities plus frozen route-specific choice bias, including the observed raw-0/raw-5 and display-0 tendencies. The information-advantaged B* policy remains a preregistered sensitivity ceiling, but should not be the sole conjunctive veto for the narrower prompt-visible selection question. No baseline may be selected after fresh outcomes are observed.

Sample size must be set by a prospective power simulation after the model-free feasibility scan establishes the attainable matched-pair geometry. The present choice-responsive discordances are too sparse to justify simply adding more calls to the current 32 pairs.

## Immediate next task

Design and run a model-free feasibility scan for the exact-matched, nonconstant-K4 pair criteria above. The scan should answer whether a balanced cohort can be built under a fixed cap; it should not call a provider or be interpreted as a hypothesis result.
