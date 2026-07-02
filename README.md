# MENTOR 粗筛下载小工具

这是从主系统拆出的独立可分发工具，只保留：

- B站登录授权
- 独立知识点管理页：支持筛选、录入、编辑、删除和 Excel/CSV 导入，导入时可预览表头并自定义字段映射
- 按知识点粗筛候选，默认目标 100 条；粗筛规则是主站规则的独立本地副本
- 独立任务管理页和候选库管理页
- 批量下载

不包含 AI 分析、ASR、音频分析、抽帧、OCR、AI 设置和完整评审流。

默认分发包是在线自举包：首次启动会自动下载 Python 运行时、Python 包依赖和 Playwright Chromium，并全部安装在工具目录内。也可以额外制作完整离线包，把 Python、wheels 和 Chromium 预先打进 zip。运行期依赖不会安装到主项目，不使用主项目 `.venv`、主项目源码包、主项目前端依赖或系统 FFmpeg。视频合并固定使用本工具本地 Python 环境中的 `imageio-ffmpeg`。

注意：GitHub 源码检出和默认 release zip 都不会提交 `.runtime/python/`、`.runtime/wheels/`、`.runtime/playwright/` 这类二进制运行时目录；它们会在 `start.cmd` 首次启动时自动下载和本地化安装。只有显式使用 `-BundleRuntime` 制作离线包时，才会把这些目录打进 zip。

## 首次运行

双击 `start.cmd`。脚本会在当前工具目录下创建本地环境：

```text
.runtime/
  python/
  wheels/
  venv/
  pip-cache/
  playwright/
```

启动脚本会优先使用 `.runtime/python/python.exe` 创建本地环境；如果 GitHub 源码包里没有这个目录，且电脑也没有 Python，脚本会自动下载 Python Core zip 并解压到 `.runtime/python/`，不会执行系统安装器。依赖不会安装到主项目，也不会使用主项目的 `.venv`。B站登录和搜索使用 `.runtime/playwright/` 内的 Chromium；视频合并使用 `.runtime/venv/` 内的 `imageio-ffmpeg`，不会调用系统 `ffmpeg.exe`。安装完成后浏览器会打开：

源码包首次启动会按需联网下载：

- Python Core zip：默认优先下载 `3.12.10`，失败后自动尝试 `3.11.9`
- Python 包依赖：从 Python package index 下载到本地 `.runtime/venv/`
- Playwright Chromium：下载到 `.runtime/playwright/`

如果官方源下载失败或超时，脚本会自动尝试国内镜像：

- Python Core zip：npmmirror、华为云、清华镜像
- Python 包依赖：清华、阿里云、腾讯云、华为云、中科大 PyPI 镜像
- Playwright Chromium：npmmirror Playwright 镜像

可选环境变量：

- `MENTOR_LITE_PYTHON_VERSIONS`：自定义 Python 下载版本顺序，默认 `3.12.10;3.11.9`
- `MENTOR_LITE_PYTHON_VERSION`：只指定一个 Python 下载版本，会覆盖默认版本顺序
- `MENTOR_LITE_PYTHON_RUNTIME_URLS`：自定义 Python Core zip 下载源，多个地址用分号分隔；地址里可用 `{version}` 作为版本占位符
- `MENTOR_LITE_PIP_INDEX_URLS`：自定义 pip 镜像，多个地址用分号分隔
- `MENTOR_LITE_PLAYWRIGHT_DOWNLOAD_HOSTS`：自定义 Playwright 浏览器下载源，多个地址用分号分隔
- `MENTOR_LITE_DOWNLOAD_TIMEOUT_SECONDS` / `MENTOR_LITE_PIP_TIMEOUT_SECONDS`：下载超时时间

```text
http://127.0.0.1:8765
```

如果首次启动失败，控制台会保留错误信息，完整启动日志也会写入 `.runtime/logs/bootstrap.log`。排查时优先查看这份日志，它会包含脚本路径、源码版本、Python 下载/解压、依赖安装和浏览器下载的完整过程。

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

默认生成在线自举包：

```powershell
.\scripts\build-release.ps1
```

会生成 `release/mentor-lite-downloader.zip`。用户首次双击 `start.cmd` 时，脚本会自动下载并安装所有运行依赖。

如果需要制作完整离线包，先在打包机上准备完整离线运行素材：

```powershell
.\scripts\prepare-offline-deps.ps1 -PythonRuntimeDir "C:\Users\you\AppData\Local\Programs\Python\Python312"
```

如果打包机的 Python 3.11+ 已经在 PATH 中，也可以不传 `-PythonRuntimeDir`。这一步会准备：

```text
.runtime/python/      # 内置 Python
.runtime/wheels/      # 离线 Python wheel 依赖
.runtime/playwright/  # 离线 Chromium
```

再运行：

```powershell
.\scripts\build-release.ps1 -BundleRuntime
```

离线包会要求 `.runtime/python/`、`.runtime/wheels/` 和 `.runtime/playwright/` 已准备好，并会全部复制进分发包。如果想直接在打包时指定 Python 来源，也可以显式指定：

```powershell
.\scripts\build-release.ps1 -BundleRuntime -PythonRuntimeDir "C:\Users\you\AppData\Local\Programs\Python\Python312"
```

只有在制作离线包但允许目标电脑联网补齐缺失 wheels 或 Chromium 时，才使用 `-AllowOnlineInstall`。只有在明确允许目标电脑自行安装 Python 时，才使用 `-NoPythonRuntime`。运行时生成的 `.runtime/venv/`、`data/` 和 `downloads/` 不会进入分发包。
