# 本地 H3 制作流程

## 导演稿

JSON 为镜头数组，CSV 一行一镜，数组字段可用 JSON 或分号分隔。保留原文，导入原子化，已有镜头不覆盖。

| 字段 | 用途 |
|---|---|
| episode_id / episode、shot_id / shot | 分集、镜头 ID |
| prompt / positive | 场景主体描述，构成 effective_prompt |
| action_prompt / motion_prompt | 动作，兼容旧字段名 |
| camera_prompt、continuity_prompt、style_prompt | 运镜、连续性、风格 |
| negative_prompt / negative | 编入 Avoid 意图；H3 原始权重无独立负面 CFG 分支 |
| dialogue、speaker、sound_intent | 原样对白、人物标识、声音意图；原生对白不要求注册 TTS 声线 |
| reference_images、style_reference_images | 语义参考图与风格图，合计不超过 9 张 |
| reference_videos | 不超过 3 段参考视频 |
| reference_audio、sound_reference | 声音参考，合计不超过 3 段 |
| duration / target_duration | 4–15 整数秒；模板默认 15；不自动缩短长镜头 |
| seed | 本地采样种子，重试默认加 1；不能保证跨设备/版本逐位相同 |
| audio_policy | 可选的逐镜头导出策略：native / external_tts / mix |

视频和音频参考各段为 2–15 秒、各模态总时长 ≤15 秒；混合共 ≤12 文件。图像最小边 256，最大边 5760，宽高比 0.4–2.5。项目当前入口接受项目内文件：图像 JPG/PNG/WEBP/HEIC/HEIF，视频 H264/HEVC 的 MP4/MOV、23.976–60 FPS，独立音频 WAV/MP3。HEIC/HEIF 还需要本机 Pillow 解码支持。大小上限图像 30 MiB、视频 50 MiB、音频 15 MiB，是本项目素材接入约束；不会自动压缩、裁掉或跨作品读取素材。

```bash
./studio create film
./studio import film examples/h3-local.json
./studio shots film --episode 01
```

提示词按 Scene、Motion、Camera、Continuity、Style、Dialogue、Sound、Avoid 合成，不调用托管 Context-IR。应清楚描述每张参考图/视频/音频控制什么，使用上游的 `<Picture 1>`、`<Video 1>`、`<Audio 1>` 标记。视频带音轨时也作为音频参考参与建模；标记编号按上游处理顺序，复杂引用应检查 `h3-request.json` 与上游日志。

## 绑定素材

```bash
./studio asset film hero reference --file /path/to/hero.png
# 使用返回的真实 path，以下 xxxxxxxx 是示意
./studio bind film 01 001 assets/hero-xxxxxxxx.png --field reference_images
./studio asset film motion reference --file /path/to/motion.mp4
./studio bind film 01 001 assets/motion-xxxxxxxx.mp4 --field reference_videos
./studio asset film voice reference --file /path/to/voice.wav
./studio bind film 01 001 assets/voice-xxxxxxxx.wav --field reference_audio
```

`--field` 同样支持 style_reference_images / sound_reference；不传路径会清空对应列表。有排队或运行任务时不能改绑，先退回尚未执行的输入任务。参考素材变更会清除当前选中视频，但历史文件和版本仍保留。文件名相同、内容变更也会让输入审核失效。

## 提交与审核

```bash
./studio submit film 01 --shot 001 --kind video
# 可覆盖成片参数，不能用步骤/帧率捷径
./studio submit film 01 --shot 002 --kind video --params '{"steps":80,"seed":321,"duration":15}'
./studio queue film --episode 01
./studio task VIDEO_TASK_ID
./studio review-inputs VIDEO_TASK_ID approve --reason '参考、对白、运镜及参数已核对'
./studio log VIDEO_TASK_ID
```

也可 `produce film 01`，只批量提交 H3 视频。输入审核覆盖整个任务参数快照及素材 SHA256，worker 开始前再次核验；无参考图的纯文本任务同样要审。审完输入不代表视频质量通过。生成结果进入 review 状态，听审原生声音并检查画面后再 `review TASK approve`。审核台展示音轨状态，候选视频播放器支持带声音播放。

如需要把已审核 Qwen 候选加入参考集合，可显式提交 `--params '{"use_selected_image":true}'`，但它是 Ref2VA 的语义参考，不承诺逐像素保留第一帧。本项目不再自动把 selected_image 当作唯一视频入口。

