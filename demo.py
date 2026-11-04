"""Explicitly synthetic rainy-day scenario; never used as a live API fallback."""
from datetime import date
from agent import TripTools, build_result, direct_tools_plan


def rainy_demo():
    def fixture_fetch(url, **kwargs):
        return {"daily": {"time": ["2026-11-04"], "weather_code": [51],
                          "temperature_2m_min": [7], "temperature_2m_max": [13],
                          "precipitation_sum": [0.8], "precipitation_probability_max": [70],
                          "wind_speed_10m_max": [12]}}
    tools = TripTools("北京", date(2026, 11, 4), "均衡", fetch=fixture_fetch, today=date(2026, 11, 4))
    plan = direct_tools_plan(tools)
    tools.weather.update({"data_kind": "synthetic_fixture", "data_label": "示例数据，非真实天气", "source_name": "项目内置测试样例",
                          "source_url": "", "retrieved_at": "未查询真实天气服务"})
    result = build_result(tools, plan, "demo")
    result["mode_label"] = "雨天情景演示（模拟天气，未调用DeepSeek）"
    result["title"] = "北京零星小雨，优先室内游玩"
    result["tips"].insert(0, "本页天气是人工测试样例，不代表北京2026年11月4日的真实天气。")
    return result
