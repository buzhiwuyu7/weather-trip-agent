"""Official DeepSeek Chat Completions + standard tool calling orchestration."""
import json
import os
from pathlib import Path

from places import search_attractions
from weather import ToolError, fetch_json, get_weather, known_city, now_iso, parse_date, resolve_city, today_china

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://api.deepseek.com/chat/completions"
PREFERENCES = ("均衡", "室内文化", "户外风景", "亲子轻松")
SYSTEM_PROMPT = (ROOT / "system_prompt.txt").read_text(encoding="utf-8")


def definition(name, description, props):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props,
                           "required": list(props), "additionalProperties": False}}}


TOOLS = [
    definition("get_weather", "查询指定城市和YYYY-MM-DD日期的真实天气。必须先调用此工具，再查景点。",
               {"city": {"type": "string"}, "date": {"type": "string"}}),
    definition("search_attractions", "查询本次城市的真实景点上下文，并按已查询天气筛选。必须先成功调用get_weather。",
               {"city": {"type": "string"}, "preference": {"type": "string", "enum": list(PREFERENCES)}}),
]


def load_env(path=None):
    """Small explicit .env loader; never executes shell expressions."""
    file = Path(path) if path else ROOT / ".env"
    if not file.exists():
        return
    for line in file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"}:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class DeepSeekClient:
    def __init__(self, api_key=None, fetch=fetch_json):
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY", "")).strip()
        if not self.api_key:
            raise ToolError("DeepSeek AI模式需要API密钥。可在页面填写，或配置DEEPSEEK_API_KEY；也可选择实时工具模式。")
        if any(c in self.api_key for c in "\r\n"):
            raise ToolError("API密钥格式无效")
        self.fetch = fetch

    def __call__(self, payload):
        return self.fetch(ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}, timeout=90)


class TripTools:
    def __init__(self, city, target, preference="均衡", fetch=fetch_json, today=None):
        self.city_input, self.target, self.preference = city, target, preference
        self.fetch, self.today = fetch, today or today_china()
        self.city, self.weather, self.attractions = None, None, None
        self.trace = []

    def valid_city(self, name):
        known_expected, known_arg = known_city(self.city_input), known_city(name)
        if name.strip().casefold() == self.city_input.strip().casefold():
            return True
        if self.city and name.strip().casefold() == self.city["name"].casefold():
            return True
        return bool(known_expected and known_arg and known_expected["name"] == known_arg["name"])

    def execute(self, name, arguments):
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            schema = next((s["function"] for s in TOOLS if s["function"]["name"] == name), None)
            if not schema or not isinstance(args, dict) or set(args) != set(schema["parameters"]["properties"]):
                raise ToolError("工具或参数字段无效")
            if any(not isinstance(value, str) or len(value) > 120 for value in args.values()):
                raise ToolError("工具参数必须是长度不超过120的文本")
            if not self.valid_city(args["city"]):
                raise ToolError("工具城市必须与本次用户选择一致")
            if name == "get_weather":
                if args["date"] != self.target.isoformat():
                    raise ToolError("工具日期必须与本次用户选择一致")
                self.city = self.city or resolve_city(self.city_input, self.fetch)
                if self.weather is None:
                    self.weather = get_weather(self.city, self.target, fetch=self.fetch, today=self.today)
                data = self.weather
            else:
                if self.weather is None:
                    raise ToolError("必须先成功查询天气，不能凭空推荐景点")
                if args["preference"] != self.preference:
                    raise ToolError("工具偏好必须与用户选择一致")
                if self.attractions is None:
                    self.attractions = search_attractions(self.city, self.weather, self.preference, fetch=self.fetch)
                data = self.attractions
            result = {"ok": True, "data": data}
        except (ValueError, TypeError, KeyError, OSError) as exc:
            result = {"ok": False, "error": str(exc)[:350]}
        self.trace.append({"tool": name, "arguments": arguments, "result": result})
        return result


