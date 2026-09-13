import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import microloop_live_20260910 as live


class LiveTests(unittest.TestCase):
    def attempt(self, status, body):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            class Transport:
                def post(self, **kwargs):
                    request = json.loads(kwargs['body'])
                    self.test = request
                    assert request['max_tokens'] == 131072
                    assert kwargs['timeout'] == 3600
                    return SimpleNamespace(status=status, body=json.dumps(body).encode())
            with patch.object(live, 'OUT', root):
                return live.sender('test-only', root, Transport)('public prompt')

    def test_success_and_invalid_candidate_not_retried(self):
        value = self.attempt(200, {'model': 'deepseek-v4-pro', 'choices': [
            {'message': {'content': 'invalid DSL'}, 'finish_reason': 'length'}],
            'usage': {'prompt_tokens': 4, 'completion_tokens': 8}})
        self.assertEqual(value['content'], 'invalid DSL')
        self.assertEqual(value['finish_reason'], 'length')

    def test_http_retryable(self):
        with self.assertRaises(live.runner.TechnicalFailure): self.attempt(429, {})

    def test_http_auth_fatal(self):
        with self.assertRaisesRegex(RuntimeError, '401'): self.attempt(401, {})

    def test_wrong_model_fatal(self):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected response model'):
            self.attempt(200, {'model': 'wrong'})

    def test_bad_envelope_technical(self):
        with self.assertRaises(live.runner.TechnicalFailure):
            self.attempt(200, {'model': 'deepseek-v4-pro', 'choices': []})


if __name__ == '__main__': unittest.main()
