"""Geocoding, dated weather queries and transparent outdoor-suitability rules."""
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
CHINA = timezone(timedelta(hours=8))


class ToolError(ValueError):
    pass


def now_iso():
    return datetime.now(CHINA).isoformat(timespec="seconds")


def today_china():
    return datetime.now(CHINA).date()


def fetch_json(url, *, data=None, headers=None, timeout=25):
    request_headers = {"User-Agent": "WeatherTripStudentAgent/1.0 (educational local application)",
                       "Accept": "application/json"}
    request_headers.update(headers or {})
    try:
        with urlopen(Request(url, data=data, headers=request_headers), timeout=timeout) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ToolError("服务返回数据过大")
        return json.loads(raw)
    except HTTPError as exc:
        hints = {400: "请求参数无效", 401: "API密钥无效", 402: "账户余额不足",
                 403: "服务拒绝访问", 404: "接口或模型不可用", 429: "请求过多或配额不足"}
        raise ToolError("外部服务 HTTP %s：%s" % (exc.code, hints.get(exc.code, "服务暂时不可用"))) from None
    except (URLError, TimeoutError, OSError):
        raise ToolError("外部服务连接失败或超时，请检查网络后重试") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ToolError("外部服务未返回有效JSON") from None


def catalog():
    return json.loads((ROOT / "data" / "attractions.json").read_text(encoding="utf-8"))


def known_city(value):
    text = value.strip().casefold()
    for city in catalog()["cities"]:
        if text in [v.casefold() for v in [city["name"]] + city["aliases"]]:
            return city
    return None


