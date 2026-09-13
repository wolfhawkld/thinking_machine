"""Public-reservoir code policies for the draft microloop experiment.

Selection accepts public observations and unlabelled query locations only.
Private banks, target labels and test scores must not be passed to selectors.
"""
import json
from microloop_protocol_20260910 import digest, schedule
from src import dsl, spark_world


def public_reservoir():
    return spark_world.build_classifier_reservoir()


def compatible(reservoir, observations):
    return tuple(h for h in reservoir if all(dsl.evaluate(h, r["point"]) == r["label"] for r in observations))


def minimum_rule(reservoir, observations, allow_constants=False):
    candidates = list(compatible(reservoir, observations))
    if allow_constants:
        candidates += list(compatible((("const", 0), ("const", 1)), observations))
    if not candidates:
        raise ValueError("No compatible public rule")
    return dsl.to_sexpr(min(candidates, key=lambda h: (dsl.node_count(h), dsl.canonical_hash(h))))


def balanced_query(reservoir, observations, query_points, task_id):
    """Maximize the binary partition balance of public-compatible rules.

    This is a uniform-reservoir heuristic, not the model's or true target prior.
    """
    candidates = compatible(reservoir, observations)
    seen = {tuple(r["point"]) for r in observations}
    remaining = [p for p in query_points if tuple(p) not in seen]
    if not candidates or not remaining:
        raise ValueError("No candidates or query locations")
    def rank(point):
        ones = sum(dsl.evaluate(h, point) for h in candidates)
        zeros = len(candidates) - ones
        return (abs(ones - zeros), digest("microloop-public-balance-v1" + task_id + json.dumps(point)))
    return min(remaining, key=rank)


def simulate(public, reservoir, label_at, policy):
    if policy not in ("random", "balanced", "repeat"):
        raise ValueError("Unknown public policy")
    observations = list(public["D0"])
    stages = []
    for step in range(3):
        stages.append({"nonconstant_minnode": minimum_rule(reservoir, observations),
                       "minnode_allow_constants": minimum_rule(reservoir, observations, True),
                       "parent": public["parent"], "observations": list(observations)})
        if step == 2:
            break
        if policy == "balanced":
            q = balanced_query(reservoir, observations, public["query_points"], public["task_id"])
        else:
            q = schedule(public, policy == "repeat")[step]
        y = next(r["label"] for r in public["D0"] if r["point"] == q) if policy == "repeat" else label_at(q)
        if type(y) is not int or y not in (0, 1):
            raise ValueError("Nonbinary oracle label")
        observations.append({"point": list(q), "label": y})
    return stages
