from __future__ import annotations

import argparse
import cgi
import html
import mimetypes
import shutil
import subprocess
import uuid
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
STATIC_DIR = BASE_DIR / "static"

ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}
MAX_UPLOAD_SIZE = 1024 * 1024 * 1024

for p in (UPLOAD_DIR, OUTPUT_DIR, STATIC_DIR):
    p.mkdir(exist_ok=True)


def ffmpeg_exists() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_frames(video_path: Path, out_dir: Path, fps: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps}",
        "-q:v",
        "2",
        str(out_dir / "frame_%06d.jpg"),
        "-y",
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def render_page(error: str = "", success: str = "", download_url: str = "", previews: list[str] | None = None) -> bytes:
    previews = previews or []
    preview_html = "".join(f'<img src="{html.escape(p)}" alt="frame" loading="lazy" />' for p in previews)
    html_text = f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"UTF-8\"/><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"/>
<title>视频抽帧工具</title><link rel=\"stylesheet\" href=\"/static/style.css\"/></head>
<body><main class=\"container\"><h1>视频抽帧工具</h1><p class=\"subtitle\">上传视频，导出序列帧并打包下载。</p>
<form method=\"post\" enctype=\"multipart/form-data\" class=\"card\">
<label>选择视频文件<input type=\"file\" name=\"video\" accept=\"video/*\" required /></label>
<label>抽帧频率（fps）<input type=\"number\" name=\"fps\" min=\"0.1\" max=\"60\" step=\"0.1\" value=\"1\" required /></label>
<button type=\"submit\">开始抽帧</button></form>
{f'<div class="msg error">{html.escape(error)}</div>' if error else ''}
{f'<section class="result"><div class="msg success">{html.escape(success)}</div><a class="download" href="{html.escape(download_url)}">下载全部图片（ZIP）</a><h2>预览（最多12张）</h2><div class="grid">{preview_html}</div></section>' if success else ''}
</main></body></html>"""
    return html_text.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send_bytes(self, data: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self._send_bytes(render_page())

        if path.startswith("/static/"):
            target = (STATIC_DIR / path.removeprefix("/static/")).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self._serve_file(target)

        if path.startswith("/outputs/"):
            rel = path.removeprefix("/outputs/")
            target = (OUTPUT_DIR / rel).resolve()
            if not str(target).startswith(str(OUTPUT_DIR.resolve())) or not target.exists():
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self._serve_file(target)

        if path == "/download":
            job_id = parse_qs(parsed.query).get("job", [""])[0]
            target = OUTPUT_DIR / f"{job_id}.zip"
            if not target.exists():
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self._serve_file(target, download_name=f"frames_{job_id}.zip")

        self.send_error(HTTPStatus.NOT_FOUND)

    def _serve_file(self, target: Path, download_name: str | None = None) -> None:
        data = target.read_bytes()
        content_type, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if self.path != "/":
            return self.send_error(HTTPStatus.NOT_FOUND)
        if not ffmpeg_exists():
            return self._send_bytes(render_page(error="服务器未安装 ffmpeg，无法抽帧。"))

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_UPLOAD_SIZE:
            return self._send_bytes(render_page(error="文件过大，最大 1GB。"), status=413)

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type", "")},
        )

        video_field = form["video"] if "video" in form else None
        fps_raw = form.getfirst("fps", "1")

        if video_field is None or not getattr(video_field, "filename", ""):
            return self._send_bytes(render_page(error="请先选择视频文件。"))

        filename = Path(video_field.filename).name
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            return self._send_bytes(render_page(error="视频格式不支持。"))

        try:
            fps = float(fps_raw)
            if fps <= 0 or fps > 60:
                raise ValueError
        except ValueError:
            return self._send_bytes(render_page(error="抽帧频率必须在 0~60 之间。"))

        job_id = uuid.uuid4().hex[:10]
        upload_path = UPLOAD_DIR / f"{job_id}_{filename}"
        output_dir = OUTPUT_DIR / job_id

        file_data = video_field.file.read()
        upload_path.write_bytes(file_data)

        try:
            extract_frames(upload_path, output_dir, fps)
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.decode("utf-8", errors="ignore")[-300:]
            return self._send_bytes(render_page(error=f"抽帧失败：{err}"))

        frames = sorted(output_dir.glob("frame_*.jpg"))
        if not frames:
            return self._send_bytes(render_page(error="没有抽取到帧，请检查视频。"))

        zip_path = OUTPUT_DIR / f"{job_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for frame in frames:
                zf.write(frame, frame.name)

        previews = [f"/outputs/{job_id}/{f.name}" for f in frames[:12]]
        return self._send_bytes(
            render_page(
                success=f"抽帧完成，共 {len(frames)} 张图片。",
                download_url=f"/download?job={job_id}",
                previews=previews,
            )
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="视频抽帧工具（本地 Web 服务）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", default=5000, type=int, help="监听端口，默认 5000")
    parser.add_argument("--open", action="store_true", dest="open_browser", help="启动后自动打开浏览器")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    url = f"http://localhost:{args.port}"

    if not ffmpeg_exists():
        print("[WARN] 当前环境未找到 ffmpeg，页面可访问但无法执行抽帧。")

    if args.open_browser:
        webbrowser.open(url)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Server running on {url}")
    server.serve_forever()