H3 任务使用整个分配的 2 张卡（TP2），GPU lane 为 -1；Qwen / TTS 使用普通单卡 lane，属于分开的可选计算服务。重启保留中断版本并标记失败，需显式 retry；不自动续接可能已损坏的本地采样。OOM 暂停 lane，修复资源后 `./studio resume --gpu -1`。重试完整保留原视频采样质量，创建新任务并重新审核输入。

```bash
./studio review TASK reject --reason '人物动作和对白不同步'
./studio retry TASK --params '{"camera_prompt":"one continuous dolly shot"}'
./studio review-inputs NEW_TASK_ID approve
```

生成目录保留 task.json、h3-request.json、h3-inference.log、h3-performance.json、media-validation.json、metrics.json 和原生 video.mp4。metrics 含本地 backend、模式、尺寸、FPS、时长、音频、步数、seed、耗时、请求 ID、Slurm job ID 和并行拓扑。父进程 CUDA 峰值不能代表 SGLang 子进程；各 rank 的分配日志及 NVML 全卡峰值应分开解读。

## 原生音频、外部 TTS 与导出

```yaml
export:
  audio_policy: native
  allow_silent: false
  video_preset: slow
  video_crf: 16
  audio_bitrate: 320k
```

native 默认：保留 H3 音轨，忽略已选 TTS。external_tts：要求显式生成并审核的 selected_tts，替换原声音。mix：保留原音轨叠加已审核 TTS，声音大小需要制作人员听审；不会自动消除原对白。改配音后须重新检查口型。单独 TTS 需要在项目 voices 注册声线。

```bash
./studio export film --episode 01
# --draft 仅允许未通过输出进入有标注的技术审片，输入审核仍必须有效
./studio export film --episode 01 --draft
```

原生 VAE 会对齐生成帧数，导出使用实际时长，误差上限 0.75 秒；明显短缺报错，不循环/补帧。editing.trims 可以指定入出点，审核通过后的剪辑范围不得悄悄改变。字幕默认按镜头对白覆盖该片段，不承诺词级同步；口型始终等待人工验收。

## 可选 Qwen 参考资产与 TTS

```bash
bash scripts/bootstrap.sh assets
.venv-control/bin/python scripts/download_models.py --group production
# 只在独立 GPU 分配内启动 Qwen 服务；不要与 H3 抢同一组卡
bash scripts/start.sh --only compute
# 外部 TTS 明确选择后才安装、下载
bash scripts/setup_tts.sh
.venv-control/bin/python scripts/download_models.py --group tts
```

Qwen 默认原生 BF16、50 步，无 Lightning LoRA；Edit 自身最多 3 张输入，不影响 H3 的 9 张语义参考能力。不再需要 FP8 派生转换。

## 旧项目迁移

```bash
./studio migrate-h3 old-film
```

保留 project.yaml 备份、剧本、资产和历史候选；替换 video、image、edit 的默认模型配方、画幅尺寸/FPS、审核策略和导出策略，停用旧的排队视频任务。已有 running 视频任务必须先结束，不强杀进程。旧 Wan 候选不能直接成为 H3 链路的最终审核版本。迁移后重新提交、审核输入和视频。分辨率/FPS 是兼容性变化，旧模板和采样参数会明确报错。

## 从本地完整制作包启动一集

```bash
.venv-control/bin/python -m scripts.import_production_package /path/to/package.zip \
  --project production-film --episode 1 --submit --approve-inputs
.venv-control/bin/python scripts/slurm_submit.py --profile a800 --gpus 2 \
  --project production-film --episode 01 --submit
```

`--approve-inputs` 是显式的输入审核授权，应在核查原导演稿和资产映射后使用；它不会通过视频输出审核。导入器识别 GB18030/UTF-8 ZIP 文件名，拒绝路径穿越、符号链接和已有项目覆盖，原包内容只存入本地 projects。逐镜映射中的背景、角色、物品、生物作为语义参考；UI、特效和状态层元数据原样保留给后期，不烘焙进模型生成的画面。菜单按钮与游戏实体按钮的歧义会记录在 production_adjustments 中。

指定 project / episode 的 Slurm 作业只处理该集，任务失败即停止；全部生成结束后自动释放 GPU，输出进入 review，仍需逐镜听审和画面审核。每次执行保留 production/episode-EP-slurm-JOB.json。未完成环境安装或权重下载时，计算作业明确报错，不切换模型。

环境安装不需要 Cargo：安装脚本使用上游 `SGLANG_BUILD_RUST_EXTS=none`，禁用与本地 H3 原生推理无关的可选 Rust 扩展，不改变权重、采样或精度。
