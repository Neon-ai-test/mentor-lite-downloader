# MENTOR 粗筛下载小工具

这是从主系统拆出的独立可分发工具，只保留：

- B站登录授权
- 知识点录入和 Excel/CSV 导入
- 按知识点粗筛候选，默认目标 100 条
- 批量下载

不包含 AI 分析、ASR、音频分析、抽帧、OCR、AI 设置和完整评审流。

运行期依赖全部安装在本工具目录内，不使用主项目 `.venv`、主项目源码包、主项目前端依赖或系统 FFmpeg。视频合并固定使用本工具本地 Python 环境中的 `imageio-ffmpeg`。

## 首次运行

双击 `start.cmd`。脚本会在当前工具目录下创建本地环境：

```text
.runtime/
  venv/
  pip-cache/
  playwright/
```

依赖不会安装到主项目，也不会使用主项目的 `.venv`。B站登录和搜索使用 `.runtime/playwright/` 内的 Chromium；视频合并使用 `.runtime/venv/` 内的 `imageio-ffmpeg`，不会调用系统 `ffmpeg.exe`。安装完成后浏览器会打开：

```text
http://127.0.0.1:8765
```

## 下载目录

下载结果按知识点归档：

```text
downloads/
  二次函数顶点坐标/
    视频标题A.mp4
    视频标题B.mp4
```

同一个知识点下的视频不会再单独建子目录。

## 分发打包

运行：

```powershell
.\scripts\build-release.ps1
```

会生成 `release/mentor-lite-downloader.zip`。运行时生成的 `.runtime/`、`data/` 和 `downloads/` 不会进入分发包。
