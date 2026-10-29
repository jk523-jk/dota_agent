import unittest

from pydantic import ValidationError

from dota_review.agent_graph import _fallback_answer, classify_request
from dota_review.api import AgentMessage


class AgentTests(unittest.TestCase):
    def test_request_router_recognizes_three_analysis_domains(self):
        self.assertEqual(classify_request("这场的眼位热区在哪"), "ward_hotspots")
        self.assertEqual(classify_request("看看 OG 的战队风格"), "team_style")
        self.assertEqual(classify_request("选手经济和补刀表现", selected_slot=4), "player")

    def test_explicit_analysis_mode_wins_over_keyword_routing(self):
        self.assertEqual(classify_request("帮我看眼位", "team_style"), "team_style")

    def test_player_fallback_uses_evidence_and_names_missing_kda(self):
        answer = _fallback_answer("player", {
            "kind": "player", "player": {"player_name": "Test", "hero": "npc_dota_hero_axe"},
            "checkpoints": [{"target_time_s": 600, "net_worth": 1234, "last_hits": 22,
                             "team_net_worth_rank": 2, "team_size": 5}],
            "observer_wards": 2, "sentry_wards": 3,
        })
        self.assertIn("1,234", answer)
        self.assertIn("不估算 K/D/A", answer)

    def test_agent_request_rejects_unbounded_chat_input_and_invalid_slot(self):
        with self.assertRaises(ValidationError):
            AgentMessage(match_id="a" * 16, message="x" * 4001)
        with self.assertRaises(ValidationError):
            AgentMessage(match_id="a" * 16, message="分析", slot=10)


if __name__ == "__main__":
    unittest.main()
