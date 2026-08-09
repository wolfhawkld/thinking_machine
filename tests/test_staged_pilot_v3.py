from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from src.pilot_checkpoint import INVALID_CANDIDATE_SENTINEL, sha256_bytes, sha256_json
from src.pilot_checkpoint_v3 import (
    PilotCheckpointV3Error,
    acquire_shard_lock,
    inspect_slot_state,
    publish_attempt_outcome,
    publish_attempt_start,
)
from src.providers.openai_compatible import (
    HTTPResponse,
    OpenAICompatibleGenerator,
    ResponsePayloadError,
    TransportError,
)
from src.staged_pilot_v3 import (
    AcceptedResponseContract,
    DurableLogicalSlotGenerator,
    FrozenTransactionIdentity,
    V3ResponseContractError,
    V3SlotUnresolvedError,
    V3TransportExhaustedError,
    route_binding_sha256,
)


SECRET = "v3-test-secret"


def _identity(*, shard_index: int = 4, campaign_hash: str = "a" * 64):
    return FrozenTransactionIdentity(
        campaign_manifest_payload_sha256=campaign_hash,
        execution_plan_sha256="b" * 64,
        plan_entry_sha256="c" * 64,
        run_id="d" * 64,
        shard_index=shard_index,
        model_stratum="official-test-model",
        phase="main",
        world_seed=2001,
        depth=3,
        arm_id="E2",
    )


def _success(
    *,
    content: str | None = None,
    model: str = "frozen-model",
    fingerprint: str | None = None,
    cache: bool = False,
    cache_mismatch: bool = False,
) -> HTTPResponse:
    if content is None:
        content = json.dumps({"expression": " (var x1) "})
    value = {
        "model": model,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 3,
            "total_tokens": 14,
        },
    }
    if fingerprint is not None:
        value["system_fingerprint"] = fingerprint
    if cache:
        value["usage"]["prompt_cache_hit_tokens"] = 2
        value["usage"]["prompt_cache_miss_tokens"] = 8 if cache_mismatch else 9
    return HTTPResponse(200, json.dumps(value).encode("utf-8"))


class SequenceTransport:
    def __init__(self, *events: object) -> None:
        self.events = list(events)
        self.calls: list[dict[str, object]] = []

    def post(self, **kwargs: object) -> HTTPResponse:
        self.calls.append(dict(kwargs))
        if not self.events:
            raise AssertionError("unexpected network request")
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event  # type: ignore[return-value]


def _generator(transport: SequenceTransport) -> OpenAICompatibleGenerator:
    return OpenAICompatibleGenerator(
        base_url="https://provider.example/v1",
        api_key=SECRET,
        model="request-model",
        timeout=120.0,
        transport=transport,
        clock=lambda: 1.0,
    )


def _contract(**kwargs) -> AcceptedResponseContract:
    return AcceptedResponseContract(
        provider_models=("frozen-model",),
        seed_supported=False,
        **kwargs,
    )


def _wrapper(
    root: Path,
    transport: SequenceTransport,
    *,
    identity: FrozenTransactionIdentity | None = None,
) -> DurableLogicalSlotGenerator:
    generator = _generator(transport)
    contract = _contract()
    return DurableLogicalSlotGenerator(
        campaign_dir=root,
        shard_index=4,
        generator=generator,
        frozen_route_binding_sha256=route_binding_sha256(generator, contract),
        transaction_identity=_identity() if identity is None else identity,
        response_contract=contract,
    )


def _generate(wrapper: DurableLogicalSlotGenerator, *, prompt: str = "safe prompt"):
    class _StopAfterOneCommittedSlot(RuntimeError):
        pass

    result = None
    try:
        with wrapper:
            result = wrapper.generate(
                prompt,
                temperature=1.0,
                round_index=0,
                candidate_index=0,
                seed=2001,
                state={"best_probe_score": 0.0, "round": 0, "improved": False},
            )
            # Most unit tests exercise a single durable slot.  Exit as an
            # explicit interrupted process, never by shrinking the live
            # protocol's exact 20-slot shard.
            raise _StopAfterOneCommittedSlot
    except _StopAfterOneCommittedSlot:
        return result


