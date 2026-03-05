# demo - 视频抽帧网页工具

一个简单可用的前后端一体化网页：上传视频后，后端用 `ffmpeg` 抽取序列帧，页面支持预览并可一键下载 ZIP。

## 技术架构

- **后端**：Python 标准库 `http.server`（无第三方依赖）
- **抽帧引擎**：ffmpeg（通过 Python `subprocess` 调用）
- **前端**：原生 HTML/CSS

## 使用方式

1. 确保系统已安装 `ffmpeg`：
   ```bash
   ffmpeg -version
   ```
2. 启动服务：
   ```bash
   python app.py
   ```
3. 浏览器打开：`http://localhost:5000`

## 功能

- 上传常见视频格式（`mp4/mov/avi/mkv/webm/m4v`）
- 可设置抽帧频率（fps）
- 抽帧后展示预览图（最多 12 张）
- 打包下载全部帧图 ZIP
