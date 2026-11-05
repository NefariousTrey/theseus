# Theseus v0.2.2 agent JSON parsing: unit tests. Made by Trey with Grok. MIT License.
# Run: python3 -B -m unittest tests.test_agentjson -v   (from the repo root)
import json
import types
import unittest

from theseus import agent as AG


class FakeAgent:
    enabled = True
    model = "fake"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def describe(self):
        return {"service": "fake service"}

    def chat(self, system, user, max_tokens=700, want_json=False):
        self.prompts.append((system, user, max_tokens))
        a = self.answers.pop(0)
        return a(user) if callable(a) else a


class FakeCov:
    tools = []


def parts(n):
    return [{"id": f"p{i}", "file": f"f{i}.js", "kind": "code", "level": "file", "first": 1, "last": 1}
            for i in range(n)]


SCAN = types.SimpleNamespace(cov=FakeCov(), target="/x/demo")


class ParseTests(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(AG._json_from('```json\n{"order": ["a"]}\n```')["order"], ["a"])

    def test_chatty_with_trailing_text(self):
        t = 'Sure! Here is the plan:\n{"order": ["a", "b"], "reasons": {"a": "x {y}"}}\nHope that helps {ok}.'
        self.assertEqual(AG._json_from(t)["order"], ["a", "b"])

    def test_think_tags(self):
        self.assertEqual(AG._json_from('<think>{"order": ["no"]} hmm</think>{"order": ["yes"]}')["order"], ["yes"])

    def test_bare_list(self):
        self.assertEqual(AG._json_from('["a","b"]')["order"], ["a", "b"])

    def test_truncated(self):
        with self.assertRaises(AG.Truncated):
            AG._json_from('{"order": ["a", "b", "c')

    def test_nothing(self):
        with self.assertRaises(ValueError):
            AG._json_from("I can't help with that.")


class PlannerTests(unittest.TestCase):
    def ids_answer(self, user):
        rows = json.loads(user.split("Parts:\n", 1)[1])
        return "ok:\n" + json.dumps({"order": [r["id"] for r in reversed(rows)]}) + "\nthanks"

    def test_retry_then_agent(self):
        bot = FakeAgent(["no json here", self.ids_answer])
        order, info = AG.make_planner(bot)(parts(5), "files", SCAN)
        self.assertTrue(info["source"].startswith("agent"), info)
        self.assertEqual(info["agent_tries"], 2)
        self.assertIn("ONLY one JSON", bot.prompts[1][0])
        self.assertEqual(sorted(p["id"] for p in order), sorted(p["id"] for p in parts(5)))

    def test_truncated_asks_shorter_list(self):
        def ans(user):
            rows = json.loads(user.split("Parts:\n", 1)[1])
            return self.ids_answer(user) if len(rows) <= 20 else '{"order": ["p1", "p2'
        bot = FakeAgent([ans] * 20)
        order, info = AG.make_planner(bot, chunk=40)(parts(40), "blocks", SCAN)
        self.assertTrue(info["source"].startswith("agent"), info)
        self.assertNotIn("fallback_chunks", info)
        self.assertEqual(len(order), 40)

    def test_fallback_only_after_retries(self):
        bot = FakeAgent(["nope"] * 3)
        order, info = AG.make_planner(bot)(parts(5), "files", SCAN)
        self.assertTrue(info.get("fallback"))
        self.assertEqual(len(bot.prompts), 3)
        self.assertTrue(info["source"].startswith("rule order (agent unavailable after retries"))


if __name__ == "__main__":
    unittest.main()
