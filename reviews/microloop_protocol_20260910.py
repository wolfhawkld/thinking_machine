"""Offline two-query state machine. No provider or filesystem-writing entrypoint.

Feedback is a single queried point's binary label, never an ordered mismatch
response. Prompts are explicitly rendered from public fields, not private rows.
"""
import copy
import hashlib
import json

from new_evidence_scoring_20260910 import score_expression
from src import dsl

CONDITIONS = ("active", "random", "repeat")


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate_public(public):
    if set(public) != {"ordinal", "task_id", "D0", "parent", "query_points"}:
        raise ValueError("Unexpected public fields")
    train = [tuple(r["point"]) for r in public["D0"]]
    pool = [tuple(p) for p in public["query_points"]]
    if len(train) != 12 or len(set(train)) != 12 or len(pool) != 49 or len(set(pool)) != 49:
        raise ValueError("Split sizes changed")
    if set(train) & set(pool) or not set(train + pool) <= set(dsl.DOMAIN):
        raise ValueError("Invalid/overlapping public points")
    if any(type(r["label"]) is not int or r["label"] not in (0, 1) for r in public["D0"]):
        raise ValueError("Nonbinary public label")


def initial_state(public, condition):
    validate_public(public)
    if condition not in CONDITIONS:
        raise ValueError("Unknown condition")
    return {"condition": condition, "task_id": public["task_id"], "round": 0,
            "history": [], "observations": copy.deepcopy(public["D0"]),
            "executed_queries": [], "finished": False}


def parse_response(content, final):
    failure = None
    try:
        payload = json.loads(content)
        expected = {"expression"} if final else {"expression", "query"}
        if not isinstance(payload, dict) or set(payload) != expected or not isinstance(payload["expression"], str):
            raise ValueError("schema")
    except (ValueError, TypeError):
        return {"expression": None, "query": None, "valid_program": False, "failure": "schema"}
    try:
        ast = dsl.parse_sexpr(payload["expression"])
        dsl.validate_expr(ast)
        if set(dsl.behavior_vector(ast)) - {0, 1}:
            raise ValueError("nonbinary")
        valid = True
    except (ValueError, TypeError, RecursionError):
        valid, failure = False, "invalid_program"
    q = payload.get("query")
    if not final and not (isinstance(q, list) and len(q) == 3 and all(type(v) is int for v in q)):
        q = None
    return {"expression": payload["expression"], "query": q, "valid_program": valid, "failure": failure}


def schedule(public, repeat=False):
    points = [r["point"] for r in public["D0"]] if repeat else public["query_points"]
    namespace = "microloop-repeat-v1" if repeat else "microloop-random-v1"
    return sorted(points, key=lambda p: digest(namespace + public["task_id"] + json.dumps(p)))[:2]


def advance(public, state, content, label_at):
    """Consume one model response. label_at accepts only an executed query.

    For the first two responses all arms propose a query. Controls override the
    proposal by a fixed schedule, even if the proposal is invalid. Active invalid
    queries consume the round without feedback. Program and query validity are
    separate: an invalid program does not automatically invalidate a legal query.
    """
    validate_public(public)
    if state["task_id"] != public["task_id"] or state["finished"] or state["round"] not in (0, 1, 2):
        raise ValueError("State/round mismatch")
    new = copy.deepcopy(state)
    final = state["round"] == 2
    parsed = parse_response(content, final)
    proposed = parsed["query"]
    eligible = proposed is not None and proposed in public["query_points"] and proposed not in state["executed_queries"]
    event = {"round": state["round"], "response": content, **parsed,
             "proposed_query_valid": bool(eligible), "executed_query": None, "feedback": None}
    if not final:
        if state["condition"] == "active":
            point = proposed if eligible else None
        else:
            point = schedule(public, state["condition"] == "repeat")[state["round"]]
        if point is not None:
            if state["condition"] == "repeat":
                label = next(r["label"] for r in public["D0"] if r["point"] == point)
            else:
                label = label_at(point)
            if type(label) is not int or label not in (0, 1):
                raise ValueError("Oracle label must be binary")
            event["executed_query"] = copy.deepcopy(point)
            event["feedback"] = {"point": copy.deepcopy(point), "label": label}
            new["observations"].append(event["feedback"])
            new["executed_queries"].append(copy.deepcopy(point))
    new["history"].append(event)
    new["round"] += 1
    new["finished"] = final
    return new


