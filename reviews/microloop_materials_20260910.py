"""One-shot offline construction of 12 fixed new microloop worlds. No API."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import dsl, spark_world

OUT = ROOT / "artifacts/microloop-draft-20260910"
NAMESPACE = "microloop-v1-20260910"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def seed(text, short=False):
    raw = hashlib.sha256(text.encode()).digest()
    return int.from_bytes(raw[:8] if short else raw, "big") & ((1 << 63) - 1) if short else int.from_bytes(raw, "big")


def seeds():
    return [seed(NAMESPACE + ":world:" + str(i), True) for i in range(12)]


def historical_seeds():
    old, sources = set(), {}
    def visit(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items(): visit(v, k)
        elif isinstance(value, list):
            if key in ("seeds", "world_seeds"):
                old.update(v for v in value if type(v) is int)
            for v in value: visit(v)
        elif key == "world_seed" and type(value) is int:
            old.add(value)
    for path in sorted((ROOT / "configs").glob("*.json")):
        visit(json.loads(path.read_text()))
        sources[str(path.relative_to(ROOT))] = sha(path)
    for namespace in ("spark-strong-k4-utilization-feasibility-v1:world-seed",
                      "spark-strong-k4-utilization-feasibility-v2:world-seed",
                      "spark-shortcut-challenge-search-v1-20260909:world-seed"):
        old.update(seed(namespace + ":" + str(i), True) for i in range(1024))
    return old, sources


def save(path, value):
    # Exclusive creation: interrupted/finished directories are not overwritten.
    with path.open("x") as f:
        path.chmod(0o600)
        json.dump(value, f, indent=2)
        f.write("\n")


def world_rows(world, ordinal):
    rows = lambda examples: [{"point": list(e.point), "label": e.label} for e in examples]
    d0, evidence, test = rows(world.train), rows(world.evidence), rows(world.test)
    sets = [{tuple(r["point"]) for r in x} for x in (d0, evidence, test)]
    if list(map(len, sets)) != [12, 49, 64] or any(sets[i] & sets[j] for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Split mismatch")
    if set.union(*sets) != set(dsl.DOMAIN): raise ValueError("Domain mismatch")
    vectors = [dsl.behavior_vector(h) for h in world.hypotheses]
    if len(vectors) != 256 or len(set(vectors)) != 256: raise ValueError("Bank mismatch")
    if not all(all(dsl.evaluate(h, r["point"]) == r["label"] for r in d0) for h in world.hypotheses):
        raise ValueError("D0 mismatch")
    parent = min(world.hypotheses, key=lambda h: (dsl.node_count(h), dsl.canonical_hash(h)))
    public = {"ordinal": ordinal, "task_id": "microloop-" + str(ordinal), "D0": d0,
              "parent": dsl.to_sexpr(parent), "query_points": [r["point"] for r in evidence]}
    private = {"ordinal": ordinal, "world_seed": world.world_seed, "target_seed": world.target_seed,
               "world_hash": world.world_hash, "bank": [dsl.to_sexpr(h) for h in world.hypotheses],
               "evidence": evidence, "test": test, "target_behavior": list(dsl.behavior_vector(world.target))}
    return public, private


def build():
    vector = seeds()
    old, sources = historical_seeds()
    if len(set(vector)) != 12 or set(vector) & old: raise ValueError("Seed collision")
    sources.update({str(Path(p).relative_to(ROOT)): sha(p) for p in (__file__, spark_world.__file__, dsl.__file__)})
    OUT.mkdir(mode=0o700, exist_ok=False)
    plan = {"namespace": NAMESPACE, "world_seeds": vector, "worlds": 12,
            "source_hashes": sources, "historical_seed_count": len(old), "seed_collisions": 0,
            "target_selection": "sha256(namespace + ':target:' + world_seed), existing generator",
            "provider_calls": 0, "selection_uses_scores": False}
    save(OUT / "seed-plan.json", plan)
    public, private = [], []
    for ordinal, s in enumerate(vector):
        world = spark_world.generate_spark_world(s, seed(NAMESPACE + ":target:" + str(s)))
        p, q = world_rows(world, ordinal)
        save(OUT / ("world-" + str(ordinal).zfill(2) + ".json"), {"public": p, "private": q})
        public.append(p)
        private.append(q)
        print("materialized", ordinal + 1, "/12", flush=True)
    if any(sha(ROOT / path) != digest for path, digest in sources.items()): raise ValueError("Source changed during build")
    save(OUT / "public.json", {"worlds": public})
    save(OUT / "private.json", {"worlds": private})
    save(OUT / "audit.json", {"worlds": len(public), "seed_collisions": 0, "provider_calls": 0,
         "no_performance_filter": True, "splits_verified": True, "bank_semantics_verified": True,
         "public_sha256": sha(OUT / "public.json"), "private_sha256": sha(OUT / "private.json"),
         "seed_plan_sha256": sha(OUT / "seed-plan.json"), "source_hashes": sources,
         "scope": "new seed instances, same finite generator; not novel target families or natural-task generalization"})
    print("COMPLETE: 12 new worlds; zero model calls")


if __name__ == "__main__": build()
