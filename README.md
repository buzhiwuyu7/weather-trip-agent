# 晴游 Weather Trip

**查询天气，再推荐适合这种天气的景点。使用 DeepSeek 官网 API 和标准函数调用。**

输入城市、日期和偏好，助手获取真实天气，查询景点上下文，再说明为什么适合去这些地方。零星小雨时优先室内展馆，避免长时间户外运动；雷暴、强降水或大风达到项目规则时建议推迟游玩。

## 快速开始

安装 **Python 3.10 或更高版本**。无需安装第三方 Python 包。

Windows 解压后双击 `start.cmd`，浏览器会打开：

```text
http://127.0.0.1:8765
```

保持启动窗口打开；关闭窗口或按 Ctrl+C 即停止服务。端口被占用时可使用 `python app.py --port 8766`。

macOS / Linux，或已在终端中选好 Python 的 Windows 用户：

```bash
python app.py
```

## 配置 DeepSeek

两种方式任选一种：

1. 页面选择 **DeepSeek AI 规划**，在密码框中输入自己的 API Key。
2. 复制 `.env.example` 为 `.env`，填写 `DEEPSEEK_API_KEY`，重启程序。`.env` 已被 Git 忽略。

```dotenv
DEEPSEEK_API_KEY=在自己的电脑上填写真实密钥
DEEPSEEK_MODEL=deepseek-flash
```

模型接口固定为 **`https://api.deepseek.com/chat/completions`**，没有代理平台、OpenAI 模型请求或网页版抓取。默认 `deepseek-flash` 来自开发时核对的官网文档，也可填写账户可用且支持工具调用的 DeepSeek 模型名称。

密钥只用于向 DeepSeek 认证：不写入浏览器存储、输出文件或请求日志；通过页面输入的密钥只在当前页面和本次请求内使用。关闭/刷新页面后需重新输入。本地 `.env` 方式则由用户自己保存在电脑上。

**不要把 `.env`、真实密钥或个人日志上传到 GitHub。** 项目不要求 GitHub 密码来运行。

## 三种明确区分的运行方式

| 方式 | 天气来源 | 建议生成 | 是否调用模型 |
|---|---|---|---|
| DeepSeek AI 规划 | 真实 Open-Meteo 请求 | DeepSeek 发起工具调用、读取结果并解释 | 是，需要密钥 |
| 实时查询与规则推荐 | 真实 Open-Meteo 请求 | 透明的天气筛选与排序规则 | 否，无需密钥 |
| 北京小雨示例 | 明确标记的人工测试数据 | 同一套筛选规则 | 否，不代表真实天气 |

实时工具模式用于无需密钥先检查功能，不能当作完成了 LLM 调用。模拟小雨只用于课堂演示，网络失败时不会拿它代替真实天气。

## 实际实现的两项技能

### `get_weather(city, date)`

- 将城市定位为坐标；北京、上海、杭州、成都、西安、南京使用本地城市表，其他城市请求 Open-Meteo 地理编码。
- 查询指定日期的天气代码、最高/最低温、累计降水、最高降水概率与最大风速。
- 未来最多支持今天起16天；历史日期会标记为历史模型数据或历史再分析，不冒充实时观测。
- 接口空数据、超期日期、缺失字段均明确失败，不编造天气。

### `search_attractions(city, preference)`

- 独立查询 SQLite 景点上下文。数据由 `data/attractions.json` 导入内存数据库；SQL 查询使用绑定参数。
- 内置 **6 个城市、12 处景点**，附官方来源、室内外分类与核实提示。
- 其他城市尝试查询 OpenStreetMap Overpass，在城市中心15公里内获取地图标注景点。服务可用性、地图覆盖和类别准确性不能保证；无结果时明确说明，不让模型补造。
- 根据已查询的天气过滤和排序。雨天不把含大片露天院落的故宫当作室内避雨景点；高风险条件不推荐出门。

景点数据不是当日营业信息：没有实时查询票价、预约余量、临时闭馆或交通时间。出行前使用附带官方链接核实。

## 标准函数调用架构

