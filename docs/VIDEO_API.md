# 官方视频 API 与本地 H3

核查日期：2026-11-10。`./studio video-models` 和审核台“视频模型与输出规格”使用同一能力表。新建项目仍默认本地 H3；选 API 项目配置才启用收费调用。已有空岛第一集的本地双卡任务、输入审核和 Slurm 作业不会被切换。

## 型号与分辨率

| 项目 profile | 直连模型 / 服务 | 可选分辨率 | 默认分辨率 | 整数时长 | 图 / 视频 / 音频参考上限 |
|---|---|---|---|---|---|
| `h3-local` | MiniMax-H3 Base / 本地 SGLang | 768P | 768P | 4–15 秒 | 9 / 3 / 3，总计 12 |
| `h3-api` | MiniMax-H3 / MiniMax V2 | 768P、2K | 2K | 4–15 秒 | 9 / 3 / 3，总计 12 |
| `h3-max-api` | MiniMax-H3-Max / MiniMax V2 | 480P、768P | 768P | 5–15 秒 | 9 / 3 / 3，总计 12 |
| `seedance-2.0` | doubao-seedance-2-0-260128 / 火山方舟 | 480p、720p、1080p、4k | 4k | 4–15 秒 | 9 / 3 / 3，总计 12 |
| `seedance-2.5` | doubao-seedance-2-5-260628 / 火山方舟 | 480p、720p、1080p | 1080p | 4–30 秒 | 30 / 10 / 10，总计 50 |

