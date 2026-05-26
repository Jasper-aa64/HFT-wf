import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import twap_runtime_stage_profile as profile  # noqa: E402


class TwapRuntimeStageProfileTests(unittest.TestCase):
    def test_parse_profile_rows_rank_runtime_hotspots(self) -> None:
        text = "\n".join(
            [
                "[TWAP_PROFILE] stage=twap.push.position_cache_scan elapsed_us=120 count=2",
                "[TWAP_PROFILE] stage=twap.push.json.total elapsed_us=500 count=1",
                "[TWAP_PROFILE] stage=twap.push.position_cache_scan elapsed_us=80 count=2",
                "[TWAP_PROFILE] stage=twap.push.bad elapsed_us=-1 count=1",
            ]
        )

        samples = profile.parse_profile_text(text)
        runtime_rows, profile_rows, hotspot_rows = profile.rows_from_samples(samples, "fixture")

        self.assertEqual(samples["twap.push.position_cache_scan"], [120, 80])
        self.assertEqual(profile_rows[0]["stage"], "twap.push.json.total")
        self.assertEqual(runtime_rows[0]["source"], "fixture")
        self.assertEqual(hotspot_rows[0]["rank"], "1")
        self.assertEqual(hotspot_rows[0]["stage"], "twap.push.position_cache_scan")
        self.assertNotIn("twap.push.json.total", {row["stage"] for row in hotspot_rows})
        self.assertNotIn("twap.push.bad", {row["stage"] for row in runtime_rows})

    def test_build_total_instrumentation_does_not_hit_earlier_return(self) -> None:
        text = (
            "TwapSalePushMessage other() {\n"
            "        message.set_success(true);\n"
            "        return message;\n"
            "}\n"
            "TwapSalePushMessage push() {\n"
            "        message.set_data(PsiCfgLoader::twapSalePositionAggregationPushToJson(cmd, aggregation, userId));\n"
            "        twapProfileLog(\"twap.push.json_serialize_total\", twapProfileNowUs() - twap_profile_json_start, aggregation.subPositionInfo.size());\n"
            "        message.set_success(true);\n"
            "        return message;\n"
            "}\n"
        )
        old = "        message.set_success(true);\n        return message;\n"
        new = (
            "        message.set_success(true);\n"
            "        twapProfileLog(\"twap.push.build_total\", twapProfileNowUs() - twap_profile_total_start, aggregation.subPositionInfo.size());\n"
            "        return message;\n"
        )

        result = profile.replace_once(text, old, new, "build total end")

        self.assertIn("TwapSalePushMessage other() {\n        message.set_success(true);\n        return message;\n}", result)
        self.assertIn("twap.push.build_total", result)
        self.assertEqual(result.count("twap.push.build_total"), 1)

    def test_cfg_write_instrumentation_stays_in_aggregation_serializer(self) -> None:
        text = (
            "std::string unrelated() {\n"
            "\trapidjson::StringBuffer buffer;\n"
            "\trapidjson::Writer<rapidjson::StringBuffer> writer(buffer);\n"
            "\tdocument.Accept(writer);\n"
            "\treturn buffer.GetString();\n"
            "}\n"
            "std::string PsiCfgLoader::twapSalePositionAggregationPushToJson(...) {\n"
            "\tdata.AddMember(\"subPositionInfoList\", subPositionInfoList, allocator);\n"
            "\tdocument.AddMember(\"data\", data, allocator);\n\n"
            "\trapidjson::StringBuffer buffer;\n"
            "\trapidjson::Writer<rapidjson::StringBuffer> writer(buffer);\n"
            "\tdocument.Accept(writer);\n"
            "\treturn buffer.GetString();\n"
            "}\n"
        )
        old = (
            "\trapidjson::StringBuffer buffer;\n"
            "\trapidjson::Writer<rapidjson::StringBuffer> writer(buffer);\n"
            "\tdocument.Accept(writer);\n"
            "\treturn buffer.GetString();\n"
        )
        new = (
            "\tconst auto twap_cfg_profile_write_start = twapCfgProfileNowUs();\n"
            "\trapidjson::StringBuffer buffer;\n"
            "\trapidjson::Writer<rapidjson::StringBuffer> writer(buffer);\n"
            "\tdocument.Accept(writer);\n"
            "\tstd::string twap_cfg_profile_result = buffer.GetString();\n"
            "\ttwapCfgProfileLog(\"twap.push.json.write_string\", twapCfgProfileNowUs() - twap_cfg_profile_write_start, twap_cfg_profile_result.size());\n"
            "\ttwapCfgProfileLog(\"twap.push.json.total\", twapCfgProfileNowUs() - twap_cfg_profile_total_start, aggregation.subPositionInfo.size());\n"
            "\treturn twap_cfg_profile_result;\n"
        )

        result = profile.replace_once(text, old, new, "cfg write")

        self.assertIn("std::string unrelated() {\n\trapidjson::StringBuffer buffer;", result)
        self.assertEqual(result.count("twap.push.json.write_string"), 1)
        self.assertIn("\tdocument.AddMember(\"data\", data, allocator);\n\n\tconst auto twap_cfg_profile_write_start", result)

    def test_session_scan_instrumentation_stays_in_aggregation_fanout(self) -> None:
        old = "        }\n\n        for (const auto &target: target_sessions) {\n"
        new = (
            "        }\n"
            "        twapProfileLog(\"twap.push.session_scan\", twapProfileNowUs() - twap_profile_session_scan_start, target_sessions.size());\n\n"
            "        long long twap_profile_session_filter_us = 0;\n"
            "        long long twap_profile_session_build_us = 0;\n"
            "        long long twap_profile_queue_push_us = 0;\n"
            "        size_t twap_profile_pushed = 0;\n"
            "        for (const auto &target: target_sessions) {\n"
        )
        text = (
            "void sendTwapSalePushMessage() {\n"
            "        std::vector<std::pair<std::string, std::shared_ptr<ClientSession>>> target_sessions;\n"
            "        {\n"
            "        }\n\n"
            "        for (const auto &target: target_sessions) {\n"
            "        }\n"
            "}\n"
            "void sendTwapSaleAggregationPushMessage() {\n"
            "        std::vector<std::tuple<std::string, std::shared_ptr<ClientSession>, StockPositionInfoAggregationRequest>> target_sessions;\n"
            "        const auto twap_profile_session_scan_start = twapProfileNowUs();\n"
            "        {\n"
            "            std::lock_guard<std::mutex> lock(clients_mutex_);\n"
            "        }\n\n"
            "        for (const auto &target: target_sessions) {\n"
            "        }\n"
            "}\n"
        )

        result = profile.replace_once(text, old, new, "session scan end")

        self.assertIn("void sendTwapSalePushMessage() {\n        std::vector<std::pair", result)
        self.assertEqual(result.count("twap.push.session_scan"), 1)
        self.assertIn("void sendTwapSaleAggregationPushMessage()", result)


if __name__ == "__main__":
    unittest.main()
