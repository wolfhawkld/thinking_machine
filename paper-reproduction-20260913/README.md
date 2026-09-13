# Local reproduction candidate — 13 September 2026

Not published and not a complete-paper reproduction package. Python 3.10+; standard library only. Run `python3 -I replay.py` from this directory. No API keys, network, model requests or dependencies are required.

`prompts.json` contains exact user prompt strings for the original entry cohort and four follow-up task sets, plus all 108 revised microloop stage prompts. Original cohort prompts are shared across configurations; this is not a physical request count. Legacy microloop prompts/rescoring are not exported. Request settings and any other message roles are not reconstructed for the earlier experiments. Revised microloop transport sent a single user message containing each saved prompt, with no system message.

`microloop-evaluation.json` contains saved answer content, actual feedback and held-out evaluation labels. The test labels are evaluator-only: never add this file to a model prompt. The package verifies 108 revised stage scores and evidence consistency using the original DSL, but does not independently regenerate worlds, recompute every paper baseline or verify provider raw envelopes. Full local raw replay remains in the source repository.

Allowlisted fields only: no key files, headers, environment values, endpoints, raw provider envelopes, reasoning traces, or request IDs. Source hashes are provenance, not proof of independent scientific validity. The author has approved sharing this candidate in the public project repository for independent review. Complete-paper packaging, licensing review and a separate-machine check remain pending.