```text
城市 + 日期 + 偏好 + 补充需求
            ↓
DeepSeek 官方 Chat Completions API + 工具 JSON Schema
            ↓
模型生成 tool_calls
            ↓
本地参数校验 → 天气 API / 景点 SQLite 或在线地图
            ↓
role=tool + 对应 tool_call_id 回传真实结果
            ↓
模型继续决定工具调用，或生成最终 JSON 建议
            ↓
主机验证推荐 ID / 天气匹配 → 页面与 Markdown / JSON 导出
```

这是作业允许的 **标准 LLM function calling** 路线，不需要额外安装 MCP 服务器。系统提示词位于 `system_prompt.txt`；模型不会直接执行 Python 或访问任意 URL。

主机绑定用户所选城市和日期，工具不能偷偷改城市、年份或日期。未取得两个成功工具结果时不会接受最终建议。模型只能推荐工具返回的 `eligible` 景点 ID。推荐理由仍属于模型生成文本，需要对照来源阅读；ID 校验不等于全文语义真实性校验。

默认上限8轮编排、12次工具调用。DeepSeek 单次请求超时90秒，天气/地图请求超时25秒。网页在查询期间显示等待状态。

## 示例与命令行

网页选择北京及日期，输入：

> 想了解历史，尽量少在雨里步行。请按真实天气推荐景点，并说明不建议哪些户外活动。

命令行真实天气查询，无需模型密钥：

```bash
python app.py --cli --city 北京 --date 今天 --mode tools --out runtime/live
```

命令行真实 DeepSeek 代理（先配置 `.env` 或环境变量）：

```bash
python app.py --cli --city 北京 --date 11.4 --mode deepseek --question "想了解历史，少走露天路段" --out runtime/deepseek
```

命令行支持 `2026-11-04`、`11.4`、`11月4日`、今天、明天、后天；未写年份时按查询时中国时区的当年解析，不自动跳到明年。页面日期选择器始终显示完整年份。全球城市也按中国时区解释相对日期，天气日界线则采用地点时区。

独立模拟小雨演示：

```bash
python app.py --demo --out runtime/rainy-demo
```

当日真实接口可能返回与示例不同的天气，这是正常行为。项目不会预设“北京11月4日一定下雨”。

## 测试与证据

```bash
python -m unittest discover -s tests -v
```

测试覆盖日期解析、预报范围、历史标记、空天气数据、雨天/晴天/极端条件、SQLite 查询、地图服务失败、参数和城市日期校验、DeepSeek 官方请求地址、工具回传与修正、虚构景点拒绝，以及本地 HTTP 页面和请求校验。

`evidence/` 包含测试结果、真实天气工具结果、模拟小雨结果和模拟模型协议测试记录；每类证据明确标注，不混用。

**本次交付未配置真实 DeepSeek 密钥，因此尚未执行有鉴权的 DeepSeek 端到端测试。** 官方接口与编排已实现并用模拟传输测试；配置密钥后可通过页面/命令行补做真实验收。真实天气联网请求已执行。GitHub Actions 工作流已准备，但尚未上传或在 GitHub 上运行。

## GitHub 上传准备

本目录就是仓库根目录：包含源码、网页、提示词、数据、测试、作业报告和 CI 工作流。压缩包不包含 `.env`、运行缓存或旧阅读助手。

用户提供目标 GitHub 账号/仓库并完成授权后，再上传此目录。现在没有创建或推送远程仓库。

## 作业文件

- `assignment_report.md`：英文作业报告。
- `作业要求中文翻译.md`：原要求完整翻译。
- `vibe_coding_log.md`：AI 编程过程与学生可讲解的设计重点。
- `docs/DEMO_GUIDE.md`：课堂演示与最终验收步骤。

## 数据与接口依据

- [DeepSeek 官方首次调用说明](https://api-docs.deepseek.com/quick_start/)
- [DeepSeek 官方 Tool Calls 指南](https://api-docs.deepseek.com/guides/tool_calls/)
- [DeepSeek 官方 JSON Output 指南](https://api-docs.deepseek.com/guides/json_mode/)
- [Open-Meteo 天气接口](https://open-meteo.com/en/docs) / [历史天气](https://open-meteo.com/en/docs/historical-weather-api) / [地理编码](https://open-meteo.com/en/docs/geocoding-api)
- [OpenStreetMap 数据署名与许可](https://www.openstreetmap.org/copyright)

天气判断是项目经验规则，不是官方预警系统；推荐列表不是已核实可完成的交通线路。数据服务条款和商业使用条件以各提供方最新说明为准。
