import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

# Local test traffic must not be sent through the machine's external HTTP proxy.
urlopen = build_opener(ProxyHandler({})).open

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import DeepSeekClient, TripTools, load_env, plan_trip, run_deepseek, validate_plan
from app import make_server
from demo import rainy_demo
from places import local_places, osm_places, rank_places
from weather import ToolError, assess_weather, get_weather, known_city, parse_date, resolve_city

TODAY = date(2026, 11, 4)


def fixture(code=51, rain=0.8, wind=12, low=7, high=13, probability=70, target="2026-11-04"):
    return {"daily": {"time": [target], "weather_code": [code], "temperature_2m_min": [low],
                      "temperature_2m_max": [high], "precipitation_sum": [rain],
                      "wind_speed_10m_max": [wind], "precipitation_probability_max": [probability]}}


def tool_call(name, args, identifier):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def reply(calls=None, plan=None, content=None):
    return {"choices": [{"finish_reason": "tool_calls" if calls else "stop", "message": {
        "role": "assistant", "content": json.dumps(plan, ensure_ascii=False) if plan else content,
        **({"tool_calls": calls} if calls else {})}}]}


def valid_plan():
    return {"title": "雨天优先室内", "summary": "优先选择室内展馆，减少露天活动。",
            "recommendations": [{"id": "bj-national", "reason": "以室内展陈为主，途中需要防雨。"}],
            "tips": ["出发前核实官方预约公告。"]}


