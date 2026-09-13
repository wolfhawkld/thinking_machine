"""Read-only historical replay diagnosis; never substitutes a sealed result."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import spark_closure as closure


def differences(left, right, path=""):
    if type(left) is not type(right):
        return [{"path": path, "sealed": left, "replayed": right}]
    if isinstance(left, dict):
        rows = []
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                rows.append({"path": path + "/" + key, "missing_side": "sealed" if key not in left else "replayed"})
            else:
                rows.extend(differences(left[key], right[key], path + "/" + key))
        return rows
    if isinstance(left, list):
        if len(left) != len(right):
            return [{"path": path, "sealed_length": len(left), "replayed_length": len(right)}]
        return [row for i, (a, b) in enumerate(zip(left, right)) for row in differences(a, b, f"{path}/{i}")]
    return [] if left == right else [{"path": path, "sealed": left, "replayed": right}]


if __name__ == "__main__":
    folder = ROOT / "artifacts/spark-closure-layered-v1-20260814"
    inputs = {name: json.loads((folder / f"{name}.json").read_text()) for name in ("plan", "generation", "analysis")}
    captured = {}
    original = closure._sha256_json

    def observe(value):
        digest = original(value)
        if isinstance(value, dict) and value.get("kind") == "spark-closure-offline-analysis":
            captured.update(value, analysis_sha256=digest)
        return digest

    error = None
    # Observe the computed object at hashing; keep validation and digest unchanged.
    with patch.object(closure, "_sha256_json", side_effect=observe):
        try:
            closure.analyze_closure(inputs["plan"], inputs["generation"])
        except closure.ClosureError as exc:
            error = str(exc)
    if not captured:
        raise RuntimeError(f"replay failed before report creation: {error}")
    report = {
        "kind": "historical-replay-diagnosis-only",
        "provider_calls_made": 0,
        "original_validator_error": error,
        "sealed_files_sha256": {name: hashlib.sha256((folder / f"{name}.json").read_bytes()).hexdigest() for name in inputs},
        "sealed_analysis_sha256": inputs["analysis"]["analysis_sha256"],
        "replayed_analysis_sha256": captured["analysis_sha256"],
        "differences": differences(inputs["analysis"], captured),
    }
    output = ROOT / "reviews/replay-audit-20260908.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)
