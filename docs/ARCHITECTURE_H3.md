# H3 与官方视频 API 架构

## 设计决策

1. 默认本地 provider 为 minimax-h3，调用隔离环境中的 SGLang；显式 API profile 注册 minimax-h3-api（标准版 / Max）和 seedance-api（2.0 / 2.5）。能力表 engine/video_models.py 驱动 CLI 与审核台，engine/workflows/video.py 根据 provider 分发校验。不注册 Comfy 视频，不自动切换或降级。
2. 纯文本选择 FL2VA；有参考素材选择 Ref2VA。两者都是同一官方 H3 Base 的任务分区，不是两条不同模型路线。每任务只加载一个分区，并把整个双 A800 分配交给 TP2，不同时驻留两套 H3 服务。
3. 本地可验证的上游质量配方是 768 短边、24 FPS、50 步、官方原始 BF16/FP32、精确 FA 注意力。关闭 torch.compile、TF32、BF16 低精度归约和 autocast，不引入近似缓存、量化或加速 LoRA。FP32 VAE 解码及 15 秒多参考配置会增加资源需求，等待实机容量验证。
4. 不用普通放大伪装官方 2K；完整 Context-IR 与 2K 重生成尚未开放。当前替代是本地场景/动作/镜头/连续性/对白/声音提示组织，不声称等价于托管 Context-IR。
5. 审核门禁从首帧转为任务参数快照、导演稿版本及参考素材 SHA256。审核输入、生成候选、审核输出是独立阶段。重试创建新候选，保留整套质量参数，重新审核输入。
6. 直接保存带原生音轨的 MP4。默认 native，外部 TTS 仅在 external_tts / mix 显式策略下参与导出。音轨缺失默认报错。无修改素材复制、音轨包保留、必要视频转码 slow / CRF16。
7. 保留原有 SQLite、工程层级、资产、候选历史、中文审核台、时间线和 CLI。迁移命令先备份配置、拒绝改动运行任务、停用旧排队生成任务，不删除历史素材。

## 受影响文件

| 范围 | 文件 |
|---|---|
| 默认配置 | configs/project.template.yaml、configs/sglang-commit.txt |
| 权重清单 | model-manifests/models.json（新增 H3 原始双分区，Qwen 改 BF16，删除 Wan/Lightning 下载项） |
| Provider | engine/providers/minimax_h3.py、engine/providers/__init__.py、engine/providers/comfy.py、engine/providers/comfy_metrics.py |
| 工作流 | engine/workflows/h3.py、engine/workflows/__init__.py |
| 数据、审核、CLI | engine/core.py、engine/cli.py、engine/review/app.py、engine/review/index.html |
| 调度、导出 | engine/scheduler/worker.py、engine/export/__init__.py |
| 依赖与安装 | requirements-control.txt、scripts/bootstrap.sh、scripts/setup_h3.sh、scripts/setup_reference_assets.sh、scripts/h3_infer.py |
| 下载与部署 | scripts/download_models.py、scripts/finish_deploy.sh、scripts/deployment_status.py、scripts/services.py、scripts/seed_projects.py |
| Slurm | scripts/slurm_submit.py、scripts/slurm_run.sh、scripts/slurm_prepare.sh（基于工作区已有集群适配继续修改） |
| 删除旧转换脚本 | scripts/dequantize_models.py、scripts/finalize_dequant.py |
| 文档与示例 | README.md、docs/USAGE.md、docs/THIRD_PARTY.md、docs/SLURM.md、本文、examples/h3-local.json |
| 回归测试 | tests/test_h3.py、tests/test_production.py、tests/test_slurm.py |

工作区原有的 Qwen TTS、SoX、其他 Slurm 脚本、SQLite journal 配置等改动保留，没有重置为仓库 HEAD。未把私有素材、项目数据库、下载权重或虚拟环境纳入源码。

## 验证方法与界限

- `.venv-control/bin/python -m pytest -q`：离线控制与真实 FFmpeg/PyAV 媒体回归。覆盖输入审核、文件字节变化、模型路线拒绝、参考视频/音频、参数保持、原生音频包保留、显式外部配音、混音、缺轨拒绝、裁剪重新审核、迁移和 Slurm 分配。
- 临时隔离 NARRATIVE_ROOT 的 CLI 冒烟已执行：create → import → produce → task → review-inputs → queue；不会消耗 GPU 或修改已有作品。
- Python 编译、Shell 语法、审核台 JavaScript 语法和 git diff --check；审核服务本机 health 接口返回 200。
- GPU 边界在离线测试中使用替身，不能据此宣称真实 H3 已推理成功。环境安装和 268 GiB 权重下载必须分别完成；尚需等待 A800 分配进行真实冷启动、15 秒多参考、音视频同步与画质验收。
- 当前账号只有 2 GPU / 64 GiB 主机内存，可能不足以承载所有最终质量负载。如容量不足，申请更多内存/卡并明确调整拓扑，不自动使用轻量模型或减少质量参数。

## 兼容性变化

produce 不再隐式生成首帧与 TTS；video 不接受 Wan 的 high/low、4n+1 帧数、LoRA、CFG 等字段；默认原生 FPS 固定 24。原生对白不要求注册 Qwen TTS 声线；独立 TTS 仍需要。旧视频和旧脚本不能绕过新的输入审核，旧项目使用 migrate-h3 并重新生成候选。

最小示例与完整命令见 README、docs/USAGE.md；A800 排队快照和 Slurm 运行见 docs/SLURM.md。模型许可与开放边界链接见 docs/THIRD_PARTY.md。

## 制作包与分集批处理补充

新增 scripts/import_production_package.py、scripts/run_episode.py、tests/test_production_package.py；扩展 worker 的 project / episode 过滤与有限批处理，扩展 Slurm 提交参数。原始制作包通过 .gitignore 的 resources/ 排除，不随公开源码同步。安装脚本显式禁用上游可选 Rust 扩展，处理无 Cargo 环境中的构建失败。


## 官方 API 扩展

- engine/providers/cloud_video.py 直连 MiniMax V2 / Ark 官方异步接口，原容器下载、媒体验证、usage/任务 ID 与实际编码记录；HTTP key 只来自进程环境。
- engine/workflows/cloud_video.py 独立校验各型号限制，并构造原生 content[]。文件引用仍经项目隔离与 SHA256 输入审核；Ark 视频使用显式远端素材位置。
- worker lane -2 不访问 CUDA，与本地 TP2 的 -1 分离；持久化提交意图和远端 ID，worker 重启后继续查询；模糊 POST 结果不自动重提。resume-api 与付费新生成 retry 区别明确。
- API profile 默认 preserve_source_format，检查整集源素材规格一致，再保留分辨率、HEVC/10bit、原生音轨和 MOV。字幕编码维持原像素格式；混合规格须显式选择统一导出策略。
- 模型参数切换先备份 project.yaml，只影响新任务；原有已审核任务的快照与指纹不变。所有模式都保留输入/输出两次审核。
- 本地权重安装与 Slurm 策略不因 API 扩展改变。API 仅需 requirements-control.txt，不安装云服务商 SDK。回归测试 mock HTTP 计费边界并实际运行 FFmpeg，不假称真实付费 API 已验证。

型号分辨率差异、示例、限制与官方来源见 [VIDEO_API.md](VIDEO_API.md)。
