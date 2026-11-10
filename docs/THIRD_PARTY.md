# 上游组件、能力与许可

代码 GPL-3.0-or-later 不替代模型/服务/素材许可。核查日期：2026-11-10。

| 组件 | 来源与用途 | 许可 / 边界 |
|---|---|---|
| 主视频模型 | [MiniMax H3 官方权重](https://huggingface.co/MiniMaxAI/MiniMax-H3)、[官方代码说明](https://github.com/MiniMax-AI/MiniMax-H3) | MiniMax H3 Community License Agreement；不是 Apache/MIT，商用前阅读权重中的 LICENSE |
| 本地 H3 推理 | [SGLang H3 原生实现](https://docs.sglang.io/cookbook/diffusion/MiniMax/MiniMax-H3)、[SGLang](https://github.com/sgl-project/sglang) | Apache-2.0；源码提交固定于 configs/sglang-commit.txt；生成全程本地 |
| 可选参考资产 | [Qwen-Image 打包](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI)、[Qwen-Image-Edit](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | 按上游 Qwen/打包仓库许可；下载原生 BF16，无加速 LoRA |
| 可选资产运行环境 | [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0；固定 configs/comfy-commit.txt，仅 image / edit 注册 |
| 可选独立配音 | [Qwen3-TTS](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice) | Apache-2.0；不自动覆盖 H3 原声音 |
| 可选识别检查 | [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) | 遵守对应仓库许可；识别一致不代表声音/表演/口型通过 |
| 视频处理 | PyAV、imageio-ffmpeg、FFmpeg | 遵守各自许可证及 FFmpeg 实际构建许可（含 x264 的构建通常涉及 GPL） |
| 字体 | Noto Sans CJK SC | SIL OFL 1.1，见 shared-assets/fonts/LICENSE |

完整线上 H3 包含托管 Context-IR、Base 与 H3-Regenerate-2K；后两者中的 2K 模块以及 Context-IR 不属于完整开放的本地系统。本项目仅使用开放 Base 与本地结构化提示组织；不运行收费 API、不需要密钥，不宣称线上线下质量等同。官方亦提供 ComfyUI 模板，但本项目的视频主流程使用 SGLang 的本地 Python 调用而非 Comfy 图。

原始 H3 checkpoint 的 CFG 蒸馏属于官方 Base 架构。本项目没有引入另外的 Turbo / timestep 蒸馏 LoRA、量化 checkpoint、稀疏近似注意力或跳步缓存。

Wan2.2 和 Lightning 路线已弃用：不注册视频入口，不在默认下载清单或新项目模板出现；旧运行文件可以留作审计，不是 fallback。`model-manifests/models.json` 记录每个下载文件的固定 revision、大小和 SHA256 / Git blob ID，包含 H3 LICENSE；不在仓库再分发权重。
