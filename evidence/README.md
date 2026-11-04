# Evidence labels

- test-results.txt: actual automated test output.
- live-weather-result.* / live-preview.jpg: actual online weather tool and local attraction query; NO LLM.
- rainy-demo.* / rainy-preview.jpg: explicitly synthetic drizzle weather and real local catalog filtering; NO LLM.
- simulated-protocol-proof.json: simulated model and weather, actual orchestration and SQLite queries; NOT a live DeepSeek run.
- browser-checks.json: UI checks performed during this session.
- package-check.txt: extracted-package start and health check.

Authenticated DeepSeek end-to-end verification and GitHub upload are pending.
