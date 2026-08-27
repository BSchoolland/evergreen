"""Concurrency resolution in scripts/review-branch.py.

Run with: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("review_branch", REPO / "scripts" / "review-branch.py")
review_branch = importlib.util.module_from_spec(_spec)
sys.modules["review_branch"] = review_branch
_spec.loader.exec_module(review_branch)


OLLAMA_MODELS = {
    "providers": {
        "ollama": {
            "baseUrl": "http://127.0.0.1:11434/v1",
            "models": [{"id": "qwen3.8:27b"}, {"id": "hf.co/user/repo"}],
        },
        "openrouter": {
            "baseUrl": "https://openrouter.ai/api/v1",
            "models": [{"id": "moonshotai/kimi-k2.6"}],
        },
        # A hosted provider with one model pinned to a local endpoint.
        "openai": {"models": [{"id": "local-proxy", "baseUrl": "http://127.0.0.1:8080/v1"}]},
    }
}

PI_SETTINGS = {"defaultProvider": "ollama", "defaultModel": "qwen3.8:27b"}


class ResolveEndpoint(unittest.TestCase):
    def endpoint(self, model, settings=PI_SETTINGS):
        return review_branch.resolve_endpoint(model, settings, OLLAMA_MODELS)

    def test_declared_provider_prefix(self):
        self.assertEqual(self.endpoint("ollama/qwen3.8:27b"),
                         ("ollama", "http://127.0.0.1:11434/v1"))

    def test_builtin_provider_prefix_has_no_base_url(self):
        self.assertEqual(self.endpoint("openai-codex/gpt-5.5"), ("openai-codex", None))

    def test_thinking_suffix_is_stripped(self):
        self.assertEqual(self.endpoint("openai-codex/gpt-5.5:high"), ("openai-codex", None))

    def test_ollama_style_tag_is_not_a_thinking_suffix(self):
        self.assertEqual(review_branch.strip_thinking_suffix("qwen3.8:27b"), "qwen3.8:27b")

    def test_bare_id_is_looked_up_in_models_json(self):
        self.assertEqual(self.endpoint("qwen3.8:27b"), ("ollama", "http://127.0.0.1:11434/v1"))

    def test_slash_in_a_model_id_is_not_a_provider_prefix(self):
        # `hf.co` is not a provider; the id belongs to ollama.
        self.assertEqual(self.endpoint("hf.co/user/repo"), ("ollama", "http://127.0.0.1:11434/v1"))

    def test_id_after_a_declared_prefix_keeps_its_slashes(self):
        self.assertEqual(self.endpoint("openrouter/moonshotai/kimi-k2.6"),
                         ("openrouter", "https://openrouter.ai/api/v1"))

    def test_per_model_base_url_overrides_the_provider(self):
        self.assertEqual(self.endpoint("openai/local-proxy"), ("openai", "http://127.0.0.1:8080/v1"))

    def test_unknown_bare_id_falls_back_to_the_default_provider(self):
        self.assertEqual(self.endpoint("gpt-5.5"), ("ollama", None))

    def test_no_model_uses_pi_default_model(self):
        self.assertEqual(self.endpoint(None), ("ollama", "http://127.0.0.1:11434/v1"))

    def test_no_default_model_still_uses_the_default_provider(self):
        self.assertEqual(self.endpoint(None, {"defaultProvider": "ollama"}),
                         ("ollama", "http://127.0.0.1:11434/v1"))


class IsSelfHostedUrl(unittest.TestCase):
    def test_self_hosted(self):
        for url in ("http://127.0.0.1:11434/v1", "http://localhost:1234/v1",
                    "http://192.168.1.50:11434/v1", "http://[::1]:11434/v1",
                    "http://gpubox.local:11434/v1"):
            self.assertTrue(review_branch.is_self_hosted_url(url), url)

    def test_hosted(self):
        for url in ("https://openrouter.ai/api/v1", "https://api.anthropic.com",
                    "https://8.8.8.8/v1"):
            self.assertFalse(review_branch.is_self_hosted_url(url), url)


class ResolveJobs(unittest.TestCase):
    """resolve_jobs reads pi's real config dir, so point it at a temp one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        agent_dir = Path(self.tmp.name)
        (agent_dir / "models.json").write_text(json.dumps(OLLAMA_MODELS))
        (agent_dir / "settings.json").write_text(json.dumps(PI_SETTINGS))
        self.env = unittest.mock.patch.dict(
            "os.environ", {review_branch.PI_AGENT_DIR_ENV: str(agent_dir)}
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_pi_default_is_the_local_model_so_lenses_serialize(self):
        # The live configuration that produced the timeouts: no --model, pi's
        # default provider is ollama on loopback.
        jobs, reason = review_branch.resolve_jobs(3, None)
        self.assertEqual(jobs, 1)
        self.assertIn("self-hosted", reason)

    def test_hosted_api_keeps_full_fan_out(self):
        jobs, _ = review_branch.resolve_jobs(3, "openai-codex/gpt-5.5")
        self.assertEqual(jobs, 3)

    def test_declared_hosted_provider_keeps_full_fan_out(self):
        jobs, _ = review_branch.resolve_jobs(3, "openrouter/moonshotai/kimi-k2.6")
        self.assertEqual(jobs, 3)

    def test_model_pinned_to_a_local_endpoint_serializes(self):
        jobs, _ = review_branch.resolve_jobs(3, "openai/local-proxy")
        self.assertEqual(jobs, 1)

    def test_slashed_local_model_id_serializes(self):
        jobs, _ = review_branch.resolve_jobs(3, "hf.co/user/repo")
        self.assertEqual(jobs, 1)

    def test_missing_pi_config_assumes_hosted(self):
        with unittest.mock.patch.dict("os.environ", {review_branch.PI_AGENT_DIR_ENV: "/nonexistent"}):
            jobs, _ = review_branch.resolve_jobs(3, None)
        self.assertEqual(jobs, 3)


if __name__ == "__main__":
    unittest.main()