def render_prompt(public, state):
    validate_public(public)
    if state["task_id"] != public["task_id"] or state["finished"]:
        raise ValueError("Cannot render this state")
    final = state["round"] == 2
    schema = '{"expression":"<DSL>"}' if final else '{"expression":"<DSL>","query":[x1,x2,x3]}'
    # Condition, task identity, seeds, bank, evidence labels and tests are omitted.
    return (
        "Infer a binary rule on x1,x2,x3 in {-2,-1,0,1,2}. Return only JSON. "
        "All supplied observations are true but may not uniquely identify the rule. "
        "The nonconstant target family compares two small arithmetic expressions with gt or eq, "
        "returning 1 or 0. Each compared expression is a variable, small constant, negated variable, "
        "or one add/sub/mul combining a variable with a variable or small constant (either order). "
        "Legal DSL: (var x1/x2/x3), (const c) for integer c in -3..3, (neg A), "
        "(add A B), (sub A B), (mul A B), (gt A B), (eq A B), (ite P A B). "
        "Maximum depth 5 and 31 nodes; your expression must be binary on all 125 inputs. "
        "You may retain or revise the initial candidate. "
        "There are two measurement stages followed by a final response. At a measurement stage "
        "suggest an unused point from the query pool that could distinguish plausible rules. "
        "The measurement scheduler may supply a different point or repeat an old observation. "
        "Update from the actual supplied point and label, not an assumed result of your suggestion.\n"
        + "Initial observations: " + json.dumps(public["D0"]) + "\n"
        + "Initial candidate: " + public["parent"] + "\n"
        + "Query pool (unlabelled): " + json.dumps(public["query_points"]) + "\n"
        + "Prior responses and actual feedback: " + json.dumps([
            {"response": h["response"], "feedback": h["feedback"]} for h in state["history"]]) + "\n"
        + ("Final response; no further queries. " if final else "Measurement stage " + str(state["round"] + 1) + ". ")
        + "Required schema: " + schema
    )


def score_trajectory(public, private, state):
    if not state["finished"] or len(state["history"]) != 3:
        raise ValueError("Trajectory incomplete")
    test = private["test"]
    if {tuple(r["point"]) for r in test} & set(map(tuple, public["query_points"])):
        raise ValueError("Query/test overlap")
    if len(private["target_behavior"]) != 125:
        raise ValueError("Target length")
    behavior = dict(zip(dsl.DOMAIN, private["target_behavior"], strict=True))
    if any(behavior[tuple(r["point"])] != r["label"] for r in [*test, *state["observations"]]):
        raise ValueError("Inconsistent scoring labels")
    stages = []
    observed = copy.deepcopy(public["D0"])
    for event in state["history"]:
        content = json.dumps({"expression": event["expression"]}) if event["expression"] is not None else None
        binding = {"D0": public["D0"], "visible_observations": observed,
                   "test": test, "target_behavior": private["target_behavior"]}
        stages.append(score_expression(content, binding))
        if event["feedback"]:
            observed.append(event["feedback"])
    return {"stages": stages, "final_test_accuracy": stages[-1]["test_accuracy"],
            "initial_to_final_accuracy_delta": stages[-1]["test_accuracy"] - stages[0]["test_accuracy"],
            "new_observations": sum(h["feedback"] is not None for h in state["history"]) if state["condition"] != "repeat" else 0}
