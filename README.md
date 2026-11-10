# 叙映工坊 · Narrative Studio

面向成片制作的本地 AI 影视工程：project / episode / shot、导演稿导入、资产绑定、SQLite 队列、输入审核、候选管理、视频审核和时间线导出。

**唯一主视频后端是本地 MiniMax H3 Base（SGLang）**。不接收费云 API，不需要 MiniMax 密钥，不保留 Wan2.2 fallback。Qwen-Image / Edit 只用于可选参考资产；Qwen3-TTS 只用于显式选择的外部配音。

默认使用官方原始 BF16/FP32 权重、精确注意力、50 步完整采样、24 FPS、15 秒目标时长、768 短边与 32 kHz 原生立体声音频。没有 Lightning、Turbo、低步数 LoRA、量化或失败后自动降级。50 步是上游完整质量配方；无限加步数不保证画质继续提高。上游 Base 本身包含 CFG 蒸馏，这与另外安装 few-step / timestep 蒸馏加速权重是不同的事。

## 已核查的上游边界

截至 2026-11-10，根据 [MiniMax 官方模型说明](https://huggingface.co/MiniMaxAI/MiniMax-H3) 和 [SGLang 原生 H3 文档](https://docs.sglang.io/cookbook/diffusion/MiniMax/MiniMax-H3)：

- 开放的 FL2VA 支持文本、首尾帧音视频生成；Ref2VA 支持图像 / 视频 / 音频混合参考并联合生成视频与音频。本项目自动为纯文本选择 FL2VA，为参考素材选择 Ref2VA。
- 官方有 ComfyUI、SGLang、vLLM、Diffusers 集成。本项目使用独立进程中的原生 SGLang，直接保存其带音轨 MP4，不经过逐帧 PNG 中转。
- **完整 Context-IR 和 H3-Regenerate-2K 尚未开放本地实现**。本项目用确定性的场景 / 动作 / 镜头 / 连续性 / 对白 / 声音提示组织替代托管 Context-IR；不宣称与官方线上系统等价。纯本地最终质量基线为官方 Base 的 768P，不把普通放大、插帧或强行运行未验证的 2K 参数称为官方 2K。
- 模型参考上限：9 张图、3 段视频、3 段音频，总计 12 个文件；视频、音频参考各合计不超过 15 秒。生成帧数由 H3 原生时域 VAE 对齐，实际时长可能与整数目标略有偏差，记录实际时长，不补静帧。

## 安装

Linux / NVIDIA CUDA。控制层可继续使用 Python 3.10+；H3 使用隔离的 Python 3.12 环境和固定的 SGLang 源码提交（`configs/sglang-commit.txt`），不覆盖原有 Comfy / TTS 环境。

```bash
bash scripts/install_control.sh
# 需要 uv；会自动取得 Python 3.12，并安装固定源码版 SGLang diffusion 依赖
bash scripts/bootstrap.sh

# 默认下载组就是 H3；固定提交、大小和 SHA256 / Git blob 校验
.venv-control/bin/python scripts/download_models.py --group h3 --list
.venv-control/bin/python scripts/download_models.py --group h3 --jobs 4
```

两套原始权重约 **268 GiB**，加环境、下载缓存和成片，建议预留至少 400–500 GiB；可选 Qwen BF16 参考资产另需约 92 GiB。下载过程需要网络；推理显式设置 Hugging Face 离线模式。下载器可断点恢复，完成校验后才写 `runtime/h3-models.status`。H3 环境安装完成后写 `runtime/h3-runtime.status`。

默认计算配方是 **2 × A800 80GB，TP=2，Ulysses=1**，整个分配交给一个 H3 worker；不再每张卡各自加载一套视频模型。主机内存、加载峰值和 15 秒多参考任务的 GPU 峰值必须在目标集群实测，不能把两卡显存简单相加当作保证。当前账号每作业只有 64 GiB 主机内存，这是实际容量风险；如 OOM，应申请更大内存/更多 GPU 后显式调整配置，不会自动改成量化或低画质模式。推理时长尚无本集群 A800 实测，不能承诺每镜头几分钟。

Slurm 运行见 [docs/SLURM.md](docs/SLURM.md)：

```bash
# 只打印资源计划
.venv-control/bin/python scripts/slurm_submit.py --profile a800 --gpus 2
# 确认安装和权重完成后提交
.venv-control/bin/python scripts/slurm_submit.py --profile a800 --gpus 2 --submit
# 登录节点只运行审核台；不在登录节点推理
bash scripts/start.sh --only review
```

非 Slurm 专用双卡主机可运行 `bash scripts/start.sh --only h3 review`。服务只监听回环地址，审核台为 `http://127.0.0.1:7868`。远程访问使用 SSH 隧道，不直接暴露公网。

## 最小成片流程

```bash
./studio create h3-film --name 'H3 本地成片'
./studio validate h3-film examples/h3-local.json
./studio import h3-film examples/h3-local.json
./studio produce h3-film 01
./studio queue h3-film --episode 01
# 检查候选 params 内的提示词、素材和质量配置，然后审核输入
./studio task VIDEO_TASK_ID
./studio review-inputs VIDEO_TASK_ID approve
# Slurm 分配就绪后，worker 自动执行；检查 MP4、音轨与运行日志
./studio review VIDEO_TASK_ID approve
./studio export h3-film --episode 01
```

网页也提供输入审核和视频候选审核。`produce` 现在只提交 H3 视频，不隐式提交首帧和独立 TTS；纯文本镜头无需首帧依赖。多模态绑定、导演稿字段、重试和外部配音见 [使用说明](docs/USAGE.md)。

## 导出与审核

默认 `export.audio_policy: native`，即使存在 `selected_tts` 也优先保留 H3 原生音轨。仅显式改为 `external_tts` 才替换，`mix` 才混音。缺失音轨默认报错，禁止无提示静音补位。无裁剪、无需格式统一时复制素材；拼接保留音轨包，字幕版本需要重编码视频时也保留原生音频。必要的视频转码默认 slow / CRF 16。

时间线 JSON / CSV 与 validation 记录原生音频存在性、实际音源、策略、审核指纹、实际时长和口型状态。联合音视频生成不等于口型已人工验收；`lip_sync` 默认为 `unverified`。

## 验证与兼容性

```bash
.venv-control/bin/python -m pytest -q
```

测试覆盖多模态参数、素材内容变更导致输入审核失效、双卡调度、保留质量的重试、真实 MP4 原生音轨包保留、外部 TTS 显式替换和缺轨拒绝。测试中的 GPU 执行边界使用替身；通过不表示真实 A800 已完成推理或画质验收。

旧项目应先备份，运行 `./studio migrate-h3 PROJECT`，再重新提交/审核视频。旧候选保留用于审计，但旧 Wan 参数和 Comfy 视频任务不能在新主链路执行。已提交 Slurm 作业使用脚本的提交时副本，改磁盘文件不会修改已排队脚本；旧部署作业须重新提交。不会自动取消用户原有作业。

原创代码 GPL-3.0-or-later；模型采用独立上游许可，H3 是 MiniMax H3 Community License，并非 Apache-2.0。见 [第三方组件](docs/THIRD_PARTY.md)。
