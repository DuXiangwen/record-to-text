# RecordToText · 录音转文本

一件为「长录音、中英印混说、只要原文」这一种活儿定制的 Windows 桌面工具。

*A Windows desktop tool for transcribing long, multilingual meeting recordings — built for one specific workflow, not for everyone.*

---

## 先读这段

**这是个人自用工具，不是通用转写软件。** 把它发出来是因为它的几个设计取舍可能对同类型的长录音场景有用，不是因为它要跟谁竞争。

明确不做的事：

- 不支持 macOS / Linux。`run.bat` 是 Windows 批处理，`CREATE_NO_WINDOW`、路径处理、`QtMultimedia` 编解码都按 Windows 写的。
- 不接多种 ASR 后端。当前只实现了**一个** OpenAI 兼容端点（`POST /v1/chat/completions` + `input_audio`）。要换别家得改 `app/asr.py`，接口细节全集中在这一个文件里。
- 不接 feature request。只收 bug。
- 没有单元测试、没有 CI、没有打包成 exe。要跑得自己装 Python 和依赖。
- 不做说话人分离、不做翻译、不做摘要、不做降噪。

**为什么不做通用化：** 这个工具好用的全部原因，是它在你按「开始」之前不需要问你任何问题。每加一个兼容层、一个开关、一个向导，就会多问一次。加到 Buzz 那个体量，设置项比功能还多，它就不再是现在这个东西了。

如果需要一个成熟的通用工具，去用这几个，都比这个项目靠谱得多：

