"use strict";
let config, latestResult, timer;
const $ = (id) => document.getElementById(id);
const environmentNames = { indoor: "室内为主", outdoor: "户外为主", mixed: "室内外结合" };
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function sourceLink(text, url) {
  const a = element("a", text);
  try { const checked = new URL(url); if (checked.protocol !== "https:") return element("span", text); a.href = checked.href; }
  catch { return element("span", text); }
  a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
}
function busy(active) {
  $("submit").disabled = active; $("demo").disabled = active;
  $("loading").hidden = !active; $("results").setAttribute("aria-busy", String(active));
  clearInterval(timer);
  if (active) { let seconds = 0; $("elapsed").textContent = "已等待 0 秒"; timer = setInterval(() => { $("elapsed").textContent = `已等待 ${++seconds} 秒`; }, 1000); }
}
function render(result) {
  latestResult = result;
  const w = result.weather;
  $("result").hidden = false; $("empty").hidden = true; $("error").hidden = true;
  $("mode-badge").textContent = result.mode_label;
  $("demo-notice").hidden = result.mode !== "demo";
  $("location-label").textContent = `${w.city.name} · ${w.city.country || ""} / ${w.date}`;
  $("result-title").textContent = result.title;
  $("weather-kind").textContent = `${w.data_label} · ${w.source_name} · ${w.retrieved_at}`;
  $("weather-symbol").textContent = w.weather_code >= 95 ? "ϟ" : w.weather_code >= 51 ? "☂" : w.weather_code <= 1 ? "☀" : "☁";
  $("temperature").textContent = `${w.temp_min_c}–${w.temp_max_c}°`;
  $("description").textContent = w.description;
  $("rain").textContent = `${w.precipitation_mm} mm`;
  $("probability").textContent = w.rain_probability_pct == null ? "历史数据无降水概率" : `最高降水概率 ${w.rain_probability_pct}%`;
  $("wind").textContent = w.wind_max_kmh;
  $("assessment-label").textContent = w.assessment.label;
  $("summary").textContent = result.summary;
  $("assessment").classList.toggle("risky", w.assessment.level === "postpone");
  $("recommendation-count").textContent = `${result.recommendations.length} 处备选 · ${result.attraction_source_mode}`;
  $("places").replaceChildren();
  result.recommendations.forEach((place, i) => {
    const card = element("article", undefined, "place"), top = element("div", undefined, "place-top"), title = element("div");
    top.append(element("span", String(i + 1).padStart(2, "0"), "place-index"));
    title.append(element("h4", place.name));
    const tags = element("div", undefined, "place-tags");
    tags.append(element("span", environmentNames[place.environment] || "类别待核实"), element("span", place.category));
    title.append(tags); top.append(title); card.append(top, element("p", place.reason, "place-reason"), element("p", place.caution, "place-caution"), sourceLink("查看来源与出行前核实 ↗", place.source_url));
    $("places").append(card);
  });
  if (!result.recommendations.length) $("places").append(element("p", w.assessment.level === "postpone" ? "当前条件下建议推迟非必要游玩，因此不安排具体景点。" : "没有找到可核实且符合天气条件的景点。本次不使用模型记忆补造推荐。", "no-places"));
  $("tips-list").replaceChildren(...result.tips.map(tip => element("li", tip)));
  $("excluded-section").hidden = !result.excluded.length;
  $("excluded").replaceChildren(...result.excluded.map(place => element("p", `${place.name}：${place.exclusion_reason}。${place.caution}`)));
  $("source-links").replaceChildren(sourceLink("Open-Meteo 天气接口原始结果 ↗", w.source_url));
  $("source-links").append(element("p", w.assessment.rule_note));
  $("trace").textContent = JSON.stringify(result.trace, null, 2);
  $("result-note").textContent = result.note;
  if (innerWidth < 701) $("results").scrollIntoView({ behavior: "smooth", block: "start" });
}
async function request(demo = false) {
  if (!config) return;
  $("error").hidden = true; $("result").hidden = true; $("empty").hidden = true; busy(true);
  try {
    const body = { city: $("city").value.trim(), date: $("date").value, preference: $("preference").value,
      question: $("question").value.trim(), mode: $("mode").value, model: $("model").value.trim(),
      api_key: $("mode").value === "deepseek" ? $("api-key").value.trim() : "" };
    const response = await fetch(demo ? "/api/demo" : "/api/plan", { method: "POST", headers: { "Content-Type": "application/json", "X-Session-Token": config.token }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "查询失败，请重试");
    render(data.result);
  } catch (error) { $("error").textContent = error.message || "连接本地服务失败，请检查启动窗口是否仍然打开"; $("error").hidden = false; }
  finally { busy(false); }
}
function download(kind) {
  if (!latestResult) return;
  const json = kind === "json", content = json ? JSON.stringify(latestResult, null, 2) : latestResult.markdown;
  const url = URL.createObjectURL(new Blob([content], { type: json ? "application/json;charset=utf-8" : "text/markdown;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url;
  link.download = `晴游-${latestResult.weather.city.name}-${latestResult.weather.date}${latestResult.mode === "demo" ? "-模拟示例" : ""}.${kind}`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("trip-form").addEventListener("submit", event => { event.preventDefault(); request(); });
$("demo").addEventListener("click", () => request(true));
$("mode").addEventListener("change", () => { $("key-fields").hidden = $("mode").value !== "deepseek"; });
$("download-md").addEventListener("click", () => download("md"));
$("download-json").addEventListener("click", () => download("json"));
(async () => {
  try {
    const response = await fetch("/api/config"); if (!response.ok) throw new Error("本地配置读取失败"); config = await response.json();
    $("date").value = config.today; $("model").value = config.model;
    $("cities").replaceChildren(...config.cities.map(city => { const option = element("option"); option.value = city; return option; }));
    if (config.has_api_key) { $("mode").value = "deepseek"; $("key-fields").hidden = false; $("key-status").textContent = "本机已配置密钥，可留空"; }
  } catch (error) { $("error").textContent = error.message; $("error").hidden = false; $("submit").disabled = true; $("demo").disabled = true; }
})();