def validate_plan(plan, attractions, assessment):
    if not isinstance(plan, dict) or set(plan) != {"title", "summary", "recommendations", "tips"}:
        raise ToolError("最终JSON字段不匹配")
    for name, limit in (("title", 120), ("summary", 2000)):
        if not isinstance(plan[name], str) or not plan[name].strip() or len(plan[name]) > limit:
            raise ToolError("最终结果文字字段无效")
    if not isinstance(plan["tips"], list) or len(plan["tips"]) > 8 or any(not isinstance(t, str) or len(t) > 500 for t in plan["tips"]):
        raise ToolError("出行提示格式无效")
    recommendations = plan["recommendations"]
    allowed = {place["id"] for place in attractions["eligible"]}
    if not isinstance(recommendations, list) or len(recommendations) > 4:
        raise ToolError("推荐数量或格式无效")
    seen = set()
    for recommendation in recommendations:
        if not isinstance(recommendation, dict) or set(recommendation) != {"id", "reason"}:
            raise ToolError("推荐字段无效")
        identifier = recommendation["id"]
        if not isinstance(identifier, str) or identifier not in allowed or identifier in seen:
            raise ToolError("推荐包含未经工具返回、天气不匹配或重复的景点")
        seen.add(identifier)
        if not isinstance(recommendation["reason"], str) or not 1 <= len(recommendation["reason"]) <= 1200:
            raise ToolError("推荐理由无效")
    if assessment["level"] == "postpone" and recommendations:
        raise ToolError("当前天气应推迟游玩")
    if allowed and not recommendations:
        raise ToolError("已有符合条件的景点，请至少选择一处")
    return plan


def run_deepseek(tools, question="", *, transport=None, model=None, max_rounds=8):
    client = transport or DeepSeekClient()
    model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    task = {"city": tools.city_input, "date": tools.target.isoformat(), "preference": tools.preference,
            "request": question[:2000], "date_note": "日期已经按用户选择解析，不要自行改年或改日"}
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(task, ensure_ascii=False)}]
    calls_count = 0
    for _ in range(max_rounds):
        ready = tools.weather is not None and tools.attractions is not None
        payload = {"model": model, "messages": messages, "tools": TOOLS,
                   "thinking": {"type": "disabled"}, "stream": False, "max_tokens": 2500,
                   "tool_choice": "none" if ready else "auto"}
        if ready:
            payload["response_format"] = {"type": "json_object"}
        result = client(payload)
        choices = result.get("choices", [])
        if not choices:
            raise ToolError("DeepSeek没有返回可用结果")
        choice = choices[0]
        if choice.get("finish_reason") in {"length", "content_filter", "insufficient_system_resource"}:
            raise ToolError("DeepSeek结果未完整生成，请缩短请求或稍后重试")
        message = choice.get("message", {})
        calls = message.get("tool_calls") or []
        assistant_message = {"role": "assistant", "content": message.get("content")}
        if calls:
            assistant_message["tool_calls"] = calls
        if message.get("reasoning_content") is not None:
            assistant_message["reasoning_content"] = message["reasoning_content"]
        messages.append(assistant_message)
        if calls:
            for call in calls:
                calls_count += 1
                if calls_count > 12:
                    raise ToolError("超过12次工具调用上限")
                if not isinstance(call.get("id"), str):
                    raise ToolError("DeepSeek工具调用缺少id")
                function = call.get("function", {})
                output = tools.execute(function.get("name"), function.get("arguments", ""))
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(output, ensure_ascii=False)})
            continue
        if not ready:
            messages.append({"role": "user", "content": "尚未取得两个成功工具结果。必须先查本次天气，再查景点，不能直接回答。"})
            continue
        try:
            plan = validate_plan(json.loads(message.get("content") or ""), tools.attractions, tools.weather["assessment"])
            return plan
        except (ValueError, TypeError) as exc:
            messages.append({"role": "user", "content": "最终JSON校验失败：%s。只选eligible中的id并按规定JSON重新输出。" % str(exc)[:200]})
    errors = [event["result"]["error"] for event in tools.trace if not event["result"]["ok"]]
    raise ToolError("达到最大编排轮数，未生成有效建议。" + ("最近工具错误：" + errors[-1] if errors else ""))