| 项目 | 说明 |
|---|---|
| [Buzz](https://github.com/chidiwilliams/buzz) | 最成熟的 Whisper 桌面 GUI，多后端、说话人识别、CLI、插件 |
| [Vibe](https://github.com/thewh1teagle/vibe) | Tauri + Rust，Vulkan/CoreML 跨显卡加速 |
| [noScribe](https://github.com/kaixxx/noScribe) | 面向访谈和定性研究，带音频-文本对齐编辑器 |
| [WhisperX](https://github.com/m-bain/whisperX) | 长音频切分与对齐的工程标准答案（库，不是 GUI） |

---

## 它解决什么

一段两小时的会议录音（m4a），中英文和印度英语混着说，你只想要一份带时间戳的原文，不想听第二遍。

直接扔给 Whisper 会有三个问题：切片在词中间剪断、跑一半断了要从头再来、在长静音上产生幻觉。这个工具把这三件事都处理了，然后把结果落在录音旁边的文件夹里。

一次转换 = 一个「项目」：项目名 + 录音路径 + 参数 → 在录音同级目录建同名文件夹 → 全部产物落在里面。

---

## 三个不太常见的设计

### 1. 波形双游标选区 → 只转写选区

顶部是真实 RMS 波形（不是截图），`≥2 秒低于 -45 dBFS` 的段落用浅灰底纹铺底，一眼看出哪几块是空场。

拖两个游标框出一段，`开始转换` 就只处理这一段，时间戳仍然按**原始录音**计（`00:32:50` 而不是 `00:00:00`）。适合「只听关键半小时」的场景。

滚轮缩放 1×–64×，空格播放/暂停，倍速 0.5–2×，播到选区末尾自动停。

### 2. 静音吸附切片：句子完整优先于段长稳定

先 `ffmpeg silencedetect` 扫全片（`-35 dBFS`，最短 `0.4s`），把静音按长度分成「句间停顿（0.4–1.5s）」和「段落边界（≥1.5s）」，然后按四级优先级定切点：

| 优先级 | 条件 | 切在哪 |
|---|---|---|
| 1 | 目标点正好落在静音内 | 目标点本身 |
| 2 | 容差窗口内有静音 | 窗口内最长的静音，靠近目标点的一端再内收 0.2s |
| 3 | 窗口内没有，但往前 `Δ`–`2Δ` 有长静音 | 那个长静音靠近目标点的一端 |
| 4 | 都没有 | 硬切，后一段从 `T - 0.3s` 开始重叠 |

`Δ` = 段长 × 30%。静音吸附不重叠（切点在静音里，两边都没词），硬切才重叠 0.3 秒 —— 宁可重复半个词，不丢字。

**段长飘了只是请求次数变化，切断了是内容丢失。** 这是整个工具里最需要想清楚的地方。

### 3. 断点续传按 index，不按文件名、不按文本非空

两个看起来能用、实际都会坏掉的判据：

- **靠文件名匹配** → 文件名里带起始时间，重切一次就全对不上。
- **靠「文本非空」** → 静音段返回空字符串，会被当成失败无限重试。

真正的续传依据是 `segments/00N_*.json` 里的 `index` 和 `status`（`done` / `empty` / `failed`）。重启后按 index 建索引，只重跑 `failed` 和缺失的段。文件名只用来给人看。

「继续」中断项目时会**锁定原参数**，界面改动只对新项目生效 —— 否则段号对不上。

---

## 环境

| 项 | 要求 |
|---|---|
| 系统 | Windows 10/11 |
| Python | 3.13 / 3.14（实测 3.14.7） |
| GUI | PySide6 |
| 音频 | ffmpeg（通过 `imageio-ffmpeg` 自动获取，无需单独安装） |
| 其他 | `numpy`、`requests` |

```bash
pip install PySide6 numpy requests imageio-ffmpeg
```

> ⚠️ `run.bat` 和 `run_debug.bat` 里硬编码了 `C:\Python314\python.exe`，改成你自己的解释器路径。

---

## 跑起来

```bash
# 1. 生成配置
copy config.example.json config.json     # Windows
# cp config.example.json config.json     # Git Bash

# 2. 填 API Key（config.json 的 api.api_key，或在界面「设置」里填）
# 3. 启动
run.bat          # 无控制台
run_debug.bat    # 带控制台，排错用
```

也可以纯命令行跑：

```bash
python cli.py --help
```

### 关于 API 端点

默认指向 `https://token-plan-cn.xiaomimimo.com/v1`，`model=mimo-v2.5-asr`。这是作者当时用的服务，随时可能变。要换成别的 OpenAI 兼容端点，改 `config.json` 的 `api.base_url` / `api.model`，以及在 `app/asr.py` 里确认请求体格式（`input_audio` 那一套不是所有服务都认）。

**`config.json` 里的 key 是明文的**，因为这是本机单用户工具，没做加密。所以它进了 `.gitignore`。

---

## 目录结构

```
A10_1106_out_info/
├── run.bat / run_debug.bat     # 启动脚本
├── main.py                     # 入口
├── cli.py                      # 命令行
├── config.example.json         # 配置模板（复制成 config.json 用）
├── app/
│   ├── config.py               # 配置读写（原子写）
│   ├── store.py                # projects.json 读写
│   ├── models.py               # Project / Segment 数据类
│   ├── audio.py                # ffmpeg：探测 / 裁剪 / 静音检测 / 切片
│   ├── waveform.py             # PCM 流式解码 + RMS 分块 + 缓存
│   ├── asr.py                  # ASR 客户端 + 重试 + 限流
│   ├── pipeline.py             # QThread 任务编排
│   ├── exporter.py             # md / txt / manifest
│   └── ui/                     # 主窗口、波形、卡片、设置、预览
└── PLAN.md                     # 完整设计文档（含全部取舍的理由）
```

### 单个项目的产物

```
{录音所在目录}/{项目名}/
├── {项目名}_全文.md            # 合并输出
├── {项目名}_全文.txt           # 合并输出（带时间戳行）
├── segments/
│   ├── 001_00-32-50.txt        # 该段原文
│   ├── 001_00-32-50.json       # 状态 / 耗时 / 重试次数（续传依据）
│   └── ...
├── audio/seg_000.mp3 ...       # 切片，16k/单声道/32kbps
├── project.json                # 参数与状态
├── manifest.json               # 每段起止 / 字数 / 耗时 / 状态
└── run.log
```

半成品不会假装是成品：中途取消或失败时，合并文件头会追加一行「本文件未完成：27 段中已完成 20 段，可右键卡片『继续』补跑」，全部跑完自动去掉。

---

## 已知限制

| # | 限制 | 影响 |
|---|---|---|
| 1 | 只测过一个 ASR 端点 | 换服务可能要改 `asr.py` 的请求体 |
| 2 | 静音阈值 `-35 dBFS` 不适用所有场景 | 空调噪声大的会议室会把说话误判成静音，可在设置里调，波形底纹能直观看出判得对不对 |
| 3 | 卡片堆到上百个时滚动会变慢 | 用的 QWidget 列表，没做虚拟化 |
| 4 | 没有单元测试 | 改动靠手动跑真录音回归 |
| 5 | 项目名和路径按 Windows 写 | 换系统要改路径处理 |

---

## 设计文档

`PLAN.md` 是完整的设计方案，包含每一个取舍的理由：为什么不做全片预转码、为什么波形用 RMS 而不是峰值、90 秒切片长度是怎么从 base64 10MB 上限倒推出来的、四种典型切片情况分别怎么处理、状态机怎么流转。

代码是这件事里最容易复现的部分。**判断不是。** 要改这个工具，先读 PLAN.md。

---

## License

暂未指定，默认保留所有权利（All rights reserved）。读、学、自己改着用没问题，但不要直接分发或商用。

作者保留后续选定开源协议的权利。