def resolve_city(value, fetch=fetch_json):
    if not isinstance(value, str) or not 2 <= len(value.strip()) <= 100:
        raise ToolError("请输入2至100字符的城市名")
    known = known_city(value)
    if known:
        return {key: known[key] for key in ("name", "latitude", "longitude", "timezone", "country")} | {"location_source": "本地城市坐标表"}
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({"name": value.strip(), "count": 8, "language": "zh"})
    results = fetch(url).get("results", [])
    if not results:
        raise ToolError("未找到城市，请换用完整城市名或英文名")
    # Refuse ambiguous matches rather than silently choosing an unrelated place.
    populated = [r for r in results if r.get("feature_code") in {"PPLC", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPL"}]
    candidates = populated or results
    first = candidates[0]
    if len(candidates) > 1:
        second = candidates[1]
        same_name = first.get("name") == second.get("name")
        different_area = (first.get("country_code"), first.get("admin1")) != (second.get("country_code"), second.get("admin1"))
        if same_name and different_area:
            raise ToolError("城市名有歧义，请补充省份或国家，例如 Paris, France")
    return {"name": first["name"], "latitude": first["latitude"], "longitude": first["longitude"],
            "timezone": first.get("timezone", "auto"), "country": first.get("country", ""),
            "admin1": first.get("admin1", ""), "location_source": url}


def parse_date(value, today=None):
    current = today or today_china()
    if not isinstance(value, str):
        raise ToolError("日期必须是文本")
    value = value.strip()
    if value in {"", "今天", "today"}:
        return current
    if value in {"明天", "tomorrow"}:
        return current + timedelta(days=1)
    if value == "后天":
        return current + timedelta(days=2)
    match = re.fullmatch(r"(?:(\d{4})[-/.年])?(\d{1,2})[-/.月](\d{1,2})日?", value)
    if not match:
        raise ToolError("日期请用 YYYY-MM-DD、11.4、11月4日或今天/明天")
    try:
        return date(int(match[1] or current.year), int(match[2]), int(match[3]))
    except ValueError:
        raise ToolError("日期不存在，请检查年月日") from None


WEATHER_CODES = {0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴", 45: "雾", 48: "雾凇",
                 51: "零星毛毛雨", 53: "毛毛雨", 55: "较强毛毛雨", 56: "轻微冻毛毛雨", 57: "较强冻毛毛雨",
                 61: "小雨", 63: "中雨", 65: "大雨", 66: "轻微冻雨", 67: "较强冻雨",
                 71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪", 80: "零星阵雨", 81: "阵雨", 82: "强阵雨",
                 85: "零星阵雪", 86: "强阵雪", 95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴较大冰雹"}


def assess_weather(weather):
    """Project heuristics, not official alerts or clinical safety thresholds."""
    code = weather["weather_code"]
    rain = weather["precipitation_mm"]
    wind = weather["wind_max_kmh"]
    low, high = weather["temp_min_c"], weather["temp_max_c"]
    probability = weather.get("rain_probability_pct")
    severe = code in {56, 57, 66, 67, 75, 82, 86, 95, 96, 99} or rain >= 25 or wind >= 50
    wet = code in set(range(51, 68)) | {71, 73, 75, 77, 80, 81, 82, 85, 86} or rain >= 0.2 or (probability is not None and probability >= 60)
    reasons = []
    if severe:
        reasons.append("存在雷暴、冻雨、强降水、强降雪或大风风险，建议推迟非必要游玩。室内目的地也不能消除途中风险。")
        level, label = "postpone", "建议推迟游玩"
    elif wet:
        reasons.append("有降水或较高降水概率，优先室内；不建议登山、水上活动和长时间露天运动。短时步行需结合临近天气。")
        level, label = "indoor", "优先室内游玩"
    elif high >= 35 or low <= 0 or wind >= 30 or code in {45, 48} or code not in WEATHER_CODES:
        reasons.append("有高温、低温、较大风或雾的影响，优先室内，减少持续户外暴露。")
        level, label = "indoor", "优先室内，减少暴露"
    else:
        reasons.append("日级天气指标对一般城市游览较友好，可考虑室内或短时户外行程。")
        level, label = "outdoor_ok", "可考虑户外游览"
    return {"level": level, "label": label, "reasons": reasons,
            "rule_note": "项目经验规则，不是官方气象预警；日预报不能保证每个时段的天气。",
            "tips": ["出发前复查临近天气和当地气象预警。", "门票、预约名额和当日开放状态尚未实时核实，请查看景点官方公告。"]}


def get_weather(city, target, *, fetch=fetch_json, today=None):
    current = today or today_china()
    offset = (target - current).days
    if offset > 15:
        raise ToolError("超过可用预报范围：最多查询今天起16天；不能据季节均值编造当天预报")
    if target < date(1940, 1, 1):
        raise ToolError("历史天气只支持1940年及以后日期")
    historic = offset < -5
    endpoint = "https://archive-api.open-meteo.com/v1/archive" if historic else "https://api.open-meteo.com/v1/forecast"
    fields = ["weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max"]
    if not historic:
        fields.append("precipitation_probability_max")
    params = {"latitude": city["latitude"], "longitude": city["longitude"], "timezone": city["timezone"],
              "start_date": target.isoformat(), "end_date": target.isoformat(), "daily": ",".join(fields),
              "wind_speed_unit": "kmh", "temperature_unit": "celsius", "precipitation_unit": "mm"}
    url = endpoint + "?" + urlencode(params)
    data = fetch(url)
    daily = data.get("daily", {})
    times = daily.get("time", [])
    if target.isoformat() not in times:
        raise ToolError("天气服务未返回该日期的数据，不能生成当天结论")
    index = times.index(target.isoformat())
    def number(field, required=True):
        values = daily.get(field, [])
        value = values[index] if len(values) > index else None
        if value is None and not required:
            return None
        if type(value) not in {int, float} or not math.isfinite(value):
            raise ToolError("天气字段缺失或无效：" + field)
        return value
    code = int(number("weather_code"))
    weather = {"city": city, "date": target.isoformat(), "weather_code": code,
               "description": WEATHER_CODES.get(code, "未知天气代码 %s" % code),
               "temp_min_c": number("temperature_2m_min"), "temp_max_c": number("temperature_2m_max"),
               "precipitation_mm": number("precipitation_sum"), "wind_max_kmh": number("wind_speed_10m_max"),
               "rain_probability_pct": number("precipitation_probability_max", False),
               "data_kind": "historical_reanalysis" if historic else ("recent_past_model" if offset < 0 else "daily_forecast"),
               "data_label": "历史再分析（非实时观测）" if historic else ("近期历史模型数据" if offset < 0 else "当日天气预报"),
               "retrieved_at": now_iso(), "source_name": "Open-Meteo", "source_url": url,
               "source_docs": "https://open-meteo.com/en/docs"}
    if weather["temp_min_c"] > weather["temp_max_c"] or weather["precipitation_mm"] < 0 or weather["wind_max_kmh"] < 0:
        raise ToolError("天气数值不合理")
    probability = weather["rain_probability_pct"]
    if probability is not None and not 0 <= probability <= 100:
        raise ToolError("降水概率超出范围")
    weather["assessment"] = assess_weather(weather)
    return weather