class StagedPilotV3Tests(unittest.TestCase):
    def test_transaction_identity_is_plan_derived_and_cross_campaign_replay_fails(self) -> None:
        entry = {
            "shard_index": 4,
            "phase": "main",
            "model_stratum": "official-test-model",
            "world_seed": 2001,
            "depth": 3,
            "arm_id": "E2",
            "run_id": "d" * 64,
        }
        entry["plan_entry_sha256"] = sha256_json(entry)
        derived = FrozenTransactionIdentity.from_plan_entry(
            campaign_manifest_payload_sha256="a" * 64,
            execution_plan_sha256="b" * 64,
            entry=entry,
        )
        self.assertNotEqual(
            derived.binding_sha256,
            FrozenTransactionIdentity.from_plan_entry(
                campaign_manifest_payload_sha256="e" * 64,
                execution_plan_sha256="b" * 64,
                entry=entry,
            ).binding_sha256,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _generate(_wrapper(root, SequenceTransport(_success())))
            transport = SequenceTransport(_success())
            foreign = _wrapper(
                root,
                transport,
                identity=_identity(campaign_hash="e" * 64),
            )
            with self.assertRaisesRegex(PilotCheckpointV3Error, "transaction"):
                foreign.execution_audit()
            with self.assertRaisesRegex(PilotCheckpointV3Error, "transaction"):
                _generate(foreign)
            self.assertEqual(transport.calls, [])

    def test_protocol_rejects_generator_and_contract_subclasses(self) -> None:
        class GeneratorSubclass(OpenAICompatibleGenerator):
            pass

        class ContractSubclass(AcceptedResponseContract):
            pass

        with tempfile.TemporaryDirectory() as directory:
            transport = SequenceTransport(_success())
            generator = GeneratorSubclass(
                base_url="https://provider.example/v1",
                api_key=SECRET,
                model="request-model",
                timeout=120.0,
                transport=transport,
            )
            with self.assertRaisesRegex(TypeError, "generator"):
                route_binding_sha256(generator, _contract())
            exact_generator = _generator(transport)
            contract = ContractSubclass(
                provider_models=("frozen-model",), seed_supported=False
            )
            with self.assertRaisesRegex(TypeError, "response_contract"):
                DurableLogicalSlotGenerator(
                    campaign_dir=Path(directory),
                    shard_index=4,
                    generator=exact_generator,
                    frozen_route_binding_sha256="f" * 64,
                    transaction_identity=_identity(),
                    response_contract=contract,
                )
            self.assertEqual(transport.calls, [])

    def test_timeout_retries_identical_body_then_commits_first_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = SequenceTransport(
                TransportError(category="timeout", delivery_ambiguous=True),
                _success(),
            )
            wrapper = _wrapper(root, transport)
            response = _generate(wrapper)

            self.assertEqual(response.expression, "(var x1)")
            self.assertEqual(len(transport.calls), 2)
            self.assertIs(transport.calls[0]["body"], transport.calls[1]["body"])
            self.assertEqual(inspect_slot_state(root, 4, 0).accepted_attempt, 2)
            audit = wrapper.execution_audit()
            self.assertEqual(audit["physical_request_starts"], 2)
            self.assertEqual(audit["content_retry_count"], 0)
            self.assertFalse(audit["gross_usage_complete"])
            self.assertFalse(audit["recovery_allows_actual_token_matched_claim"])

    def test_invalid_assistant_content_is_committed_without_content_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = SequenceTransport(_success(content="not json"))
            wrapper = _wrapper(root, transport)
            response = _generate(wrapper)

            self.assertEqual(response.expression, INVALID_CANDIDATE_SENTINEL)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(inspect_slot_state(root, 4, 0).accepted_attempt, 1)
            self.assertEqual(wrapper.execution_audit()["content_retry_count"], 0)

    def test_committed_call_replays_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _wrapper(root, SequenceTransport(_success()))
            expected = _generate(first)

            transport = SequenceTransport()
            resumed = _wrapper(root, transport)
            actual = _generate(resumed)
            self.assertEqual(actual, expected)
            self.assertEqual(transport.calls, [])
            self.assertEqual(resumed.execution_audit()["call_checkpoint_replays"], 1)

    def test_durable_retryable_outcome_resumes_at_next_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = _generator(SequenceTransport())
            contract = _contract()
            prepared = generator.prepare_request(
                "safe prompt",
                temperature=1.0,
                seed=2001,
                state={"best_probe_score": 0.0, "round": 0, "improved": False},
            )
            publish_attempt_start(
                root,
                shard_index=4,
                slot_index=0,
                attempt_ordinal=1,
                request_body_sha256=prepared.body_sha256,
                prompt_sha256=sha256_bytes(b"safe prompt"),
                route_binding_sha256=route_binding_sha256(generator, contract),
                transaction_binding_sha256=_identity().binding_sha256,
            )
            publish_attempt_outcome(
                root,
                shard_index=4,
                slot_index=0,
                attempt_ordinal=1,
                outcome_class="retryable_http",
                http_status=503,
            )

            transport = SequenceTransport(_success())
            resumed = _wrapper(root, transport)
            _generate(resumed)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(inspect_slot_state(root, 4, 0).accepted_attempt, 2)

    def test_orphaned_start_stops_instead_of_drawing_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generator = _generator(SequenceTransport())
            contract = _contract()
            prepared = generator.prepare_request(
                "safe prompt",
                temperature=1.0,
                seed=2001,
                state={"best_probe_score": 0.0, "round": 0, "improved": False},
            )
            publish_attempt_start(
                root,
                shard_index=4,
                slot_index=0,
                attempt_ordinal=1,
                request_body_sha256=prepared.body_sha256,
                prompt_sha256=sha256_bytes(b"safe prompt"),
                route_binding_sha256=route_binding_sha256(generator, contract),
                transaction_binding_sha256=_identity().binding_sha256,
            )
            transport = SequenceTransport(_success())
            with self.assertRaises(V3SlotUnresolvedError):
                wrapper = _wrapper(root, transport)
                _generate(wrapper)
            self.assertEqual(transport.calls, [])
            audit = wrapper.execution_audit()
            self.assertEqual(audit["physical_request_starts"], 1)
            self.assertEqual(audit["unresolved_slot_count"], 1)
            self.assertFalse(audit["gross_usage_complete"])

    def test_three_retryable_failures_are_engineering_failure_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = SequenceTransport(
                HTTPResponse(503, b""),
                HTTPResponse(503, b""),
                HTTPResponse(503, b""),
            )
            wrapper = _wrapper(root, transport)
            with self.assertRaises(V3TransportExhaustedError):
                _generate(wrapper)
            self.assertEqual(len(transport.calls), 3)
            self.assertEqual(inspect_slot_state(root, 4, 0).status, "exhausted")
            audit = wrapper.execution_audit()
            self.assertEqual(audit["physical_request_starts"], 3)
            self.assertEqual(audit["retry_count"], 2)
            self.assertEqual(audit["exhausted_slot_count"], 1)
            self.assertFalse(audit["gross_usage_complete"])

    def test_2xx_envelope_or_bound_model_failure_is_fatal_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = SequenceTransport(HTTPResponse(200, b"not-json"), _success())
            with self.assertRaises(ResponsePayloadError):
                wrapper = _wrapper(root, malformed)
                _generate(wrapper)
            self.assertEqual(len(malformed.calls), 1)
            self.assertEqual(inspect_slot_state(root, 4, 0).status, "fatal")
            self.assertEqual(wrapper.execution_audit()["fatal_slot_count"], 1)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong_model = SequenceTransport(_success(model="drifted-model"), _success())
            wrapper = _wrapper(root, wrong_model)
            with self.assertRaises(V3ResponseContractError):
                _generate(wrapper)
            self.assertEqual(len(wrong_model.calls), 1)
            self.assertEqual(inspect_slot_state(root, 4, 0).status, "fatal")
            audit = wrapper.execution_audit()
            self.assertEqual(audit["discarded_known_response_count"], 1)
            self.assertEqual(audit["gross_known_token_lower_bound"], 14)
            self.assertEqual(audit["usage_unknown_start_marker_count"], 0)
            self.assertEqual(
                audit["failure_category_counts"], {"provider_model_contract": 1}
            )

    def test_cache_and_fingerprint_contract_drift_are_fatal_without_retry(self) -> None:
        for event in (_success(cache=True), _success(fingerprint="unexpected")):
            with self.subTest(event=event):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    transport = SequenceTransport(event, _success())
                    with self.assertRaises(V3ResponseContractError):
                        _generate(_wrapper(root, transport))
                    self.assertEqual(len(transport.calls), 1)
                    self.assertEqual(inspect_slot_state(root, 4, 0).status, "fatal")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = SequenceTransport(_success(cache=True, cache_mismatch=True))
            generator = _generator(transport)
            contract = _contract(prompt_cache_mode="complete")
            wrapper = DurableLogicalSlotGenerator(
                campaign_dir=root,
                shard_index=4,
                generator=generator,
                frozen_route_binding_sha256=route_binding_sha256(generator, contract),
                transaction_identity=_identity(),
                response_contract=contract,
                forbidden_values=(SECRET, generator.endpoint),
            )
            with self.assertRaises(V3ResponseContractError):
                _generate(wrapper)
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(inspect_slot_state(root, 4, 0).status, "fatal")

    def test_response_contract_collections_must_be_immutable_tuples(self) -> None:
        with self.assertRaisesRegex(ValueError, "tuple"):
            AcceptedResponseContract(provider_models=["model"])  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "tuple"):
            AcceptedResponseContract(
                provider_models=("model",),
                finish_reasons=["stop"],  # type: ignore[arg-type]
            )

    def test_fingerprint_is_frozen_and_only_its_hash_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = SequenceTransport(_success(fingerprint=SECRET))
            generator = _generator(transport)
            contract = _contract(
                provider_fingerprint_mode="exact_sha256",
                provider_fingerprint_sha256=sha256_bytes(SECRET.encode()),
            )
            wrapper = DurableLogicalSlotGenerator(
                campaign_dir=root,
                shard_index=4,
                generator=generator,
                frozen_route_binding_sha256=route_binding_sha256(generator, contract),
                transaction_identity=_identity(),
                response_contract=contract,
                forbidden_values=(SECRET, generator.endpoint),
            )
            _generate(wrapper)
            checkpoint = next((root / "call-checkpoints").glob("*.json")).read_bytes()
            self.assertNotIn(SECRET.encode(), checkpoint)
            self.assertIn(sha256_bytes(SECRET.encode()).encode(), checkpoint)

    def test_timeout_and_route_contract_are_checked_before_marker_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = SequenceTransport(_success())
            wrong_timeout = OpenAICompatibleGenerator(
                base_url="https://provider.example/v1",
                api_key=SECRET,
                model="request-model",
                timeout=1.0,
                transport=transport,
            )
            contract = _contract()
            with self.assertRaisesRegex(ValueError, "120"):
                DurableLogicalSlotGenerator(
                    campaign_dir=root,
                    shard_index=4,
                    generator=wrong_timeout,
                    frozen_route_binding_sha256=route_binding_sha256(
                        wrong_timeout, contract
                    ),
                    transaction_identity=_identity(),
                    response_contract=contract,
                )
            self.assertEqual(transport.calls, [])
            self.assertFalse((root / "physical-attempts").exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = SequenceTransport(_success())
            generator = _generator(transport)
            oversized = _contract(max_output_tokens=512)
            with self.assertRaisesRegex(ValueError, "256"):
                DurableLogicalSlotGenerator(
                    campaign_dir=root,
                    shard_index=4,
                    generator=generator,
                    frozen_route_binding_sha256=route_binding_sha256(
                        generator, oversized
                    ),
                    transaction_identity=_identity(),
                    response_contract=oversized,
                )
            self.assertEqual(transport.calls, [])
            self.assertFalse((root / "physical-attempts").exists())

    def test_audit_rejects_unknown_marker_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = _wrapper(root, SequenceTransport(_success()))
            _generate(wrapper)
            original = next((root / "physical-attempts").glob("*-start.json"))
            unknown = original.with_name(
                "shard-0004-slot-00-attempt-04-start.json"
            )
            unknown.write_bytes(original.read_bytes())
            os.chmod(unknown, 0o600)
            with self.assertRaisesRegex(PilotCheckpointV3Error, "filename"):
                wrapper.execution_audit()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = _wrapper(root, SequenceTransport(_success()))
            _generate(wrapper)
            original = next((root / "physical-attempts").glob("*-start.json"))
            foreign = original.with_name(
                "shard-9999-slot-00-attempt-01-start.json"
            )
            foreign.write_bytes(original.read_bytes())
            os.chmod(foreign, 0o600)
            with self.assertRaisesRegex(PilotCheckpointV3Error, "coordinates"):
                wrapper.execution_audit()

    def test_empty_shard_audit_cannot_claim_complete_usage_or_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = _wrapper(root, SequenceTransport())
            audit = wrapper.execution_audit()
            self.assertFalse(audit["shard_complete"])
            self.assertFalse(audit["gross_usage_complete"])
            self.assertFalse(audit["recovery_allows_actual_token_matched_claim"])

    def test_changed_route_is_rejected_before_marker_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _generator(SequenceTransport())
            frozen = route_binding_sha256(first, _contract())
            changed_transport = SequenceTransport(_success())
            changed = OpenAICompatibleGenerator(
                base_url="https://other-provider.example/v1",
                api_key=SECRET,
                model="request-model",
                timeout=120.0,
                transport=changed_transport,
            )
            with self.assertRaisesRegex(ValueError, "drifted"):
                DurableLogicalSlotGenerator(
                    campaign_dir=root,
                    shard_index=4,
                    generator=changed,
                    frozen_route_binding_sha256=frozen,
                    transaction_identity=_identity(),
                    response_contract=_contract(),
                )
            self.assertEqual(changed_transport.calls, [])
            self.assertFalse((root / "physical-attempts").exists())

    def test_future_slot_gap_fails_preflight_and_releases_shard_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transport = SequenceTransport(_success())
            generator = _generator(transport)
            contract = _contract()
            prepared = generator.prepare_request(
                "future prompt",
                temperature=1.0,
                seed=2002,
            )
            binding = route_binding_sha256(generator, contract)
            publish_attempt_start(
                root,
                shard_index=4,
                slot_index=1,
                attempt_ordinal=1,
                request_body_sha256=prepared.body_sha256,
                prompt_sha256=sha256_bytes(b"future prompt"),
                route_binding_sha256=binding,
                transaction_binding_sha256=_identity().binding_sha256,
            )
            wrapper = DurableLogicalSlotGenerator(
                campaign_dir=root,
                shard_index=4,
                generator=generator,
                frozen_route_binding_sha256=binding,
                transaction_identity=_identity(),
                response_contract=contract,
                forbidden_values=(SECRET, generator.endpoint),
            )
            with self.assertRaisesRegex(PilotCheckpointV3Error, "future"):
                with wrapper:
                    pass
            self.assertEqual(transport.calls, [])
            with acquire_shard_lock(root, 4):
                pass

    def test_resume_rejects_prompt_or_request_drift_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _generate(_wrapper(root, SequenceTransport(_success())))
            transport = SequenceTransport(_success())
            with self.assertRaisesRegex(Exception, "hash drifted"):
                _generate(_wrapper(root, transport), prompt="changed prompt")
            self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
