import unittest
from unittest.mock import Mock, patch

from retail_agent.core import Profile, Quote, analyze
from retail_agent.llm import critique
from test_core import rising_bars


class LlmTests(unittest.TestCase):
    def setUp(self):
        bars = rising_bars()
        q = Quote("A", "002001", "样本", bars[-1].close, 80e6, 8e9)
        self.candidate = analyze(q, bars, Profile())

    def test_reject_plain_http_remote(self):
        with self.assertRaises(ValueError):
            critique(self.candidate, "secret", "http://example.com/v1", "model")

    @patch("retail_agent.llm.requests.post")
    def test_structured_critique_and_missing_fields(self, post):
        response = Mock()
        response.json.return_value = {"choices": [{"message": {"content":
            '{"opportunity":"观察放量","countercase":"可能假突破","trigger":"次日核验","invalidation":"跌破参考线","questions":"核实公告"}'}}]}
        post.return_value = response
        result = critique(self.candidate, "secret", "https://example.com/v1", "model")
        self.assertEqual(result["countercase"], "可能假突破")
        self.assertEqual(post.call_args.kwargs["timeout"], 35)
        response.json.return_value = {"choices": [{"message": {"content": '{"opportunity":"只有一个字段"}'}}]}
        with self.assertRaises(ValueError):
            critique(self.candidate, "secret", "https://example.com/v1", "model")


if __name__ == "__main__":
    unittest.main()