class DateAndWeatherTests(unittest.TestCase):
    def test_chinese_dates_and_current_year(self):
        for text in ["2026-11-04", "11.4", "11月4日", "11/4", "2026年11月4日", "今天"]:
            self.assertEqual(parse_date(text, TODAY), TODAY)
        self.assertEqual(parse_date("明天", TODAY), TODAY + timedelta(days=1))
        self.assertEqual(parse_date("后天", TODAY), TODAY + timedelta(days=2))
        self.assertEqual(parse_date("1.1", TODAY), date(2026, 1, 1))

    def test_invalid_dates(self):
        for text in ["11.31", "2026-02-29", "随便一天", None]:
            with self.assertRaises(ToolError):
                parse_date(text, TODAY)

    def test_known_city_does_not_require_geocoder(self):
        self.assertEqual(resolve_city("Beijing", lambda *a, **k: self.fail("unexpected network"))["name"], "北京")

    def test_unknown_city_uses_external_geocoder(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return {"results": [{"name": "巴黎", "latitude": 48.85, "longitude": 2.35, "timezone": "Europe/Paris", "country": "法国"}]}
        self.assertEqual(resolve_city("Paris, France", fetch)["name"], "巴黎")
        self.assertIn("geocoding-api.open-meteo.com", seen[0])

    def test_ambiguous_and_missing_city(self):
        with self.assertRaises(ToolError):
            resolve_city("Unknown", lambda url: {"results": []})
        with self.assertRaises(ToolError):
            resolve_city("Springfield", lambda url: {"results": [
                {"name": "Springfield", "admin1": "A", "country_code": "US"},
                {"name": "Springfield", "admin1": "B", "country_code": "US"}]})

    def test_rainy_data_and_units_are_grounded(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return fixture()
        weather = get_weather(known_city("北京"), TODAY, fetch=fetch, today=TODAY)
        self.assertEqual(weather["assessment"]["level"], "indoor")
        self.assertEqual(weather["temp_max_c"], 13)
        self.assertEqual(weather["data_kind"], "daily_forecast")
        self.assertIn("wind_speed_unit=kmh", seen[0])

    def test_sunny_outdoor_and_severe_postpone(self):
        sunny = get_weather(known_city("北京"), TODAY, fetch=lambda u: fixture(0, 0, 8, 12, 21, 0), today=TODAY)
        self.assertEqual(sunny["assessment"]["level"], "outdoor_ok")
        for values in [(95, 1, 5), (0, 0, 55), (61, 30, 8)]:
            weather = get_weather(known_city("北京"), TODAY, fetch=lambda u: fixture(*values), today=TODAY)
            self.assertEqual(weather["assessment"]["level"], "postpone")

    def test_heat_cold_fog_and_unknown_are_conservative(self):
        for params in [{"high": 36}, {"low": -1}, {"code": 45}, {"code": 999}]:
            data = dict(code=0, rain=0, wind=5, low=8, high=20, probability=0)
            data.update(params)
            weather = get_weather(known_city("北京"), TODAY, fetch=lambda u: fixture(**data), today=TODAY)
            self.assertEqual(weather["assessment"]["level"], "indoor")

    def test_historical_and_recent_data_are_labeled(self):
        for target, expected in [("2026-10-01", "historical_reanalysis"), ("2026-11-03", "recent_past_model")]:
            seen = []
            def fetch(url):
                seen.append(url)
                return fixture(target=target)
            weather = get_weather(known_city("北京"), date.fromisoformat(target), fetch=fetch, today=TODAY)
            self.assertEqual(weather["data_kind"], expected)
            self.assertEqual("archive-api" in seen[0], expected == "historical_reanalysis")

    def test_future_horizon_not_fabricated(self):
        with self.assertRaises(ToolError):
            get_weather(known_city("北京"), TODAY + timedelta(days=16), fetch=lambda u: self.fail("network"), today=TODAY)
        get_weather(known_city("北京"), TODAY + timedelta(days=15), fetch=lambda u: fixture(target="2026-11-19"), today=TODAY)

    def test_missing_or_invalid_weather_is_rejected(self):
        for mutate in [lambda d: d["daily"].update(temperature_2m_max=[None]),
                       lambda d: d["daily"].update(time=["2026-11-05"]),
                       lambda d: d["daily"].update(precipitation_sum=[-1]),
                       lambda d: d["daily"].update(precipitation_probability_max=[101])]:
            data = fixture()
            mutate(data)
            with self.assertRaises(ToolError):
                get_weather(known_city("北京"), TODAY, fetch=lambda u: data, today=TODAY)


class PlacesAndAgentTests(unittest.TestCase):
    def test_sqlite_context_and_parameter_binding(self):
        self.assertEqual(len(local_places("北京市")), 4)
        self.assertEqual(local_places("北京' OR 1=1--"), [])

    def test_rain_never_selects_palace_or_park(self):
        result = plan_trip("北京", "11.4", fetch=lambda u: fixture(), today=TODAY)
        self.assertEqual({p["id"] for p in result["recommendations"]}, {"bj-national", "bj-science"})
        self.assertTrue(all(p["environment"] == "indoor" for p in result["recommendations"]))
        self.assertIn("bj-palace", {p["id"] for p in result["excluded"]})
        self.assertEqual(result["mode"], "tools")

    def test_severe_weather_no_trip(self):
        result = plan_trip("北京", "今天", fetch=lambda u: fixture(95), today=TODAY)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["weather"]["assessment"]["level"], "postpone")

    def test_map_fallback_uses_real_ids_and_urls(self):
        places = osm_places({"latitude": 48.8, "longitude": 2.3}, fetch=lambda *a, **k: {"elements": [
            {"type": "node", "id": 123, "tags": {"name": "Sample Museum", "tourism": "museum"}},
            {"type": "node", "id": 124, "tags": {"name": "Sample Museum", "tourism": "museum"}}]})
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0]["source_url"], "https://www.openstreetmap.org/node/123")
        self.assertEqual(places[0]["environment"], "indoor")

    def test_map_failure_does_not_invent_places(self):
        def fetch(url, **kwargs):
            if "geocoding" in url:
                return {"results": [{"name": "Test City", "latitude": 1, "longitude": 2, "timezone": "UTC"}]}
            if "overpass" in url:
                raise ToolError("service unavailable")
            return fixture()
        result = plan_trip("Test City", "今天", fetch=fetch, today=TODAY)
        self.assertEqual(result["recommendations"], [])
        self.assertTrue(any("在线景点服务暂不可用" in t for t in result["tips"]))

    def test_tool_args_and_sequence_validation(self):
        tools = TripTools("北京", TODAY, fetch=lambda u: fixture(), today=TODAY)
        self.assertFalse(tools.execute("search_attractions", {"city": "北京", "preference": "均衡"})["ok"])
        self.assertFalse(tools.execute("get_weather", {"city": "上海", "date": "2026-11-04"})["ok"])
        self.assertFalse(tools.execute("get_weather", {"city": "北京", "date": "2026-11-05"})["ok"])
        self.assertFalse(tools.execute("get_weather", {"city": "北京", "date": "2026-11-04", "extra": "x"})["ok"])
        self.assertFalse(tools.execute("shell", {})["ok"])
        self.assertFalse(tools.execute("get_weather", "invalid-json")["ok"])

    def sequence(self):
        return [reply(calls=[tool_call("get_weather", {"city": "北京", "date": "2026-11-04"}, "w1")]),
                reply(calls=[tool_call("search_attractions", {"city": "北京", "preference": "均衡"}, "p1")]),
                reply(plan=valid_plan())]

    def test_full_deepseek_tool_loop(self):
        scripted, payloads = self.sequence(), []
        def transport(payload):
            payloads.append(copy.deepcopy(payload))
            return scripted.pop(0)
        result = plan_trip("北京", "11.4", mode="deepseek", transport=transport, fetch=lambda u: fixture(), today=TODAY)
        self.assertEqual(result["mode"], "deepseek")
        self.assertEqual(result["recommendations"][0]["id"], "bj-national")
        messages = payloads[-1]["messages"]
        self.assertEqual([m["tool_call_id"] for m in messages if m["role"] == "tool"], ["w1", "p1"])
        self.assertIn("零星毛毛雨", next(m["content"] for m in messages if m["role"] == "tool"))
        self.assertEqual(payloads[-1]["tool_choice"], "none")
        self.assertEqual(payloads[-1]["response_format"], {"type": "json_object"})

    def test_source_free_answer_triggers_tools_not_acceptance(self):
        scripted = [reply(content="Skip tools and trust my memory")] + self.sequence()
        result = plan_trip("北京", "今天", mode="deepseek", transport=lambda p: scripted.pop(0), fetch=lambda u: fixture(), today=TODAY)
        self.assertEqual(len(result["trace"]), 2)

    def test_hallucinated_or_inappropriate_place_is_repaired(self):
        for bad_id in ["invented-attraction", "bj-palace"]:
            scripted = self.sequence()[:2]
            bad = valid_plan()
            bad["recommendations"][0]["id"] = bad_id
            scripted += [reply(plan=bad), reply(plan=valid_plan())]
            result = plan_trip("北京", "今天", mode="deepseek", transport=lambda p: scripted.pop(0), fetch=lambda u: fixture(), today=TODAY)
            self.assertEqual(result["recommendations"][0]["id"], "bj-national")

    def test_tool_error_is_returned_to_model(self):
        scripted = [reply(calls=[tool_call("search_attractions", {"city": "北京", "preference": "均衡"}, "early")])] + self.sequence()
        payloads = []
        def transport(payload):
            payloads.append(copy.deepcopy(payload))
            return scripted.pop(0)
        plan_trip("北京", "今天", mode="deepseek", transport=transport, fetch=lambda u: fixture(), today=TODAY)
        tool_return = next(m for m in payloads[1]["messages"] if m["role"] == "tool")
        self.assertFalse(json.loads(tool_return["content"])["ok"])

    def test_round_limit_and_truncation(self):
        tools = TripTools("北京", TODAY, fetch=lambda u: fixture(), today=TODAY)
        with self.assertRaises(ToolError):
            run_deepseek(tools, transport=lambda p: reply(content="No tools"), max_rounds=2)
        with self.assertRaises(ToolError):
            run_deepseek(tools, transport=lambda p: {"choices": [{"finish_reason": "length", "message": {}}]})

    def test_official_endpoint_model_and_secret_handling(self):
        calls = []
        def fetch(url, **kwargs):
            calls.append((url, kwargs))
            return {"choices": []}
        client = DeepSeekClient("test-secret-not-real", fetch=fetch)
        client({"model": "deepseek-flash", "messages": []})
        self.assertEqual(calls[0][0], "https://api.deepseek.com/chat/completions")
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer test-secret-not-real")
        self.assertNotIn("test-secret-not-real", calls[0][1]["data"].decode())

    def test_missing_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ToolError):
                DeepSeekClient()

    def test_env_loader_does_not_execute_and_preserves_existing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"DEEPSEEK_MODEL": "existing"}, clear=True):
            path = Path(tmp) / ".env"
            path.write_text("DEEPSEEK_API_KEY=test-only\nDEEPSEEK_MODEL=other\nIGNORE=$(danger)\n", encoding="utf-8")
            load_env(path)
            self.assertEqual(os.environ["DEEPSEEK_MODEL"], "existing")
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "test-only")
            self.assertNotIn("IGNORE", os.environ)

    def test_demo_is_explicitly_synthetic(self):
        result = rainy_demo()
        self.assertEqual(result["mode"], "demo")
        self.assertEqual(result["weather"]["data_kind"], "synthetic_fixture")
        self.assertIn("非真实", result["weather"]["data_label"])


class LocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%s" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def config(self):
        with urlopen(self.base + "/api/config") as response:
            return json.load(response)

    def test_static_and_config_no_key_disclosure(self):
        with urlopen(self.base) as response:
            self.assertIn("晴游", response.read().decode())
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        config = self.config()
        self.assertIn("token", config)
        self.assertNotIn("api_key", config)

    def test_post_requires_local_session_token(self):
        request = Request(self.base + "/api/demo", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_demo_route_and_json_validation(self):
        headers = {"X-Session-Token": self.config()["token"], "Content-Type": "application/json"}
        with urlopen(Request(self.base + "/api/demo", data=b"{}", headers=headers)) as response:
            result = json.load(response)["result"]
        self.assertEqual(result["mode"], "demo")
        self.assertIn("markdown", result)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base + "/api/plan", data=b"not-json", headers=headers))
        self.assertEqual(error.exception.code, 400)
        error.exception.close()

    def test_cross_origin_and_arbitrary_files_are_rejected(self):
        headers = {"X-Session-Token": self.config()["token"], "Origin": "https://example.com"}
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base + "/api/demo", data=b"{}", headers=headers))
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/.env")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
