#!/usr/bin/env python3
"""Run TWAP position-push runtime stage profiling on an instrumented workspace.

This script does not modify the Code2 baseline. It copies the source tree into
an isolated workspace, injects temporary timing logs into the TWAP aggregation
push path, runs the existing remote TWAP performance runner, then converts the
runtime stage logs into profile.tsv / hotspots.tsv artifacts that the headless
auto-loop can consume.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any


PROFILE_FIELDS = ["stage", "total_ms", "count", "avg_ms", "source", "touched_files", "symbols", "notes"]
HOTSPOT_FIELDS = [
    "rank",
    "stage",
    "total_ms",
    "avg_ms",
    "count",
    "score",
    "notes",
    "touched_files",
    "symbols",
    "expected_delta_seconds",
]
RUNTIME_PROFILE_FIELDS = [
    "stage",
    "total_us",
    "total_ms",
    "count",
    "avg_us",
    "avg_ms",
    "p50_us",
    "p95_us",
    "max_us",
    "source",
    "touched_files",
    "symbols",
    "notes",
]

STAGE_META: dict[str, dict[str, str]] = {
    "twap.push.user_accounts_snapshot": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "buildTwapSaleAggregationPushMessage|m_userAccountsCache",
        "risk": "low",
    },
    "twap.push.position_cache_scan": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "buildTwapSaleAggregationPushMessage|m_twapSalePositionCache",
        "risk": "low",
    },
    "twap.push.order_status_lookup": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "buildTwapSaleAggregationPushMessage|getOrderStatus",
        "risk": "low",
    },
    "twap.push.order_config_lookup": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "buildTwapSaleAggregationPushMessage|getTwapSaleOrderConfigCache|orderConfigStrToJson",
        "risk": "low",
    },
    "twap.push.aggregate_loop": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "buildTwapSaleAggregationPushMessage|subPositionInfo",
        "risk": "medium",
    },
    "twap.push.weighted_price_and_sort": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "buildTwapSaleAggregationPushMessage|std::sort",
        "risk": "low",
    },
    "twap.push.json_serialize_total": {
        "files": "PsiGrpcServer/twap_sale_service.cpp|PsiUtils/PsiCfgLoader.cpp",
        "symbols": "twapSalePositionAggregationPushToJson",
        "risk": "medium",
    },
    "twap.push.build_total": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "buildTwapSaleAggregationPushMessage",
        "risk": "diagnostic",
    },
    "twap.push.session_scan": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "sendTwapSaleAggregationPushMessage|token_user_map|twap_sale_aggregation_sub_map_",
        "risk": "low",
    },
    "twap.push.session_filter": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "sendTwapSaleAggregationPushMessage|matchesSearchStockCode",
        "risk": "low",
    },
    "twap.push.message_build_for_sessions": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "sendTwapSaleAggregationPushMessage|buildTwapSaleAggregationPushMessage",
        "risk": "medium",
    },
    "twap.push.queue_push": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "sendTwapSaleAggregationPushMessage|twap_sale_message_queue|queue_mutex",
        "risk": "low",
    },
    "twap.push.fanout_total": {
        "files": "PsiGrpcServer/twap_sale_service.cpp",
        "symbols": "sendTwapSaleAggregationPushMessage",
        "risk": "diagnostic",
    },
    "twap.push.json.add_summary_fields": {
        "files": "PsiUtils/PsiCfgLoader.cpp",
        "symbols": "twapSalePositionAggregationPushToJson|addTwapSalePositionJsonFields",
        "risk": "medium",
    },
    "twap.push.json.subposition_fields": {
        "files": "PsiUtils/PsiCfgLoader.cpp",
        "symbols": "twapSalePositionAggregationPushToJson|subPositionInfoList",
        "risk": "medium",
    },
    "twap.push.json.write_string": {
        "files": "PsiUtils/PsiCfgLoader.cpp",
        "symbols": "twapSalePositionAggregationPushToJson|rapidjson::Writer",
        "risk": "medium",
    },
    "twap.push.json.total": {
        "files": "PsiUtils/PsiCfgLoader.cpp",
        "symbols": "twapSalePositionAggregationPushToJson",
        "risk": "diagnostic",
    },
}


SERVICE_HELPERS = r'''
#include <chrono>
#include <iostream>

namespace {
    inline long long twapProfileNowUs() {
        return std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
    }

    inline void twapProfileLog(const char *stage, long long elapsedUs, size_t count = 1) {
        std::cout << "[TWAP_PROFILE] stage=" << stage
                  << " elapsed_us=" << elapsedUs
                  << " count=" << count << std::endl;
    }
}
'''

CFG_HELPERS = r'''
#include <chrono>
#include <iostream>

namespace {
    inline long long twapCfgProfileNowUs() {
        return std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
    }

    inline void twapCfgProfileLog(const char *stage, long long elapsedUs, size_t count = 1) {
        std::cout << "[TWAP_PROFILE] stage=" << stage
                  << " elapsed_us=" << elapsedUs
                  << " count=" << count << std::endl;
    }
}
'''


def run(cmd: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def copy_source(source_root: Path, workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    ignore = shutil.ignore_patterns(
        ".git",
        ".codex_build",
        ".gatekeeper_worktrees",
        ".trellis",
        ".trellis-backup*",
        "build",
        "gatekeeper_runs",
        "experiments",
        "headless_runs",
        "twap_perf_compare_*",
        "*.docx",
    )
    shutil.copytree(source_root, workspace, ignore=ignore)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    preferred_anchor = {
        "build total end": (
            "        message.set_data(PsiCfgLoader::twapSalePositionAggregationPushToJson(cmd, aggregation, userId));\n"
            "        message.set_success(true);\n"
            "        return message;\n"
        ),
        "session scan end": (
            "        std::vector<std::tuple<std::string, std::shared_ptr<ClientSession>, StockPositionInfoAggregationRequest>> target_sessions;\n"
            "        const auto twap_profile_session_scan_start = twapProfileNowUs();\n"
            "        {\n"
            "            std::lock_guard<std::mutex> lock(clients_mutex_);\n"
        ),
        "cfg write": (
            "\tdata.AddMember(\"subPositionInfoList\", subPositionInfoList, allocator);\n"
            "\tdocument.AddMember(\"data\", data, allocator);\n\n"
            "\trapidjson::StringBuffer buffer;\n"
            "\trapidjson::Writer<rapidjson::StringBuffer> writer(buffer);\n"
            "\tdocument.Accept(writer);\n"
            "\treturn buffer.GetString();\n"
        ),
    }.get(label)
    if label == "build total end" and preferred_anchor and preferred_anchor in text:
        return text.replace(
            preferred_anchor,
            "        message.set_data(PsiCfgLoader::twapSalePositionAggregationPushToJson(cmd, aggregation, userId));\n"
            + new,
            1,
        )
    if label == "build total end":
        scoped_old = (
            "        twapProfileLog(\"twap.push.json_serialize_total\", twapProfileNowUs() - twap_profile_json_start, aggregation.subPositionInfo.size());\n"
            "        message.set_success(true);\n"
            "        return message;\n"
        )
        if scoped_old in text:
            return text.replace(
                scoped_old,
                "        twapProfileLog(\"twap.push.json_serialize_total\", twapProfileNowUs() - twap_profile_json_start, aggregation.subPositionInfo.size());\n"
                + new,
                1,
            )
    if label == "session scan end" and preferred_anchor and preferred_anchor in text:
        scoped_old = preferred_anchor + "        }\n\n        for (const auto &target: target_sessions) {\n"
        scoped_new = preferred_anchor + (
            "        }\n"
            "        twapProfileLog(\"twap.push.session_scan\", twapProfileNowUs() - twap_profile_session_scan_start, target_sessions.size());\n\n"
            "        long long twap_profile_session_filter_us = 0;\n"
            "        long long twap_profile_session_build_us = 0;\n"
            "        long long twap_profile_queue_push_us = 0;\n"
            "        size_t twap_profile_pushed = 0;\n"
            "        for (const auto &target: target_sessions) {\n"
        )
        if scoped_old in text:
            return text.replace(scoped_old, scoped_new, 1)
        marker = "        const auto twap_profile_session_scan_start = twapProfileNowUs();\n"
        marker_index = text.find(marker)
        scoped_index = text.find("        }\n\n        for (const auto &target: target_sessions) {\n", marker_index)
        if marker_index >= 0 and scoped_index >= 0:
            scoped_old = "        }\n\n        for (const auto &target: target_sessions) {\n"
            scoped_new = (
                "        }\n"
                "        twapProfileLog(\"twap.push.session_scan\", twapProfileNowUs() - twap_profile_session_scan_start, target_sessions.size());\n\n"
                "        long long twap_profile_session_filter_us = 0;\n"
                "        long long twap_profile_session_build_us = 0;\n"
                "        long long twap_profile_queue_push_us = 0;\n"
                "        size_t twap_profile_pushed = 0;\n"
                "        for (const auto &target: target_sessions) {\n"
            )
            return text[:scoped_index] + scoped_new + text[scoped_index + len(scoped_old):]
    if label == "cfg write" and preferred_anchor and preferred_anchor in text:
        return text.replace(
            preferred_anchor,
            "\tdata.AddMember(\"subPositionInfoList\", subPositionInfoList, allocator);\n"
            "\tdocument.AddMember(\"data\", data, allocator);\n\n"
            + new,
            1,
        )
    if label in {"build total end", "session scan end", "cfg write"}:
        raise RuntimeError(f"instrumentation anchor not found: {label}")
    if old not in text:
        stable_anchor = {
            "accounts snapshot": (
                "        std::vector<SysUserAccountDb> accounts = mp_baseDataMgr->m_userAccountsCache.snapshot(userId);\n"
            ),
            "position cache get": (
                "        std::vector<TwapSaleStockPositionDb> positions;\n"
                "        for (const auto &account : accounts) {\n"
                "            TwapStockPositionInfo positionInfo;\n"
                "            if (!mp_baseDataMgr->m_twapSalePositionCache.get(account.account_id + \"_\" + stockCode, positionInfo)) {\n"
            ),
            "order status/config start": (
                "            position.orderStatus = getOrderStatus(account.account_id, stockCode);\n\n"
                "            TwapSaleOrderConfigDb orderConfig = getTwapSaleOrderConfigCache(account.account_id, stockCode);\n"
            ),
        }.get(label)
        if stable_anchor and stable_anchor in text:
            return text.replace(stable_anchor, new, 1)
        regex_anchor = {
            "order status/config start": (
                r"            position\.orderStatus = getOrderStatus\(account\.account_id, stockCode\);\n\n"
                r"(?:            //.*\n)?"
                r"            TwapSaleOrderConfigDb orderConfig = getTwapSaleOrderConfigCache\(account\.account_id, stockCode\);\n"
            ),
        }.get(label)
        if regex_anchor and re.search(regex_anchor, text):
            return re.sub(regex_anchor, lambda _match: new, text, count=1)
        raise RuntimeError(f"instrumentation anchor not found: {label}")
    return text.replace(old, new, 1)


def inject_service_cpp(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = replace_once(
        text,
        '#include "twap_sale_service.h"\n',
        '#include "twap_sale_service.h"\n' + SERVICE_HELPERS + "\n",
        "service helpers",
    )
    text = replace_once(
        text,
        "    TwapSalePushMessage TwapSaleServiceImpl::buildTwapSaleAggregationPushMessage(const std::string &userId, const std::string &stockCode, const std::string &cmd) {\n"
        "        TwapSalePushMessage message = {};\n",
        "    TwapSalePushMessage TwapSaleServiceImpl::buildTwapSaleAggregationPushMessage(const std::string &userId, const std::string &stockCode, const std::string &cmd) {\n"
        "        const auto twap_profile_total_start = twapProfileNowUs();\n"
        "        TwapSalePushMessage message = {};\n",
        "build total start",
    )
    text = replace_once(
        text,
        "        // 浠庣紦瀛樨幏鍙栫敤鎴疯处鎴峰垪琛?\n"
        "        std::vector<SysUserAccountDb> accounts = mp_baseDataMgr->m_userAccountsCache.snapshot(userId);\n",
        "        // 浠庣紦瀛樨幏鍙栫敤鎴疯处鎴峰垪琛?\n"
        "        const auto twap_profile_accounts_start = twapProfileNowUs();\n"
        "        std::vector<SysUserAccountDb> accounts = mp_baseDataMgr->m_userAccountsCache.snapshot(userId);\n"
        "        twapProfileLog(\"twap.push.user_accounts_snapshot\", twapProfileNowUs() - twap_profile_accounts_start, accounts.size());\n",
        "accounts snapshot",
    )
    text = replace_once(
        text,
        "        // 浠庣紦瀛樨幏鍙栨寔浠撴暟鎹?\n"
        "        std::vector<TwapSaleStockPositionDb> positions;\n"
        "        for (const auto &account : accounts) {\n"
        "            TwapStockPositionInfo positionInfo;\n"
        "            if (!mp_baseDataMgr->m_twapSalePositionCache.get(account.account_id + \"_\" + stockCode, positionInfo)) {\n",
        "        // 浠庣紦瀛樨幏鍙栨寔浠撴暟鎹?\n"
        "        long long twap_profile_position_cache_us = 0;\n"
        "        long long twap_profile_order_status_us = 0;\n"
        "        long long twap_profile_order_config_us = 0;\n"
        "        size_t twap_profile_positions_seen = 0;\n"
        "        std::vector<TwapSaleStockPositionDb> positions;\n"
        "        for (const auto &account : accounts) {\n"
        "            TwapStockPositionInfo positionInfo;\n"
        "            const auto twap_profile_position_cache_start = twapProfileNowUs();\n"
        "            const bool twap_profile_position_cache_hit = mp_baseDataMgr->m_twapSalePositionCache.get(account.account_id + \"_\" + stockCode, positionInfo);\n"
        "            twap_profile_position_cache_us += twapProfileNowUs() - twap_profile_position_cache_start;\n"
        "            if (!twap_profile_position_cache_hit) {\n",
        "position cache get",
    )
    text = replace_once(
        text,
        "            // 浠庣紦瀛樨幏鍙?orderStatus\n"
        "            position.orderStatus = getOrderStatus(account.account_id, stockCode);\n\n"
        "            // 浠庣紦瀛樨幏鍙?selected, sellRatio, biddingSellRatio, params, keepState\n"
        "            TwapSaleOrderConfigDb orderConfig = getTwapSaleOrderConfigCache(account.account_id, stockCode);\n",
        "            // 浠庣紦瀛樨幏鍙?orderStatus\n"
        "            const auto twap_profile_order_status_start = twapProfileNowUs();\n"
        "            position.orderStatus = getOrderStatus(account.account_id, stockCode);\n"
        "            twap_profile_order_status_us += twapProfileNowUs() - twap_profile_order_status_start;\n\n"
        "            // 浠庣紦瀛樨幏鍙?selected, sellRatio, biddingSellRatio, params, keepState\n"
        "            const auto twap_profile_order_config_start = twapProfileNowUs();\n"
        "            TwapSaleOrderConfigDb orderConfig = getTwapSaleOrderConfigCache(account.account_id, stockCode);\n",
        "order status/config start",
    )
    text = replace_once(
        text,
        "            positions.push_back(position);\n"
        "        }\n\n"
        "        if (positions.empty()) {\n",
        "            positions.push_back(position);\n"
        "            ++twap_profile_positions_seen;\n"
        "            twap_profile_order_config_us += twapProfileNowUs() - twap_profile_order_config_start;\n"
        "        }\n\n"
        "        twapProfileLog(\"twap.push.position_cache_scan\", twap_profile_position_cache_us, accounts.size());\n"
        "        twapProfileLog(\"twap.push.order_status_lookup\", twap_profile_order_status_us, twap_profile_positions_seen);\n"
        "        twapProfileLog(\"twap.push.order_config_lookup\", twap_profile_order_config_us, twap_profile_positions_seen);\n\n"
        "        if (positions.empty()) {\n",
        "position loop end",
    )
    text = replace_once(
        text,
        "        TwapSaleStockPositionAggregationDb aggregation;\n"
        "        bool initialized = false;\n"
        "        for (auto &position: positions) {\n",
        "        TwapSaleStockPositionAggregationDb aggregation;\n"
        "        bool initialized = false;\n"
        "        const auto twap_profile_aggregate_start = twapProfileNowUs();\n"
        "        for (auto &position: positions) {\n",
        "aggregate start",
    )
    text = replace_once(
        text,
        "            aggregation.subPositionInfo.push_back(position);\n"
        "        }\n\n"
        "        double weightedOpenPrice = 0;\n",
        "            aggregation.subPositionInfo.push_back(position);\n"
        "        }\n"
        "        twapProfileLog(\"twap.push.aggregate_loop\", twapProfileNowUs() - twap_profile_aggregate_start, positions.size());\n\n"
        "        const auto twap_profile_weighted_sort_start = twapProfileNowUs();\n"
        "        double weightedOpenPrice = 0;\n",
        "aggregate end",
    )
    text = replace_once(
        text,
        "        std::sort(aggregation.subPositionInfo.begin(), aggregation.subPositionInfo.end(), [](const TwapSaleStockPositionDb &left, const TwapSaleStockPositionDb &right) {\n"
        "            if (left.marketValue == right.marketValue) {\n"
        "                return left.id > right.id;\n"
        "            }\n"
        "            return left.marketValue > right.marketValue;\n"
        "        });\n\n"
        "        message.set_data(PsiCfgLoader::twapSalePositionAggregationPushToJson(cmd, aggregation, userId));\n",
        "        std::sort(aggregation.subPositionInfo.begin(), aggregation.subPositionInfo.end(), [](const TwapSaleStockPositionDb &left, const TwapSaleStockPositionDb &right) {\n"
        "            if (left.marketValue == right.marketValue) {\n"
        "                return left.id > right.id;\n"
        "            }\n"
        "            return left.marketValue > right.marketValue;\n"
        "        });\n"
        "        twapProfileLog(\"twap.push.weighted_price_and_sort\", twapProfileNowUs() - twap_profile_weighted_sort_start, aggregation.subPositionInfo.size());\n\n"
        "        const auto twap_profile_json_start = twapProfileNowUs();\n"
        "        message.set_data(PsiCfgLoader::twapSalePositionAggregationPushToJson(cmd, aggregation, userId));\n"
        "        twapProfileLog(\"twap.push.json_serialize_total\", twapProfileNowUs() - twap_profile_json_start, aggregation.subPositionInfo.size());\n",
        "json total",
    )
    text = replace_once(
        text,
        "        message.set_success(true);\n"
        "        return message;\n",
        "        message.set_success(true);\n"
        "        twapProfileLog(\"twap.push.build_total\", twapProfileNowUs() - twap_profile_total_start, aggregation.subPositionInfo.size());\n"
        "        return message;\n",
        "build total end",
    )
    text = replace_once(
        text,
        "    void TwapSaleServiceImpl::sendTwapSaleAggregationPushMessage(const std::string &account_id, const std::string &stock_code, const std::string &cmd) {\n"
        "        std::string userId;\n",
        "    void TwapSaleServiceImpl::sendTwapSaleAggregationPushMessage(const std::string &account_id, const std::string &stock_code, const std::string &cmd) {\n"
        "        const auto twap_profile_fanout_start = twapProfileNowUs();\n"
        "        std::string userId;\n",
        "fanout total start",
    )
    text = replace_once(
        text,
        "        std::vector<std::tuple<std::string, std::shared_ptr<ClientSession>, StockPositionInfoAggregationRequest>> target_sessions;\n"
        "        {\n"
        "            std::lock_guard<std::mutex> lock(clients_mutex_);\n",
        "        std::vector<std::tuple<std::string, std::shared_ptr<ClientSession>, StockPositionInfoAggregationRequest>> target_sessions;\n"
        "        const auto twap_profile_session_scan_start = twapProfileNowUs();\n"
        "        {\n"
        "            std::lock_guard<std::mutex> lock(clients_mutex_);\n",
        "session scan start",
    )
    text = replace_once(
        text,
        "        }\n\n"
        "        for (const auto &target: target_sessions) {\n",
        "        }\n"
        "        twapProfileLog(\"twap.push.session_scan\", twapProfileNowUs() - twap_profile_session_scan_start, target_sessions.size());\n\n"
        "        long long twap_profile_session_filter_us = 0;\n"
        "        long long twap_profile_session_build_us = 0;\n"
        "        long long twap_profile_queue_push_us = 0;\n"
        "        size_t twap_profile_pushed = 0;\n"
        "        for (const auto &target: target_sessions) {\n",
        "session scan end",
    )
    text = replace_once(
        text,
        "            const StockPositionInfoAggregationRequest &request = std::get<2>(target);\n"
        "            if (!stock_code.empty() && !matchesSearchStockCode(stock_code, request.searchstockcode())) {\n",
        "            const StockPositionInfoAggregationRequest &request = std::get<2>(target);\n"
        "            const auto twap_profile_filter_start = twapProfileNowUs();\n"
        "            const bool twap_profile_filter_match = stock_code.empty() || matchesSearchStockCode(stock_code, request.searchstockcode());\n"
        "            twap_profile_session_filter_us += twapProfileNowUs() - twap_profile_filter_start;\n"
        "            if (!twap_profile_filter_match) {\n",
        "filter",
    )
    old_session_build = (
        "            TwapSalePushMessage message = stock_code.empty()\n"
        "                                          ? buildTwapSaleAggregationMessage(request)\n"
        "                                          : buildTwapSaleAggregationPushMessage(userId, stock_code, cmd);\n"
    )
    cached_stock_build = (
        "        TwapSalePushMessage stock_change_message;\n"
        "        bool has_stock_change_message = false;\n"
        "        if (!stock_code.empty()) {\n"
        "            stock_change_message = buildTwapSaleAggregationPushMessage(userId, stock_code, cmd);\n"
        "            has_stock_change_message = stock_change_message.success();\n"
        "        }\n"
    )
    if old_session_build in text:
        text = replace_once(
            text,
            old_session_build,
            "            const auto twap_profile_build_start = twapProfileNowUs();\n"
            "            TwapSalePushMessage message = stock_code.empty()\n"
            "                                          ? buildTwapSaleAggregationMessage(request)\n"
            "                                          : buildTwapSaleAggregationPushMessage(userId, stock_code, cmd);\n"
            "            twap_profile_session_build_us += twapProfileNowUs() - twap_profile_build_start;\n",
            "session build",
        )
    elif cached_stock_build in text:
        text = replace_once(
            text,
            cached_stock_build,
            "        long long twap_profile_session_filter_us = 0;\n"
            "        long long twap_profile_session_build_us = 0;\n"
            "        long long twap_profile_queue_push_us = 0;\n"
            "        size_t twap_profile_pushed = 0;\n"
            "        TwapSalePushMessage stock_change_message;\n"
            "        bool has_stock_change_message = false;\n"
            "        if (!stock_code.empty()) {\n"
            "            const auto twap_profile_build_start = twapProfileNowUs();\n"
            "            stock_change_message = buildTwapSaleAggregationPushMessage(userId, stock_code, cmd);\n"
            "            has_stock_change_message = stock_change_message.success();\n"
            "            twap_profile_session_build_us += twapProfileNowUs() - twap_profile_build_start;\n"
            "        }\n",
            "cached stock build",
        )
        text = replace_once(
            text,
            "            TwapSalePushMessage message = stock_code.empty()\n"
            "                                          ? buildTwapSaleAggregationMessage(request)\n"
            "                                          : stock_change_message;\n",
            "            const auto twap_profile_build_start = twapProfileNowUs();\n"
            "            TwapSalePushMessage message = stock_code.empty()\n"
            "                                          ? buildTwapSaleAggregationMessage(request)\n"
            "                                          : stock_change_message;\n"
            "            if (stock_code.empty()) {\n"
            "                twap_profile_session_build_us += twapProfileNowUs() - twap_profile_build_start;\n"
            "            }\n",
            "cached empty stock build",
        )
        text = replace_once(
            text,
            "        twapProfileLog(\"twap.push.session_scan\", twapProfileNowUs() - twap_profile_session_scan_start, target_sessions.size());\n\n"
            "        long long twap_profile_session_filter_us = 0;\n"
            "        long long twap_profile_session_build_us = 0;\n"
            "        long long twap_profile_queue_push_us = 0;\n"
            "        size_t twap_profile_pushed = 0;\n",
            "        twapProfileLog(\"twap.push.session_scan\", twapProfileNowUs() - twap_profile_session_scan_start, target_sessions.size());\n\n",
            "cached duplicate accumulator cleanup",
        )
    else:
        raise RuntimeError("instrumentation anchor not found: session build")

    old_queue_push = (
        "            std::lock_guard<std::mutex> lock(session->queue_mutex);\n"
        "            if (session->active) {\n"
        "                session->twap_sale_message_queue[client_id].push(message);\n"
        "            }\n"
        "        }\n"
    )
    enqueue_queue_push = (
        "            std::lock_guard<std::mutex> lock(session->queue_mutex);\n"
        "            if (session->active) {\n"
        "                session->twap_sale_message_queue[client_id].enqueue(message);\n"
        "            }\n"
        "        }\n"
    )
    queue_anchor = old_queue_push if old_queue_push in text else enqueue_queue_push
    queue_method = "push" if queue_anchor == old_queue_push else "enqueue"
    text = replace_once(
        text,
        queue_anchor,
        "            const auto twap_profile_queue_start = twapProfileNowUs();\n"
        "            std::lock_guard<std::mutex> lock(session->queue_mutex);\n"
        "            if (session->active) {\n"
        f"                session->twap_sale_message_queue[client_id].{queue_method}(message);\n"
        "                ++twap_profile_pushed;\n"
        "            }\n"
        "            twap_profile_queue_push_us += twapProfileNowUs() - twap_profile_queue_start;\n"
        "        }\n"
        "        twapProfileLog(\"twap.push.session_filter\", twap_profile_session_filter_us, target_sessions.size());\n"
        "        twapProfileLog(\"twap.push.message_build_for_sessions\", twap_profile_session_build_us, target_sessions.size());\n"
        "        twapProfileLog(\"twap.push.queue_push\", twap_profile_queue_push_us, twap_profile_pushed);\n"
        "        twapProfileLog(\"twap.push.fanout_total\", twapProfileNowUs() - twap_profile_fanout_start, twap_profile_pushed);\n",
        "queue push",
    )
    path.write_text(text, encoding="utf-8")


def inject_cfg_loader_cpp(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = replace_once(
        text,
        '#include "PsiCfgLoader.h"\n',
        '#include "PsiCfgLoader.h"\n' + CFG_HELPERS + "\n",
        "cfg helpers",
    )
    text = replace_once(
        text,
        "std::string PsiCfgLoader::twapSalePositionAggregationPushToJson(const std::string &cmd, const TwapSaleStockPositionAggregationDb &aggregation, const std::string &/*userId*/) {\n"
        "\trapidjson::Document document;\n",
        "std::string PsiCfgLoader::twapSalePositionAggregationPushToJson(const std::string &cmd, const TwapSaleStockPositionAggregationDb &aggregation, const std::string &/*userId*/) {\n"
        "\tconst auto twap_cfg_profile_total_start = twapCfgProfileNowUs();\n"
        "\trapidjson::Document document;\n",
        "cfg total start",
    )
    text = replace_once(
        text,
        "\trapidjson::Value data(rapidjson::kObjectType);\n"
        "\taddTwapSalePositionJsonFields(data, aggregation.position, allocator, false);\n\n"
        "\trapidjson::Value subPositionInfoList(rapidjson::kArrayType);\n",
        "\trapidjson::Value data(rapidjson::kObjectType);\n"
        "\tconst auto twap_cfg_profile_summary_start = twapCfgProfileNowUs();\n"
        "\taddTwapSalePositionJsonFields(data, aggregation.position, allocator, false);\n"
        "\ttwapCfgProfileLog(\"twap.push.json.add_summary_fields\", twapCfgProfileNowUs() - twap_cfg_profile_summary_start, 1);\n\n"
        "\tlong long twap_cfg_profile_subposition_us = 0;\n"
        "\trapidjson::Value subPositionInfoList(rapidjson::kArrayType);\n",
        "cfg summary",
    )
    text = replace_once(
        text,
        "\tfor (const auto &subPosition: aggregation.subPositionInfo) {\n"
        "\t\trapidjson::Value subObj(rapidjson::kObjectType);\n"
        "\t\taddTwapSalePositionJsonFields(subObj, subPosition, allocator, true);\n"
        "\t\tsubPositionInfoList.PushBack(subObj, allocator);\n"
        "\t}\n",
        "\tfor (const auto &subPosition: aggregation.subPositionInfo) {\n"
        "\t\tconst auto twap_cfg_profile_subposition_start = twapCfgProfileNowUs();\n"
        "\t\trapidjson::Value subObj(rapidjson::kObjectType);\n"
        "\t\taddTwapSalePositionJsonFields(subObj, subPosition, allocator, true);\n"
        "\t\tsubPositionInfoList.PushBack(subObj, allocator);\n"
        "\t\ttwap_cfg_profile_subposition_us += twapCfgProfileNowUs() - twap_cfg_profile_subposition_start;\n"
        "\t}\n"
        "\ttwapCfgProfileLog(\"twap.push.json.subposition_fields\", twap_cfg_profile_subposition_us, aggregation.subPositionInfo.size());\n",
        "cfg subposition",
    )
    text = replace_once(
        text,
        "\trapidjson::StringBuffer buffer;\n"
        "\trapidjson::Writer<rapidjson::StringBuffer> writer(buffer);\n"
        "\tdocument.Accept(writer);\n"
        "\treturn buffer.GetString();\n",
        "\tconst auto twap_cfg_profile_write_start = twapCfgProfileNowUs();\n"
        "\trapidjson::StringBuffer buffer;\n"
        "\trapidjson::Writer<rapidjson::StringBuffer> writer(buffer);\n"
        "\tdocument.Accept(writer);\n"
        "\tstd::string twap_cfg_profile_result = buffer.GetString();\n"
        "\ttwapCfgProfileLog(\"twap.push.json.write_string\", twapCfgProfileNowUs() - twap_cfg_profile_write_start, twap_cfg_profile_result.size());\n"
        "\ttwapCfgProfileLog(\"twap.push.json.total\", twapCfgProfileNowUs() - twap_cfg_profile_total_start, aggregation.subPositionInfo.size());\n"
        "\treturn twap_cfg_profile_result;\n",
        "cfg write",
    )
    path.write_text(text, encoding="utf-8")


def instrument_workspace(workspace: Path) -> None:
    inject_service_cpp(workspace / "PsiGrpcServer" / "twap_sale_service.cpp")
    inject_cfg_loader_cpp(workspace / "PsiUtils" / "PsiCfgLoader.cpp")


PROFILE_RE = re.compile(r"\[TWAP_PROFILE\]\s+stage=(?P<stage>\S+)\s+elapsed_us=(?P<elapsed>-?\d+)\s+count=(?P<count>\d+)")


def parse_profile_text(text: str) -> dict[str, list[int]]:
    samples: dict[str, list[int]] = {}
    for match in PROFILE_RE.finditer(text):
        stage = match.group("stage")
        elapsed = int(match.group("elapsed"))
        if elapsed < 0:
            continue
        samples.setdefault(stage, []).append(elapsed)
    return samples


def percentile(sorted_values: list[int], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * pct
    lower = int(pos)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = pos - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def rows_from_samples(samples: dict[str, list[int]], source: str) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    runtime_rows: list[dict[str, str]] = []
    profile_rows: list[dict[str, str]] = []
    for stage, values in samples.items():
        ordered = sorted(values)
        total_us = sum(values)
        count = len(values)
        meta = STAGE_META.get(stage, {})
        runtime_rows.append(
            {
                "stage": stage,
                "total_us": str(total_us),
                "total_ms": f"{total_us / 1000.0:.3f}",
                "count": str(count),
                "avg_us": f"{statistics.mean(values):.3f}",
                "avg_ms": f"{statistics.mean(values) / 1000.0:.6f}",
                "p50_us": f"{percentile(ordered, 0.50):.3f}",
                "p95_us": f"{percentile(ordered, 0.95):.3f}",
                "max_us": str(max(values)),
                "source": source,
                "touched_files": meta.get("files", ""),
                "symbols": meta.get("symbols", ""),
                "notes": meta.get("risk", "medium"),
            }
        )
        profile_rows.append(
            {
                "stage": stage,
                "total_ms": f"{total_us / 1000.0:.3f}",
                "count": str(count),
                "avg_ms": f"{statistics.mean(values) / 1000.0:.6f}",
                "source": source,
                "touched_files": meta.get("files", ""),
                "symbols": meta.get("symbols", ""),
                "notes": f"{meta.get('risk', 'medium')}; runtime_stage_profile",
            }
        )
    profile_rows.sort(key=lambda row: float(row["total_ms"]), reverse=True)
    runtime_rows.sort(key=lambda row: float(row["total_ms"]), reverse=True)
    top_total = float(profile_rows[0]["total_ms"]) if profile_rows else 1.0
    hotspot_rows: list[dict[str, str]] = []
    actionable_rows = [
        row
        for row in profile_rows
        if not (STAGE_META.get(row["stage"], {}).get("risk") == "diagnostic")
    ]
    actionable_top_total = float(actionable_rows[0]["total_ms"]) if actionable_rows else top_total
    for rank, row in enumerate(actionable_rows, start=1):
        total_ms = float(row["total_ms"])
        hotspot_rows.append(
            {
                "rank": str(rank),
                "stage": row["stage"],
                "total_ms": row["total_ms"],
                "avg_ms": row["avg_ms"],
                "count": row["count"],
                "score": f"{total_ms / actionable_top_total:.6f}" if actionable_top_total else "0.000000",
                "notes": row["notes"],
                "touched_files": row["touched_files"],
                "symbols": row["symbols"],
                "expected_delta_seconds": f"{(total_ms / 1000.0) * 0.05:.6f}",
            }
        )
    return runtime_rows, profile_rows, hotspot_rows


def remote_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def sync_workspace(remote_host: str, workspace: Path, remote_workspace: str) -> None:
    with tempfile.TemporaryDirectory(prefix="twap_profile_sync_") as tmp:
        archive_base = Path(tmp) / workspace.name
        archive_path = Path(shutil.make_archive(str(archive_base), "gztar", root_dir=workspace))
        remote_archive = f"/tmp/{archive_path.name}"
        scp = run(["scp", str(archive_path), f"{remote_host}:{remote_archive}"], timeout=300)
        if scp.returncode != 0:
            raise RuntimeError(f"scp workspace failed:\n{scp.stdout}")
    parent = str(Path(remote_workspace).parent).replace("\\", "/")
    command = (
        f"rm -rf {remote_quote(remote_workspace)} && "
        f"mkdir -p {remote_quote(parent)} {remote_quote(remote_workspace)} && "
        f"tar -xzf {remote_quote(remote_archive)} -C {remote_quote(remote_workspace)} && "
        f"rm -f {remote_quote(remote_archive)}"
    )
    result = run(["ssh", remote_host, command], timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"remote unpack failed:\n{result.stdout}")


def run_remote_profile(args: argparse.Namespace, remote_workspace: str, remote_run_dir: str, run_dir: Path) -> None:
    env = {
        "RUN_ID": run_dir.name,
        "RUN_DIR": remote_run_dir,
        "ROOT": remote_workspace,
        "ENV_FILE": args.env_file,
        "ENDPOINT": args.endpoint,
        "USER_ID": args.user_id,
        "MEASURE_CASES": args.measure_cases,
        "BUILD_TARGETS": args.build_targets,
        "CANDIDATE_ID": "twap_runtime_stage_profile",
        "TWAP_CORRECTNESS_MODE": args.correctness_mode,
    }
    env_prefix = " ".join(f"{key}={remote_quote(value)}" for key, value in env.items() if value)
    command = (
        f"cd {remote_quote(args.remote_hft_root)} && "
        f"{env_prefix} bash scripts/twap_headless_remote.sh"
    )
    log_path = run_dir / "remote_profile.log"
    result = run(["ssh", args.remote_host, command], timeout=args.remote_timeout_seconds)
    log_path.write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"remote profile run failed rc={result.returncode}; see {log_path}")


def fetch_remote_run(remote_host: str, remote_run_dir: str, local_dir: Path) -> None:
    local_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"/tmp/{local_dir.name}.tgz"
    command = f"tar -czf {remote_quote(archive_name)} -C {remote_quote(remote_run_dir)} ."
    result = run(["ssh", remote_host, command], timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"remote archive failed:\n{result.stdout}")
    archive_path = local_dir.parent / f"{local_dir.name}.tgz"
    scp = run(["scp", f"{remote_host}:{archive_name}", str(archive_path)], timeout=300)
    if scp.returncode != 0:
        raise RuntimeError(f"scp remote run failed:\n{scp.stdout}")
    with tarfile.open(archive_path, "r:gz") as tar:
        try:
            tar.extractall(local_dir, filter="data")
        except TypeError:
            tar.extractall(local_dir)
    archive_path.unlink(missing_ok=True)
    run(["ssh", remote_host, f"rm -f {remote_quote(archive_name)}"], timeout=60)


def write_summary(run_dir: Path, remote_run_dir: str, remote_workspace: str, profile_rows: list[dict[str, str]]) -> None:
    payload = {
        "schema": "twap_runtime_stage_profile_v1",
        "run_dir": str(run_dir),
        "remote_run_dir": remote_run_dir,
        "remote_workspace": remote_workspace,
        "stage_count": len(profile_rows),
        "top_stages": profile_rows[:8],
        "profile_path": str(run_dir / "profile.tsv"),
        "hotspots_path": str(run_dir / "hotspots.tsv"),
        "runtime_stage_profile_path": str(run_dir / "runtime_stage_profile.tsv"),
    }
    (run_dir / "runtime_stage_profile_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TWAP runtime stage profiling without modifying Code2 baseline.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--remote-host", default="root@192.168.170.62")
    parser.add_argument("--remote-hft-root", default="/root/work/HFT-wf")
    parser.add_argument("--remote-run-root", default="/root/work/psi_experiments/runs")
    parser.add_argument("--remote-workspace-root", default="/root/work/psi_experiments/local_agent_candidates")
    parser.add_argument("--env-file", default="/root/work/.toolchain/psi-env-code2.sh")
    parser.add_argument("--endpoint", default="192.168.170.62:18321")
    parser.add_argument("--user-id", default="dc548fe6083e4523a918aaef1a68b857")
    parser.add_argument("--measure-cases", default="100:50:120 500:20:180 1000:20:240 500:5:240")
    parser.add_argument("--build-targets", default="PsiGrpcServer PsiTraderRunner twap_current_task_runtime_test twap_position_push_perf_test")
    parser.add_argument("--correctness-mode", default="push_only", choices=("push_only", "skip"))
    parser.add_argument("--remote-timeout-seconds", type=int, default=7200)
    parser.add_argument("--parse-only-log", type=Path, default=None)
    parser.add_argument(
        "--instrument-only",
        action="store_true",
        help="copy and instrument the source workspace, then stop before syncing or running remote Linux",
    )
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.parse_only_log:
        text = args.parse_only_log.read_text(encoding="utf-8", errors="replace")
        samples = parse_profile_text(text)
        runtime_rows, profile_rows, hotspot_rows = rows_from_samples(samples, "twap_runtime_profile:parse_only")
        write_tsv(run_dir / "runtime_stage_profile.tsv", runtime_rows, RUNTIME_PROFILE_FIELDS)
        write_tsv(run_dir / "profile.tsv", profile_rows, PROFILE_FIELDS)
        write_tsv(run_dir / "hotspots.tsv", hotspot_rows, HOTSPOT_FIELDS)
        write_summary(run_dir, "", "", profile_rows)
        return 0

    source_root = args.source_root.resolve()
    workspace = run_dir / "instrumented_workspace"
    copy_source(source_root, workspace)
    instrument_workspace(workspace)
    (run_dir / "instrumentation_manifest.json").write_text(
        json.dumps(
            {
                "source_root": str(source_root),
                "workspace": str(workspace),
                "instrumented_files": [
                    "PsiGrpcServer/twap_sale_service.cpp",
                    "PsiUtils/PsiCfgLoader.cpp",
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.instrument_only:
        print(f"instrumented_workspace={workspace}")
        return 0

    remote_workspace = f"{args.remote_workspace_root.rstrip('/')}/{run_dir.name}/instrumented_workspace"
    remote_run_dir = f"{args.remote_run_root.rstrip('/')}/{run_dir.name}"
    sync_workspace(args.remote_host, workspace, remote_workspace)
    run_remote_profile(args, remote_workspace, remote_run_dir, run_dir)
    fetched_dir = run_dir / "remote_artifacts"
    fetch_remote_run(args.remote_host, remote_run_dir, fetched_dir)

    profile_text = ""
    for name in ("candidate_runner.out", "candidate_correctness_runner.out"):
        path = fetched_dir / name
        if path.exists():
            profile_text += "\n" + path.read_text(encoding="utf-8", errors="replace")
    samples = parse_profile_text(profile_text)
    if not samples:
        raise RuntimeError(f"no TWAP_PROFILE lines found in {fetched_dir}")
    runtime_rows, profile_rows, hotspot_rows = rows_from_samples(samples, "twap_runtime_profile:remote_linux")
    write_tsv(run_dir / "runtime_stage_profile.tsv", runtime_rows, RUNTIME_PROFILE_FIELDS)
    write_tsv(run_dir / "profile.tsv", profile_rows, PROFILE_FIELDS)
    write_tsv(run_dir / "hotspots.tsv", hotspot_rows, HOTSPOT_FIELDS)
    write_summary(run_dir, remote_run_dir, remote_workspace, profile_rows)
    print(f"profile={run_dir / 'profile.tsv'}")
    print(f"hotspots={run_dir / 'hotspots.tsv'}")
    print(f"runtime_stage_profile={run_dir / 'runtime_stage_profile.tsv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
