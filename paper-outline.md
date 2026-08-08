# Paper Outline: A Short Validation Preprint

**Status:** Draft v0.1

**Date:** 2026-08-04

**Target length:** 5–6 main-text pages, plus references and reproducibility appendix

## 1. Recommended Positioning

### Working title

> **Does Adaptive Entropy Scheduling Improve Verifiable Hypothesis Search? A Controlled Pilot Study**

Alternative title if the experiment is not yet complete:

> **Adaptive Exploration–Verification Scheduling for Scientific Hypothesis Search: A Testable Framework**

`Entropy-Cycling Scientific Agents` may remain as a subtitle or project name. It should not substitute for an operational definition.

### Intended readers

Primary audience:

- AI-for-Science and scientific-discovery-agent researchers;
- LLM search, verification, and test-time-compute researchers;
- evolutionary, quality-diversity, novelty-search, and stochastic-optimization researchers.

Secondary audience:

- researchers working on open-ended learning and recursive self-improvement.

Human neuroscience, AGI, and RSI are motivation or implications only. They are not the evidentiary basis of the paper.

### Paper type

The first release is a **validation preprint / proof-of-mechanism paper**. If released before results, it must instead be labeled a **position paper / testable framework** throughout.

## 2. Claim Contract

### Claim before results

> We introduce a controlled experiment for testing whether verification feedback can adapt sampling-based exploration under a fixed inference budget, and we specify the outcomes that would support or falsify the mechanism.

### Maximum positive claim after a successful first experiment

> In procedurally generated, programmatically verifiable symbolic worlds, verifier-feedback-controlled temperature scheduling improved held-out hypothesis recovery under a matched inference and verification budget relative to the preregistered comparator.

This wording is permitted only if the primary comparison and budget checks in `experiment-spec.md` pass.

### Additional claim levels

- If Adaptive beats Fixed-Cycle: feedback control outperformed a predetermined high/low cycle.
- If Adaptive beats MTX: the method showed an increment beyond the implemented simultaneous multi-temperature exchange baseline.
- If the effect replicates with a second model: the result is not specific to one model snapshot.
- If a recognized external benchmark also improves: the effect has preliminary transfer beyond the synthetic DSL.

Each level must be earned separately. A lower-level result must not be written as a higher-level conclusion.

### Explicit non-claims

The paper does not claim that:

- randomness itself creates intelligence;
- neural noise explains human creativity;
- token sampling temperature is a physical or biological entropy;
- there is a universal optimal intermediate entropy;
- the system discovers new real-world science;
- the system performs recursive self-improvement;
- the work proves anything about the viability or limits of Transformers for AGI;
- no prior work has explored similar mechanisms;
- the method outperforms AI Co-Scientist, Robin, DGM, AlphaEvolve, or EvoDiverse as complete systems.

Recommended boundary sentence:

> These results test a controllable search-policy mechanism; they do not establish that stochasticity itself creates intelligence, that the system performs recursive self-improvement, or that synthetic rule recovery transfers directly to real scientific discovery.

## 3. Abstract Blueprint

Keep the abstract between 150 and 190 words and fill the result fields only after the frozen analysis completes.

```text
Scientific hypothesis search requires both exploration of diverse candidates and reliable
elimination of unsupported ones. Existing LLM systems implement many forms of sampling,
evolution, critique, and verification, but it remains unclear whether verification feedback
should explicitly control exploration intensity under a fixed inference budget. We study this
question in procedurally generated symbolic worlds with programmatic ground truth. We compare
fixed low, medium, and high temperatures, open-loop annealing, a fixed cycle, a multi-temperature
exchange baseline, and a verifier-feedback-controlled adaptive schedule while matching model
calls, candidates, tokens, and verifier access. [PRIMARY RESULT.] [MECHANISM RESULT.]
Our findings provide [support/no support] for adaptive temperature scheduling as a mechanism
for verifiable hypothesis search. They do not imply real-world scientific discovery or recursive
self-improvement, and instead motivate targeted evaluation in richer scientific environments.
```

## 4. Main-Text Structure and Page Budget