def direct_tools_plan(tools):
    weather = tools.execute("get_weather", {"city": tools.city_input, "date": tools.target.isoformat()})
    if not weather["ok"]:
        raise ToolError(weather["error"])
    attractions = tools.execute("search_attractions", {"city": tools.city_input, "preference": tools.preference})
    if not attractions["ok"]:
        raise ToolError(attractions["error"])
    assessment = tools.weather["assessment"]
    return {"title": assessment["label"], "summary": assessment["reasons"][0],
            "recommendations": [{"id": place["id"], "reason": place["fit_reason"]} for place in tools.attractions["eligible"]],
            "tips": []}


def build_result(tools, plan, mode, model=None):
    indexed = {p["id"]: p for p in tools.attractions["eligible"]}
    recommendations = [indexed[item["id"]] | {"reason": item["reason"]} for item in plan["recommendations"]]
    tips = list(dict.fromkeys(tools.weather["assessment"]["tips"] + tools.attractions["warnings"] + plan["tips"]))
    if tools.weather["data_kind"] in {"historical_reanalysis", "recent_past_model"}:
        tips.insert(0, "这是历史日期的天气回顾与条件适配示例，不是当前出行预报。")
    return {"mode": mode, "mode_label": "DeepSeek AI 规划" if mode == "deepseek" else "实时工具查询与规则推荐（未调用LLM）",
            "model": model if mode == "deepseek" else None, "generated_at": now_iso(),
            "title": plan["title"], "summary": plan["summary"], "weather": tools.weather,
            "recommendations": recommendations, "excluded": tools.attractions["excluded"],
            "tips": tips, "trace": tools.trace,
            "attraction_source_mode": tools.attractions["source_mode"],
            "note": "推荐是备选景点清单，不是已验证交通和开放状态的一日线路。"}


def plan_trip(city="北京", date_text="今天", preference="均衡", question="", mode="tools", api_key=None,
              model=None, fetch=fetch_json, transport=None, today=None):
    if mode not in {"tools", "deepseek"} or preference not in PREFERENCES:
        raise ToolError("模式或偏好无效")
    if not isinstance(city, str) or not 2 <= len(city.strip()) <= 100:
        raise ToolError("请输入有效城市名")
    if not isinstance(question, str) or len(question) > 2000:
        raise ToolError("补充需求不能超过2000字")
    current = today or today_china()
    target = parse_date(date_text, current)
    if (target - current).days > 15 or target.year < 1940:
        raise ToolError("日期超出范围：未来最多16天，历史从1940年起")
    tools = TripTools(city.strip(), target, preference, fetch, current)
    model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    if not isinstance(model, str) or not 1 <= len(model) <= 100:
        raise ToolError("模型名称无效")
    if mode == "deepseek":
        plan = run_deepseek(tools, question, transport=transport or DeepSeekClient(api_key, fetch), model=model)
    else:
        plan = direct_tools_plan(tools)
    return build_result(tools, plan, mode, model)


def to_markdown(result):
    weather = result["weather"]
    lines = ["# " + result["title"], "", "- 模式：" + result["mode_label"],
             "- 城市：" + weather["city"]["name"], "- 日期：" + weather["date"],
             "- 数据类型：" + weather["data_label"], "- 查询时间：" + weather["retrieved_at"],
             "", "## 天气", "", "%s；%s–%s°C；降水%s毫米；最大风速%s公里/小时。" % (
                 weather["description"], weather["temp_min_c"], weather["temp_max_c"], weather["precipitation_mm"], weather["wind_max_kmh"]),
             "", result["summary"], "", "## 景点备选", ""]
    for place in result["recommendations"]:
        lines.extend(["### " + place["name"], "", place["reason"], "", place["caution"], "", "[来源与出行前核实](%s)" % place["source_url"], ""])
    if not result["recommendations"]:
        lines.append("本次没有推荐具体景点；请查看天气判断和提示。")
    lines.extend(["", "## 提示", ""] + ["- " + tip for tip in result["tips"]])
    lines.extend(["", "[天气接口原始数据](%s)" % weather["source_url"], "", result["note"]])
    return "\n".join(lines)
