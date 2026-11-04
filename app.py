"""Local-only browser app and CLI. Uses only Python's standard library."""
import argparse
import json
import os
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agent import PREFERENCES, load_env, plan_trip, to_markdown
from demo import rainy_demo
from weather import ToolError, catalog, today_china

ROOT = Path(__file__).resolve().parent


def make_server(port=8765):
    token = secrets.token_urlsafe(32)
    slots = threading.BoundedSemaphore(2)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Never log request bodies, keys or personal preferences.

        def allowed_host(self):
            return self.headers.get("Host") in {"127.0.0.1:%s" % self.server.server_port, "localhost:%s" % self.server.server_port}

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed_host():
                return self.send(403, {"error": "主机地址无效"})
            path = urlparse(self.path).path
            if path == "/api/config":
                return self.send(200, {"token": token, "today": today_china().isoformat(), "has_api_key": bool(os.getenv("DEEPSEEK_API_KEY")),
                                       "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                                       "cities": [c["name"] for c in catalog()["cities"]], "preferences": PREFERENCES})
            if path == "/health":
                return self.send(200, {"ok": True, "service": "weather-trip-agent"})
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8"), "/favicon.svg": ("favicon.svg", "image/svg+xml")}
            if path in static:
                filename, content_type = static[path]
                return self.send(200, (ROOT / "static" / filename).read_bytes(), content_type)
            return self.send(404, {"error": "页面不存在"})

        def do_POST(self):
            if not self.allowed_host() or not secrets.compare_digest(self.headers.get("X-Session-Token", ""), token):
                return self.send(403, {"error": "请刷新本地页面后重试"})
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://127.0.0.1:%s" % self.server.server_port, "http://localhost:%s" % self.server.server_port}:
                return self.send(403, {"error": "请求来源无效"})
            if urlparse(self.path).path not in {"/api/plan", "/api/demo"}:
                return self.send(404, {"error": "接口不存在"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 16000:
                    return self.send(413, {"error": "请求过大"})
                data = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(data, dict):
                    raise ToolError("请求必须是JSON对象")
                if self.path == "/api/demo":
                    result = rainy_demo()
                else:
                    if not slots.acquire(blocking=False):
                        return self.send(429, {"error": "当前已有任务处理中，请稍后重试"})
                    try:
                        result = plan_trip(city=data.get("city", "北京"), date_text=data.get("date", "今天"),
                                           preference=data.get("preference", "均衡"), question=data.get("question", ""),
                                           mode=data.get("mode", "tools"), api_key=data.get("api_key") or None,
                                           model=data.get("model") or None)
                    finally:
                        slots.release()
                result["markdown"] = to_markdown(result)
                return self.send(200, {"ok": True, "result": result})
            except (ToolError, ValueError, TypeError, AttributeError) as exc:
                return self.send(400, {"ok": False, "error": str(exc)[:450]})
            except Exception:
                return self.send(500, {"ok": False, "error": "服务暂时无法完成请求，请稍后重试；没有使用模拟天气替代真实数据"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    load_env()
    parser = argparse.ArgumentParser(description="晴游：DeepSeek天气与景点助手")
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--city", default="北京")
    parser.add_argument("--date", default="今天")
    parser.add_argument("--preference", default="均衡", choices=PREFERENCES)
    parser.add_argument("--question", default="")
    parser.add_argument("--mode", default="tools", choices=["tools", "deepseek"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--demo", action="store_true", help="明确标记的模拟小雨情景")
    parser.add_argument("--out", type=Path, help="保存JSON及Markdown，适合课堂展示")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.cli or args.demo:
        try:
            result = rainy_demo() if args.demo else plan_trip(args.city, args.date, args.preference, args.question, args.mode, model=args.model)
            print(to_markdown(result))
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                (args.out / "result.md").write_text(to_markdown(result), encoding="utf-8")
            return 0
        except ToolError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    try:
        server = make_server(args.port)
    except OSError:
        print("端口已被使用，可添加 --port 8766 后重试。", file=sys.stderr)
        return 1
    address = "http://127.0.0.1:%s" % server.server_port
    print("晴游已启动：" + address + "\n保持此窗口打开，按 Ctrl+C 结束。", flush=True)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
