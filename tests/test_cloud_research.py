import tempfile
import unittest
import sys
from pathlib import Path

from cloud.experiment import (
    classify_result,
    execute_command,
    load_candidate,
    parse_summary,
    same_hardware,
    validate_experiment_id,
)
from cloud.websearch import (
    LINKUP_SEARCH_URL,
    build_query,
    normalize_response,
    search_experiments,
    write_packet,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def post(self, url, **kwargs):
        self.request = {"url": url, **kwargs}
        return FakeResponse(self.payload)


class WebsearchTests(unittest.TestCase):
    def sample_response(self):
        return {
            "data": {
                "candidates": [
                    {
                        "title": "Test optimizer schedule",
                        "hypothesis": "A shorter warmup lowers validation BPB.",
                        "proposed_change": "Reduce warmup steps in train.py.",
                        "expected_effect": "Faster useful learning in five minutes.",
                        "evidence_summary": "The source reports improved short-horizon convergence.",
                        "compatibility_risks": ["May destabilize the first steps."],
                        "source_urls": ["https://example.org/paper"],
                    }
                ]
            },
            "sources": [
                {
                    "name": "Primary paper",
                    "url": "https://example.org/paper",
                    "snippet": "Short-horizon result.",
                }
            ],
        }

    def test_query_contains_experiment_contract(self):
        query = build_query("Lower validation BPB", 3)
        self.assertIn("exactly 3", query)
        self.assertIn("five minutes", query)
        self.assertIn("train.py", query)
        self.assertIn("primary source", query)

    def test_normalizes_candidates_and_sources(self):
        packet = normalize_response(
            self.sample_response(),
            objective="Lower validation BPB",
            query="query",
            count=1,
            depth="deep",
            hardware="one RTX 4090",
        )
        self.assertEqual(packet["schema_version"], 1)
        self.assertRegex(packet["candidates"][0]["id"], r"^[0-9a-f]{12}$")
        self.assertEqual(packet["sources"][0]["url"], "https://example.org/paper")

    def test_rejects_candidate_without_source(self):
        response = self.sample_response()
        response["data"]["candidates"][0]["source_urls"] = []
        with self.assertRaisesRegex(ValueError, "no valid source URL"):
            normalize_response(
                response,
                objective="objective",
                query="query",
                count=1,
                depth="deep",
                hardware="one RTX 4090",
            )

    def test_packet_round_trip_for_runner(self):
        packet = normalize_response(
            self.sample_response(),
            objective="objective",
            query="query",
            count=1,
            depth="deep",
            hardware="one RTX 4090",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_packet(packet, Path(directory) / "packet.json")
            candidate = load_candidate(path, packet["candidates"][0]["id"])
        self.assertEqual(candidate["title"], "Test optimizer schedule")

    def test_search_uses_structured_linkup_contract(self):
        session = FakeSession(self.sample_response())
        packet = search_experiments(
            "objective",
            count=1,
            hardware="one RTX 4090",
            api_key="test-token",
            session=session,
        )
        self.assertEqual(session.request["url"], LINKUP_SEARCH_URL)
        self.assertEqual(session.request["json"]["outputType"], "structured")
        self.assertTrue(session.request["json"]["includeSources"])
        self.assertNotIn("test-token", str(packet))


class ExperimentTests(unittest.TestCase):
    def test_parse_train_summary(self):
        output = """
GPU: NVIDIA GeForce RTX 4090
---
val_bpb:          0.997900
training_seconds: 300.1
peak_vram_mb:     12345.6
num_steps:        953
smoke_test:       true
"""
        self.assertEqual(
            parse_summary(output),
            {
                "val_bpb": 0.9979,
                "training_seconds": 300.1,
                "peak_vram_mb": 12345.6,
                "num_steps": 953,
                "smoke_test": True,
            },
        )

    def test_classify_improvement(self):
        verdict = classify_result(
            return_code=0,
            timed_out=False,
            smoke_test=False,
            summary={"val_bpb": 0.99},
            baseline_val_bpb=1.0,
            min_improvement=0.001,
        )
        self.assertEqual(verdict, "improved")

    def test_classify_timeout_before_metrics(self):
        verdict = classify_result(
            return_code=124,
            timed_out=True,
            smoke_test=False,
            summary={"val_bpb": 0.99},
            baseline_val_bpb=1.0,
            min_improvement=0.0,
        )
        self.assertEqual(verdict, "timeout")

    def test_classify_hardware_mismatch(self):
        verdict = classify_result(
            return_code=0,
            timed_out=False,
            smoke_test=False,
            summary={"val_bpb": 0.99},
            baseline_val_bpb=1.0,
            min_improvement=0.0,
            hardware_matches=False,
        )
        self.assertEqual(verdict, "hardware_mismatch")

    def test_hardware_identity_uses_model_and_memory(self):
        baseline = {"name": "RTX 4090", "memory_total_mb": "24564", "uuid": "GPU-a"}
        rescheduled = {"name": "RTX 4090", "memory_total_mb": "24564", "uuid": "GPU-b"}
        other = {"name": "A100", "memory_total_mb": "81920", "uuid": "GPU-c"}
        self.assertTrue(same_hardware(baseline, rescheduled))
        self.assertFalse(same_hardware(baseline, other))
        self.assertFalse(same_hardware(None, rescheduled))

    def test_command_execution_writes_parseable_log(self):
        command = [
            sys.executable,
            "-c",
            "print('val_bpb:          0.991000')",
        ]
        with tempfile.TemporaryDirectory() as directory:
            return_code, timed_out, _, log_path = execute_command(
                command=command,
                experiment_dir=Path(directory),
                timeout_seconds=10,
            )
            summary = parse_summary(log_path.read_text(encoding="utf-8"))
        self.assertEqual(return_code, 0)
        self.assertFalse(timed_out)
        self.assertEqual(summary["val_bpb"], 0.991)

    def test_experiment_id_blocks_path_traversal(self):
        self.assertEqual(validate_experiment_id("warmup-01"), "warmup-01")
        with self.assertRaises(ValueError):
            validate_experiment_id("../escape")


if __name__ == "__main__":
    unittest.main()