| Section | Target length | Required outcome |
|---|---:|---|
| Abstract | 0.15 page | Problem, comparison, result, boundary |
| 1. Introduction and Scope | 0.70 page | One research question and three contributions |
| 2. Related Work and Gap | 0.80 page | Honest direct-overlap matrix and exact increment |
| 3. Method | 1.10 pages | World, verifier, schedules, and controller |
| 4. Experimental Protocol | 0.85 page | Budget matching, metrics, seeds, statistics |
| 5. Results and Analysis | 1.20 pages | Primary result, manipulation check, mechanism comparison |
| 6. Limitations and Implications | 0.55 page | Failure modes and transfer boundary |
| 7. Conclusion | 0.20 page | One result, one boundary, one next step |

References and the reproducibility appendix are excluded from the main-text target.

## 5. Section-by-Section Outline

### 1. Introduction and Scope

Opening problem:

- Scientific search must preserve unusual candidates long enough to test them.
- More sampling alone may only spend more compute or create invalid outputs.
- The testable question is whether external validation should control subsequent exploration intensity.

Required distinctions:

- token-level sampling randomness versus an Agent-level search policy;
- diversity versus validity;
- fixed schedules versus feedback-controlled scheduling;
- search-policy adaptation versus model-weight self-improvement.

End the introduction with exactly one research question and three contributions:

1. an operational, falsifiable definition of adaptive exploration–verification scheduling;
2. a matched-budget closed-world evaluation with programmatic truth;
3. an empirical result or, before experiments, a preregistered protocol with explicit failure conditions.

The human-brain analogy is limited to one short motivation paragraph or moved to the discussion.

### 2. Related Work and Gap

This section must answer:

1. Which existing systems already generate, rank, evolve, critique, or verify hypotheses?
2. Which methods already use fixed, annealed, evolutionary, or multi-temperature exploration?
3. What does verifier-feedback scheduling add beyond those methods?
4. Is the contribution a new system, a controller, an evaluation, or only a framing?

Minimum related-work groups:

- AI Co-Scientist and multi-agent hypothesis generation;
- Robin, FutureHouse, and experiment/data-feedback loops;
- The AI Scientist and automated research workflows;
- FunSearch and AlphaEvolve;
- DGM and open-ended archives of self-modifying agents;
- self-consistency, Tree-of-Thought, MCTS, best-of-N, and test-time compute;
- evolutionary search, novelty search, and quality-diversity;
- simulated annealing and parallel tempering;
- EvoDiverse or the closest available multi-temperature scientific-hypothesis search;
- DiscoveryWorld and other controlled scientific-discovery benchmarks.

Core references already identified:

- [Towards Diverse Scientific Hypothesis Search with Large Language Models](https://arxiv.org/abs/2606.10587)
- [Towards an AI Co-Scientist](https://arxiv.org/abs/2502.18864)
- [The Darwin Gödel Machine](https://sakana.ai/dgm/)
- [The AI Scientist](https://arxiv.org/abs/2408.06292)
- [DiscoveryWorld](https://arxiv.org/abs/2406.06769)
- [A Multi-Agent System for Automating Scientific Discovery / Robin](https://www.nature.com/articles/s41586-026-10652-y)
- [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)

At least 15 primary references are required before release. Replace all `ARXIV`, `NATURE`, and organization-name placeholders inherited from `chat-log.md` with complete citations.

The gap statement should be narrow:

> We isolate whether verification feedback should change a proposal policy's exploration intensity under matched budgets; we do not claim that exploration, verification, memory, or multi-temperature search are individually new.

### 3. Method

Subsections:

#### 3.1 Problem formulation

Define:

- world and hidden law;
- candidate hypothesis;
- proposal policy;
- programmatic verifier;
- archive;
- inference and verification budgets;
- final held-out outcome.

#### 3.2 Exploration proxy and realized diversity

State that temperature is the controlled proxy. Define AST and behavioral diversity separately. Do not combine them into one universal entropy number.

#### 3.3 Exploration–verification loop

Show the five-round loop and archive update. All arms use the same verifier,
probe set, release rule, and feedback budget. The realized counterexamples may
differ endogenously because the arms generate different candidates; report that
trajectory rather than describing the feedback content as identical.

#### 3.4 Adaptive controller

Include the exact deterministic pseudocode from `experiment-spec.md`. Explain why progress lowers temperature and stagnation raises it.

#### 3.5 Baselines

Explain Fixed-Low, Fixed-Mid, Fixed-High, Annealing, Fixed-Cycle, and MTX. State explicitly that MTX is inspired by multi-temperature exchange and is not presented as a formal reproduction of EvoDiverse or parallel tempering.

### 4. Experimental Protocol

Required content:

- DSL and procedural generation;
- development and confirmatory split;
- five rounds, four calls per round;
- candidate and counterexample budgets;
- exact final selection rule;
- hidden test set;
- model/API snapshot;
- token accounting;
- primary and secondary endpoints;
- primary comparator selection on development worlds;
- paired confirmatory statistics and confidence intervals;
- preregistered minimum important effect;
- publication of seeds, configs, raw outputs, and code.

State explicitly that candidates are not independent statistical samples; the unit of analysis is a world.

Also disclose the development execution deviation before presenting results:

- the first monolithic 1,120-call attempt produced no private-test result and
  was abandoned after a transport error at request 235;
- the replacement execution protocol commits independent 20-call
  world-by-arm generation shards, seals each 140-call world, and releases
  cumulative snapshots at 2, 4, and 8 worlds;
- the 2- and 4-world looks are overlapping, optional-stopping-conditioned,
  exploratory signals only. They cannot freeze `B*` or receive the final
  development positive/negative label;
- the unchanged eight-world decision rule is the only preliminary development
  classification, and confirmatory data remain inaccessible.

Report gross execution overhead separately from the accepted scientific grid:
physical request starts, discarded successful calls, ambiguous deliveries,
known-token lower bound, and whether recovery invalidates an actual-token-
matched claim. Never hide abandoned shards inside a nominal 1,120-call total.

### 5. Results and Analysis

The section order is frozen to reduce result-driven storytelling:

1. protocol-v1 transport abort and protocol-v2 recovery/accounting deviation;
2. 2- and 4-world exploratory snapshots, clearly separated from the final
   result and never described as independent replications;
3. complete-grid budget and implementation checks;
4. H1 manipulation check: temperature versus realized diversity;
5. primary Adaptive-versus-`B*` result;
6. Adaptive versus Fixed-Cycle and MTX;
7. cost-normalized performance;
8. difficulty-tier and nonoverlapping two-world heterogeneity diagnostics;
9. syntax/parser failure analysis;
10. representative successful and failed trajectories;
11. sensitivity analysis, clearly labeled secondary.

Negative results remain in the same order. Do not omit a failed manipulation check or a stronger baseline that wins.

### 6. Limitations and Implications

Required limitations:

- a symbolic DSL is not an open scientific domain;
- sampling temperature may be a weak or model-dependent manipulation;
- behavioral equivalence is easier to verify than scientific validity;
- one model family does not establish generality;
- the verifier is perfect by construction, unlike literature or experimental evidence;
- MTX is not necessarily a full reproduction of the closest published method;
- closed-world improvement cannot be extrapolated to AGI or RSI;
- longer adaptive runs may behave differently from five rounds;
- the task studies search control, not autonomous goal selection.

Implication ladder:

- Positive synthetic result: justify replication in a second model and controlled benchmark.
- Positive external-benchmark result: justify a domain-grounded scientific task.
- Negative result: revise the exploration operator, not the outcome metric after the fact.

### 7. Conclusion

Use three sentences:

1. the precise tested mechanism;
2. the observed result and its boundary;
3. the next falsifiable extension.

Do not conclude with a claim about AGI, human cognition, or an intelligence explosion.

## 6. Required Figures and Tables

### Figure 1: Controlled loop

```text
train observations
      ↓
temperature-controlled proposal policy
      ↓
candidate ASTs → programmatic verifier → archive
      ↑                                  ↓
      └──────── adaptive controller ← score/counterexample
                                         ↓
                                  private final test
```

### Figure 2: Temperature policies

Plot the five-round temperature trajectories for L, M, H, A, C, MTX, and an example E run. E must be marked as data-dependent rather than a predetermined curve.

### Figure 3: Primary result

Paired world-level Adaptive-minus-comparator differences with a confidence interval, accompanied by raw arm-level distributions.

### Figure 4: Mechanism result

Realized behavioral diversity and best verifier score over rounds. The figure should reveal whether expansion and convergence actually occurred.

### Table 1: Operational definitions

| Term | Operational meaning in this paper | Not claimed to be |
|---|---|---|
| Sampling temperature | API decoding control | Scientific-space entropy |
| Structural diversity | Distance between canonical ASTs | Scientific novelty |
| Behavioral diversity | Prediction disagreement over the finite domain | Validity |
| Verification | Exact program execution on probe points | Literature or wet-lab confirmation |
| Adaptive scheduling | Deterministic score-feedback controller | Model self-improvement |

### Table 2: Related-work matrix

Columns:

- diversified proposals;
- explicit exploration/temperature control;
- external verifier/tool;
- verification-feedback controller;
- archive or memory;
- matched-budget comparison;
- programmatic ground truth;
- relation to this paper.

Rows should include AI Co-Scientist, Robin, The AI Scientist, AlphaEvolve/FunSearch, DGM, search/novelty methods, EvoDiverse, and this work.

### Table 3: Arm and budget matrix

List calls, candidates, token caps, verifier executions, released counterexamples, and final-selection rules for every arm.

## 7. Reproducibility Appendix

Include:

- exact DSL grammar;
- world-generation rejection rules;
- all prompts;
- candidate schema;
- controller pseudocode;
- schedule values;
- config hashes;
- world seeds;
- model/API snapshot and date;
- complete budget ledger;
- statistical code;
- seed-level results;
- deviations from preregistration;
- sanitized raw-response location.

## 8. Writing and Release Gates

### Gate P0 — framing

- The title is a question or bounded mechanism claim.
- The abstract has one claim.
- `entropy`, `candidate`, `verification`, and `budget` are operationally defined.

### Gate P1 — related work

- At least 15 primary references.
- Closest competing work is discussed directly.
- No `first`, `no one`, `SOTA`, or `complete coverage` claim without a documented search.

### Gate P2 — experiment integrity

- Frozen configs and seed split.
- Programmatic truth and exact verifier.
- Same inference and verification budget across arms.
- Strong static, open-loop, cyclic, and multi-temperature baselines.
- Primary comparison and minimum important effect frozen.

### Gate P3 — reporting

- Manipulation check reported before outcome claims.
- Confidence intervals and seed-level variation reported.
- Negative and null results retained.
- Budget and syntax failures visible.
- Claim level follows the interpretation table in `experiment-spec.md`.

### Gate P4 — release

- PDF and source available.
- One-command reproduction tested in a clean environment.
- Code, configs, seeds, logs, and analysis published together.
- LLM assistance disclosed according to the selected venue or repository policy.
- Public version has a stable timestamp.

## 9. Outreach Derivatives

The preprint should generate four compact external artifacts:

1. a one-page memo containing the claim, loop diagram, strongest result, closest-work difference, and one question;
2. one fixed-budget performance figure suitable for email preview;
3. a three-minute reproducibility demo;
4. a repository with a one-command run and frozen logs.

The initial outreach ask is technical feedback on prior art or a short replication discussion, not adoption of a complete AGI/RSI theory.

Suggested target order after the preprint is reproducible:

1. authors of the closest multi-temperature hypothesis-search work;
2. Sakana AI / DGM and open-ended-learning researchers;
3. Recursive and other explicitly self-improving-AI teams;
4. RSI workshop organizers and related paper authors;
5. FutureHouse after adding a domain-grounded extension;
6. Google DeepMind AI Co-Scientist or AlphaEvolve authors;
7. frontier-lab RSI safety/evaluation programs when framed around monitorability and verification.

## 10. Definition of Done

The short preprint is ready when a technically informed reader can answer all of the following without contacting the authors:

- What exactly is controlled?
- What exactly is measured as diversity?
- What does the verifier know?
- What information and compute does each arm receive?
- Which baseline is the primary comparator?
- What result would falsify the proposal?
- What result was actually observed?
- Which claims are explicitly outside scope?
- Can the experiment be reproduced from one command?
