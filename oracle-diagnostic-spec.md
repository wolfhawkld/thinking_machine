# Post-outcome Oracle Diagnostic Specification

Status: post-snapshot, post-primary-outcome, exploratory development diagnostic.

This diagnostic was requested after the eight-world staged development result
and its endpoint-specification gap were known. Preliminary aggregate sanity
recomputations were performed while designing the implementation. Consequently,
none of the quantities below is preregistered, confirmatory, or permitted to
change the frozen primary classification. The eight development worlds are
calibration data after this analysis.

## Purpose

The diagnostic separates three possible explanations for the observed result:

1. the generated candidate pool did not contain a better hypothesis;
2. a better generated hypothesis existed but the frozen probe selector missed it;
3. invalid DSL generation removed too many search opportunities.

It makes no population-level or statistical-significance claim.

## Source and release barrier

The sole candidate source is the 56 committed, hash-verified checkpoints from
the completed staged-v2 campaign. Before any candidate private-test evaluation,
the implementation must:

- verify the campaign manifest, all 56 checkpoint envelopes, and all eight
  world seals;
- bind the supplied S3 public snapshot to the campaign manifest and require the
  complete 8-world/56-run/1,120-call boundary;
- replay all checkpoints and reproduce their frozen prompts, probe scores,
  validity fields, hashes, trajectories, and selected candidates;
- load and replay every run before evaluating any candidate on the private test.

No model provider, credential loader, or network client is used.

## Candidate eligibility and scores

A candidate is search-eligible exactly when its frozen checkpoint fields have
`syntax_valid=true` and `runtime_valid=true`. An invalid sentinel is never parsed
or evaluated on the private test.

Every search-eligible candidate is evaluated on all 64 private-test points with
the frozen verifier. A candidate that was probe-valid but encounters a private-
test runtime failure remains eligible and receives the verifier's observed score
of zero; it is not removed after seeing the test.

For each world-by-arm run:

- `selected_accuracy` is the frozen selected candidate's reproduced private-test
  accuracy. A run with no selected candidate has no observed selected accuracy.
- `oracle_at_20_accuracy` is the maximum observed private-test accuracy among all
  search-eligible generated candidates.
- `selection_regret` is `oracle_at_20_accuracy - selected_accuracy` when selection
  succeeded.
- `selected_is_oracle_tie` is true when selected accuracy equals the maximum;
  it does not assert that the same candidate identity was selected.

If a run has no search-eligible candidate, both observed selected and oracle
accuracy are null. A separately labelled post-hoc terminal-zero sensitivity
assigns zero to both for arm-level aggregation. Zero is not described as an
observed private-test accuracy.

Oracle@20 selects on the same private test used to score it. It is an optimistic,
unattainable candidate-pool ceiling subject to winner's curse, not a deployable
selection rule or an unbiased performance estimate.

## Probe-to-test relationship

The primary relationship diagnostic is tie-aware Kendall tau-b within each run,
after de-duplicating by full-domain behavior hash. Correlation is null with fewer
than two values or when its denominator is zero. Two secondary variants are also
reported:

- call-weighted, retaining repeated candidate slots;
- canonical-deduplicated, retaining one `(probe, test)` pair per canonical
  candidate hash.

The secondary variants report Pearson and tie-aware Spearman correlation. The
call-weighted variant is explicitly frequency-weighted; the canonical-
deduplicated variant is not. Neither treats candidates as independent samples.

Arm summaries average and take the median over defined run-level correlations;
candidates are never pooled across worlds. The result also reports whether the
private-test oracle is present in the maximum-probe-score tie set. No p-values
or thresholds are used.

## DSL and concentration diagnostics

The result reports search-valid/invalid counts and rates overall, by arm, by
world, and by world-by-arm run, together with frozen failure-code counts. It
also reports each world's share of all invalid candidates. JSON response-schema
adherence remains distinct from executable DSL validity.

## Comparisons and interpretation

Arm-level selected, Oracle@20, and regret means use equal-weighted worlds. The
post-hoc structural-zero sensitivity is used only to make the seven-arm table
complete. The key reference comparison is E minus C. If the best comparator is
also shown separately for selected and Oracle metrics, its identity is a
post-hoc argmax over the frozen comparator set, not a newly frozen comparator.

- If E's Oracle@20 exceeds C while selected E does not, selection is a plausible
  bottleneck.
- If E's Oracle@20 does not exceed C, selection alone cannot explain the missing
  adaptive advantage.
- A concentrated invalid-rate difference remains a model/interface confound and
  prevents a pure exploration-mechanism claim.

These are descriptive diagnostic statements, not decision thresholds.

## Public artifact boundary

The public diagnostic may contain aggregate and per-run counts, accuracies,
regrets, and correlations. It must not contain candidate expressions, candidate-
level private-test scores, test examples or labels, predictions, prompts, raw
provider content, credentials, endpoints, or candidate identity linked to a
private-test score.

The artifact binds the S3 snapshot SHA-256, campaign manifest SHA-256, ordered
checkpoint-set SHA-256, ordered world-seal-set SHA-256, and diagnostic
implementation SHA-256. It explicitly records that the original snapshot was
unchanged and the diagnostic is not the frozen primary analysis.
