# Weather Trip: A DeepSeek Weather-Aware Attraction Agent

## Purpose

Weather Trip is a single-purpose agent that queries weather for a selected city and date, retrieves attraction context, and recommends places appropriate for those conditions. For drizzle or rain risk, it prioritizes indoor venues and discourages prolonged exposed outdoor activities. For high-risk conditions under the project's explicit rules, it recommends postponing nonessential sightseeing.

The application provides a Chinese browser interface, command-line access, source links, downloadable advice, and inspectable tool traces. It uses the official DeepSeek API, not another provider's model or a third-party proxy.

## Requirement 1: At least two distinct functional skills

### Weather query: `get_weather(city, date)`

The host resolves a city to coordinates and calls Open-Meteo for dated daily weather. Returned context includes weather codes, minimum and maximum temperature, precipitation, maximum wind speed, and precipitation probability when available. It checks date alignment and missing fields. Forecasts, recent historical model data, and historical reanalysis are explicitly distinguished.

### Attraction context: `search_attractions(city, preference)`

The host independently queries a parameterized SQLite database populated from a supplied JSON catalog. The catalog contains twelve attractions in six Chinese cities, with sources and indoor/outdoor classifications. Other cities attempt a bounded OpenStreetMap Overpass query. Missing map data or service failure results in an explicit limitation, not invented attractions.

Candidates are filtered against the weather assessment before model selection. Large outdoor palace courtyards are not classified as fully indoor simply because the venue is called a museum. Opening hours, prices, current closures, and booking availability are not represented as verified.

## Requirement 2: Standard context integration

The agent uses standard LLM function calling through the official endpoint:

```text
https://api.deepseek.com/chat/completions
```

Tools are declared using JSON Schema in the `tools` array. The model emits `tool_calls`; the host validates and executes the functions, then sends results as `role: tool` messages with the corresponding `tool_call_id`. The model continues until it produces a final structured recommendation or a limit is reached.

The default configurable model is `deepseek-flash`, as documented by DeepSeek at development time. Non-thinking mode is explicitly selected for this workflow. Final advice uses JSON output after both tools have succeeded. The normal endpoint is used without claiming Beta strict-schema support; arguments are validated locally.

The host binds all tool calls to the user's chosen city, date, and preference. It rejects recommendations whose IDs are absent from the weather-eligible tool results. Final textual explanations still require human review; ID validation alone cannot establish semantic truth for every sentence.

## Requirement 3: AI-written scaffolding

Codex generated the HTTP transport, tool declarations, orchestration loop, weather parsing, database query layer, local web server, browser interface, tests, and launch scripts in this conversation. The central design work concerns the system prompt and orchestration: query first, reason from evidence, classify weather exposure carefully, and avoid fabricating facts.

The editable system prompt is included as `system_prompt.txt`. The development record is included as `vibe_coding_log.md`. This report does not claim that the student independently wrote code or performed work outside this session.

## Architecture

```text
City + dated request + preference
              |
              v
DeepSeek official API with function schemas
              |
              v
Model tool call -> host validation -> weather API / SQLite or map query
              ^                                      |
              +--------- matched tool result --------+
              |
              v
Model JSON advice -> allowed-ID and weather checks
              |
              v
Browser results + Markdown export + JSON trace
```

## Deployment

The project runs locally using Python 3.10 or newer and requires no third-party Python packages. On Windows, `start.cmd` launches the application and opens its loopback address. It does not expose a public server. The package includes a GitHub Actions test workflow and a Git ignore file excluding secrets and runtime outputs.

The user requested GitHub upload after providing account information. Therefore, repository files are prepared, but no GitHub repository has been created or pushed during this stage.

## Validation and evidence

The automated suite covers date parsing, forecast boundaries, historical labels, invalid weather fields, drizzle/rain selection, sunny conditions, severe conditions, local SQL queries, map failure, argument restrictions, city/date binding, the official DeepSeek URL, tool-call result matching, rejection of unsupported attractions, and local HTTP behavior.

Real weather-tool execution was tested against Open-Meteo. A separate explicitly synthetic rainy-day scenario demonstrates indoor recommendations. The DeepSeek orchestration was tested using a simulated model transport and real host logic. The evidence directory distinguishes these categories.

**A real DeepSeek API key was not available, so authenticated live-model end-to-end verification remains outstanding.** The project includes the implementation and runnable entry points needed to complete it. Tool-only results and synthetic demos must not be presented as live-model acceptance evidence. The GitHub workflow has been prepared but has not run on GitHub.

## Final acceptance procedure

1. Configure an actual DeepSeek key in the local page or ignored `.env` file.
2. Select DeepSeek AI mode and request weather-aware recommendations for a supported city and an available forecast date.
3. Confirm successful weather and attraction tool calls in the exported trace.
4. Check that `mode` is `deepseek` and the weather is not `synthetic_fixture`.
5. Compare numeric weather fields against the source API and attraction IDs against the returned candidates.
6. Save the real output and trace as final classroom evidence; then publish the repository after GitHub authorization is available.

## Limitations

Weather selection rules are project heuristics, not official meteorological alerts. Daily forecasts do not describe every hour. Relative dates use the China time zone, while weather day boundaries use the resolved place's time zone. The local attraction catalog is limited; OpenStreetMap coverage and availability vary. Attraction lists are alternatives, not a route with validated travel times or real-time booking information. Data-service usage conditions remain those of the original providers.

## Official references

- [DeepSeek API quick start](https://api-docs.deepseek.com/quick_start/)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [Open-Meteo Forecast API](https://open-meteo.com/en/docs)
- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api)
- [OpenStreetMap copyright and data attribution](https://www.openstreetmap.org/copyright)
