# 叙映工坊 · Narrative Studio

面向本地 GPU 的 AI 影视制作与人工审核平台。把作品配置、导演稿、参考资产、生成任务、候选审核和视频导出串成可追踪的流程，适用于短剧、动画、广告和故事短片。

主力视频配置为 **Wan2.2 I2V A14B 双专家**，每张 GPU 独立执行任务。通用引擎不预设作品人物、题材、语言或画幅。

## 已实现

- 多作品隔离：每个作品拥有自己的 YAML、剧本、资产、任务和输出。
- 导入 JSON / CSV 导演稿，支持字段映射、时长验证与原文留存。
- Qwen-Image 首帧、Qwen-Image-Edit 参考图编辑、Wan2.2 图生视频和 Qwen3-TTS 配音。
- SQLite 持久队列、显存门槛、单卡锁、失败记录与显式重试。
- 中文网页审核台：选择候选、通过或拒绝、填写原因、继续生产。
- 首帧审核后才执行视频；首帧变化后，旧视频不能通过审核。
- 导出 H.264/AAC 视频、中文字幕、镜头表和时间线；草稿带技术预览标注。

此版本是可继续开发的工程原型。原部署曾在双 H200 NVL 上完成约 34 秒技术样片与第二题材验证；公开仓库不附带该服务器的素材、任务数据库或验收记录。当前未实现口型同步、姿态控制和角色 LoRA 训练，也未证明长篇影视质量或稳定产能。

## 环境与安装

已使用 Linux、Python 3.10、PyTorch 2.8.0 + CUDA 12.8。完整生成需要 NVIDIA GPU、兼容驱动、较大主机内存及约 250 GiB 以上可用磁盘；下载和 BF16 派生文件会同时占用空间，转换过程也会占用大量内存。H200 是原部署验证环境，其他显卡的速度、显存需求和兼容性需自行验证。

所有命令在仓库根目录执行。`PYTHON_BIN` 可指定 Python 3.10 可执行文件。安装使用项目虚拟环境，不修改驱动或系统 Python。

```bash
git clone https://github.com/SDUM-Liu/narrative-studio.git
cd narrative-studio
export PYTHON_BIN=python3.10

# CPU 控制层：不下载模型，也能使用 CLI、导入与审核界面
bash scripts/install_control.sh
bash scripts/start.sh --only review
# 浏览器：http://127.0.0.1:7868
```

完整 GPU 生产安装：

```bash
# 安装固定提交的 ComfyUI 与 GPU 运行环境
bash scripts/bootstrap.sh

# 先查看下载清单；所有文件固定版本并校验摘要
.venv-control/bin/python scripts/download_models.py --group production --list
.venv-control/bin/python scripts/download_models.py --group production
.venv-control/bin/python scripts/download_models.py --group tts

# 配音独立环境；保持同一个 Python 次版本
bash scripts/setup_tts.sh

# 原部署使用此转换规避 FP8 混合格式与加速 LoRA 的运行问题
# BF16 只是容器，不能恢复 FP8 已损失的精度
.venv/bin/python scripts/dequantize_models.py
.venv/bin/python scripts/finalize_dequant.py

# 创建明确标为草稿的竖屏短剧、横屏茶饮广告示例
.venv-control/bin/python -m scripts.seed_projects

bash scripts/start.sh                  # 默认 GPU 0 和 1
# 单卡：bash scripts/start.sh --gpus 0
bash scripts/health.sh
```

审核台与 ComfyUI 仅监听本机，端口分别为 `7868`、`8190`、`8191`。远程访问可在自己电脑运行 `ssh -N -L 7868:127.0.0.1:7868 user@server`，再打开本机浏览器。当前界面面向可信本地操作者，没有账号认证，不适合直接暴露到公网。

仅在安装步骤使用网络下载依赖和模型。默认生成流程调用本机服务，不接入收费生成 API。模型权重单独下载，上游来源和许可见 [第三方组件](docs/THIRD_PARTY.md)。

## 制作一个作品

```bash
./studio create my-film --name '我的短片'
# 修改 projects/my-film/project.yaml：画幅、人物、声线、模型和预算
./studio validate my-film examples/short-film.json
./studio import my-film examples/short-film.json
./studio produce my-film 01
./studio queue my-film --episode 01
```

示例不包含对白；使用配音前，需要在作品配置的 `voices` 中登记对应 `speaker`。导入字段示例见 [导演稿格式](docs/USAGE.md)。

在网页审核首帧后，排队的视频才可执行。逐项审核视频和配音，再导出：

```bash
./studio review TASK_ID approve
./studio review TASK_ID reject --reason '人物面部不一致'
./studio retry TASK_ID --params '{"seed":12345}'
./studio export my-film --episode 01
# 允许未通过的视频进入带标注的技术草稿；仍检查首帧与配音门禁
./studio export my-film --episode 01 --draft
```

不覆盖历史候选，不把失败或拒绝记录计作质量通过。视频不足以覆盖镜头或对白时，导出会报错，需调整时长或重新生成。更多命令见 [使用说明](docs/USAGE.md)。

## 目录与验证

```text
engine/               通用控制、调度、模型适配、审核界面和导出
configs/              通用作品模板、字段映射与固定运行依赖
model-manifests/      上游固定提交、文件大小与校验摘要
scripts/              安装、下载、模型转换与服务管理
examples/             可直接导入的简单导演稿
tests/                CPU 控制逻辑回归测试
shared-assets/fonts/  Noto CJK 字体及独立 OFL 许可
```

运行时创建的 `projects/`、`models/`、`runtime/`、`logs/`、`vendor/` 和虚拟环境均被 Git 忽略。初次安装前可先查看下载量；不要提交自己的素材或运行数据库。

```bash
python -m pip install -r requirements-control.txt
python -m pytest -q
```

这些测试验证导入、作品隔离、审核门禁、重试和导出前置校验，不代表 GPU 推理与影视质量验收。提供 [GitHub Actions 配置示例](docs/ci-example.yml)，复制到 `.github/workflows/tests.yml` 后可启用 CPU 测试；当前仓库未启用自动运行。

## 开源许可

原创代码和示例采用 **GPL-3.0-or-later**，详见 [LICENSE](LICENSE)。Noto 字体继续采用 SIL OFL 1.1。模型、ComfyUI 和其他依赖各自遵循上游许可；代码许可不替代素材、肖像、声线及输出内容的相关授权。第三方归属见 [NOTICE](NOTICE) 与 [组件清单](docs/THIRD_PARTY.md)。
