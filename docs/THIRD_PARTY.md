# 第三方组件与模型来源

仓库分发原创控制代码、示例和 Noto 字体。ComfyUI、模型和运行包由安装脚本单独取得。精确模型版本、路径及摘要见 `model-manifests/models.json`；有 LFS 摘要的文件校验 SHA256，普通 Git 文件校验 Git blob SHA1。

| 组件 | 官方来源 | 上游许可 / 说明 |
|---|---|---|
| ComfyUI | [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0；固定提交保留上游 LICENSE |
| 文生图 | [Qwen-Image-2512](https://huggingface.co/Qwen/Qwen-Image-2512)、[Comfy 打包](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI) | Apache-2.0；包含独立的文本编码器与 VAE |
| 图像编辑 | [Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)、[Comfy 打包](https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI) | Apache-2.0 |
| 图像加速 | [Qwen Image Lightning](https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning)、[Edit Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | Apache-2.0；LoRA 独立于基础权重 |
| 视频 | [Wan2.2-I2V-A14B](https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B)、[Comfy 打包](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | Apache-2.0；高噪声/低噪声专家分阶段运行 |
| 视频加速 | [Wan2.2 Distill LoRAs](https://huggingface.co/lightx2v/Wan2.2-Distill-Loras) | Apache-2.0；使用打包仓库中的 high/low 4 步 LoRA |
| 配音 | [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、[0.6B CustomVoice 权重](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice) | Apache-2.0 |
| 语音识别 | [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[0.6B 权重](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) | Apache-2.0；可选中文初筛 |
| 字体 | [Noto CJK](https://github.com/notofonts/noto-cjk) | SIL OFL 1.1；仓库内原始字体及 LICENSE 保留 |
| 视频编码 | [FFmpeg](https://ffmpeg.org/)、[imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | Python 包与实际二进制许可分别适用；使用包含 libx264 的构建 |

`dequantize_models.py` 仅将选定 FP8 存储与标量缩放转换为普通 BF16 张量。派生权重保留原量化误差，仍遵循原权重许可；`finalize_dequant.py` 调整旧 FP8 控制标记，不改变张量载荷。发布派生权重需另行保留来源与许可，本仓库不分发这些文件。

根目录 GPL-3.0-or-later 许可覆盖原创应用代码和示例。它不改变字体、外部代码、模型权重、声音预设或运行二进制的独立条款，也不对用户上传素材或生成内容授予额外权利。