依据：[MiniMax 官方 OpenAPI](https://platform.minimax.io/docs/api-reference/video/generation/api/v2-video-generation.json)、[火山方舟创建任务 API](https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh)、[Seedance 2.5 官方教程](https://docs.volcengine.com/docs/ark/seedance-2-5?lang=zh)。MiniMax 文档摘要可能没有列全 Max，实际以该 OpenAPI 的 model/resolution 约束为准。

MiniMax **直连 Max 不提供 1080P/2K**，不要混用 [fal Max 的独立规格](https://fal.ai/models/minimax/h3-max/text-to-video/api)。Max 是独立产品型号；不能从名字推断它是标准版的更高分辨率档位。需要官方 MiniMax 2K 时选标准版 `h3-api`。不支持的分辨率会在入队前报错，不会自动向下映射。

默认均为原生音频、24 FPS、型号支持的最高分辨率及最长默认时长；导演稿明确指定的镜头时长优先。Max 默认 `prompt_expansion_mode: quality`。API 不暴露本地采样步数、CFG、TP 拓扑等参数。本次核查的 MiniMax V2 和 Ark Seedance 2.x 不声明可控 seed，因此不发送该字段；metrics 分开记录 requested_seed、seed_applied=false 和服务返回 seed，不承诺固定种子复现。

## 安装与密钥

API 仅需要控制环境，无需安装 H3 权重、SGLang、CUDA 或提交 A800 作业。

```bash
bash scripts/install_control.sh
# 在启动 API worker 的服务器 shell 中设置；不要把真实密钥写入项目或 Git。
export MINIMAX_API_KEY='你的 MiniMax 按量付费 API 密钥'
export ARK_API_KEY='你的火山方舟 API 密钥'
bash scripts/start.sh --only api review
```

也可用 `services.py run --only api review` 在进程管理器里前台监督。`api` worker 使用 SQLite lane `-2`，只处理 API 视频；本地 H3 仍为 `-1`。它不访问 `nvidia-smi` / CUDA，不等待 Slurm。单 worker 串行执行，遵循模型并发额度，适合高分辨率限流场景。环境变量由启动进程继承，不会自动读取 `.env`。也支持 `MINIMAX_API_KEY_FILE` / `ARK_API_KEY_FILE` 指定权限 600 的纯文本密钥文件；不会自动扫描工作目录中的密钥。

MiniMax 直连 `https://api.minimax.io/v2/video_generation`，按 task_id 查询 `/v2/query/video_generation/{task_id}`；Seedance 直连 `https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks`。不使用第三方转售网关。账号需自行开通相应模型并有可用额度。

## 最小运行示例

```bash
./studio video-models
./studio create cloud-film --name '官方 API 制作' --video-model seedance-2.5
./studio import cloud-film examples/video-api.json
./studio produce cloud-film 01
./studio queue cloud-film --episode 01
# 填入上一步任务 ID；打印原生请求，内嵌素材字节已隐藏；不发起请求。
./studio video-request VIDEO_TASK_ID
./studio review-inputs VIDEO_TASK_ID approve --reason '模型、分辨率、参考素材与对白已确认'
# API worker 保存远端 ID、轮询并下载后，进入待审状态
./studio inspect VIDEO_TASK_ID
./studio review VIDEO_TASK_ID approve
./studio export cloud-film --episode 01
```

切换未来任务的配置（自动备份 YAML；已有候选和排队任务继续使用原快照）：

```bash
./studio video-model cloud-film h3-api --resolution 2K --ratio 9:16 --duration 15
./studio video-model cloud-film h3-max-api --resolution 768P --prompt-expansion-mode quality
./studio video-model cloud-film seedance-2.0 --resolution 4k
./studio video-model cloud-film seedance-2.5 --resolution 1080p --duration 30 --output-format mov
# Seedance 可显式生成无声视频；默认仍有声。无声视频需外部配音或显式允许静音才能导出。
./studio video-model cloud-film seedance-2.5 --audio none
./studio video-model cloud-film h3-local --ratio 16:9
```

逐任务可通过 `submit --params` 覆盖当前型号的 resolution、duration、aspect_ratio、output_format、参考位置等；切换 provider 时应使用完整的 `video-model` 配置，避免把本地 steps/TP 参数混入 API。原生有声开关逐任务修改时，`generate_audio`、`native_audio`、`audio_mode` 必须一致。

## 多模态参考与首尾帧

`asset` / `bind` 沿用本地工作流。每个素材仍须有项目内的真实文件，输入审核绑定其 SHA256；API 参数用 `reference_locations` 将这些本地路径对应到远端位置。修改映射同样会使输入审核失效。项目内原文件与远端对象应指向同一版本；系统无法保证外部 URL 内容永久不变，建议使用不可变对象或服务商素材 ID。

- MiniMax：图/视频/音频可直接内嵌 Base64；大文件使用 HTTPS 或 `mm_file://文件ID`。
- Seedance：图像/音频可内嵌 Base64；**视频必须显式提供 HTTPS 或 `asset://素材ID`**。不会把服务器本地路径伪装为云端可访问地址。
- 所有请求体不得超过 64 MiB。Base64 会增大体积；超限拒绝，不压低素材质量。图片/视频/音频的格式、尺寸、数量、时长分别按官方规则验证。
- MiniMax 和 Seedance 2.0 的参考视频、音频各合计不超过 15 秒；2.5 分别提升到 30 秒。Seedance 2.0 音频必须配合图像或视频，2.5 可只参考音频。

示例：先将 motion.mp4 注册/绑定为 reference_videos，再提交：

```bash
./studio submit cloud-film 01 --shot 001 --kind video --params '{"reference_locations":{"assets/motion-真实ID.mp4":"asset://你已上传的素材ID"}}'
```

`image_mode: reference` 默认把图片作为语义/人物/风格参考。显式 `image_mode: first_last` 将 reference_images 中第一张作为首帧、第二张作为尾帧；要求 1–2 张图、不与其他模态或风格参考混用，ratio 必须 adaptive。可通过 `video-model --image-mode first_last --ratio adaptive` 配置。`use_selected_image` 仍只是把已审核图片加入当前参考列表，模式由 image_mode 决定。

本次接入覆盖文生、语义多模态参考和首尾帧生成。Seedance 的编辑/延长、自动时长 -1、Draft、联网工具，以及 MiniMax 独立 Context-IR/重生成任务不属于当前镜头生成契约，未作为伪选项开放。

## 恢复、导出与验证边界

每次付费请求前先持久化提交意图；取得远端 ID 后写 `remote-task.json`。GET 查询遇到临时网络错误/429/服务端错误会有界退避，POST 不自动重试。worker 重启后继续原 task_id；超时/下载中断可 `./studio resume-api TASK_ID`，继续同一候选的查询/下载，不再生成。若 POST 响应丢失且没有 task_id，自动重提被阻止，应先去服务商控制台核对。`retry` 表示主动新生成，会再次计费，需要重新审核输入。

API 输出原容器直接保存为 video.mp4 / video.mov，并记录真实尺寸、FPS、音轨、编码、像素格式、API 任务 ID、usage、返回 seed 和耗时。远端原始响应保留在私有制作目录；API key 不写入任务/metrics，不向下载 CDN 转发 Authorization。服务商 URL 有效期有限，应及时恢复下载。

API profile 默认 `export.preserve_source_format: true`，按实际素材尺寸导出；保持 HEVC/10bit 和原生 AAC，MOV 的 PCM 音轨也保持。字幕版需要视频编码时保持原像素格式。不同尺寸/编码/音频规格的候选混合导出会报错，避免拼接损坏或隐式降质；需先统一源规格，或显式关闭此选项并设置统一导出画布。口型始终等待人工验收。

费用取决于官方当前计费、参考素材、分辨率与实际生成时长；失败/超时不等于服务商一定不计费。高质量默认配置会增加费用，无自动低价重试或跨供应商 fallback。当前回归测试使用 HTTP 替身与真实本地媒体，不等同于已经用你的账号完成付费实测。
