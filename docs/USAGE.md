# 使用说明

## 导演稿

JSON 为镜头对象数组，CSV 为一行一镜。字段支持：

| 字段 | 含义 |
|---|---|
| `episode_id` / `episode` | 分集 ID |
| `shot_id` / `shot` | 镜头 ID，同一分集唯一 |
| `prompt` / `positive` | 首帧画面描述 |
| `action_prompt` / `motion_prompt` | 动作和运镜描述 |
| `negative_prompt` / `negative` | 负面提示 |
| `target_duration` / `duration` | 镜头秒数，受预算上限约束 |
| `characters` | 已登记人物 ID 数组 |
| `character_stage` | 人物阶段 |
| `reference_images` | 作品内已有文件的相对路径，最多 3 张 |
| `dialogue` | 对白文本 |
| `speaker` | 作品配置中 `voices` 的键 |
| `seed` | 生成种子，不能单独保证人物一致性 |

还保留 `start/end`、`camera/action/expression/blocking/sound` 和原始输入。时间码支持秒数和 `HH:MM:SS`，起止时间必须与时长一致。CSV 数组可使用 JSON 数组或分号分隔。

```bash
./studio import my-film script.csv --mapping configs/field-mapping.example.json
./studio shots my-film --episode 01
```

导入前校验全部镜头。重复 ID、未登记声线、跨作品路径和不一致时间码会报错；已有镜头不会静默覆盖。

## 资产与参考图

```bash
./studio asset my-film hero character --file /path/to/hero.png --meta '{"name":"主角"}'
./studio asset my-film hero voice --meta '{"speaker":"Dylan","language":"Chinese"}'
# 将 assets/hero-xxxxxxxx.png 替换为上一条资产命令返回的 path
./studio bind my-film 01 001 assets/hero-xxxxxxxx.png --prompt '调整后的镜头提示'
```

资产命令的返回值包含实际保存路径，应使用该路径绑定参考图。角色和声线设置分别保存在作品配置。当前 TTS 使用 Qwen3 CustomVoice 官方预设声线；没有克隆声音的界面。

## 队列、审核与重启

```bash
./studio submit my-film 01 --kind image --shot 001
./studio submit my-film 01 --kind video --shot 001
./studio submit my-film 01 --kind tts --shot 001
./studio log TASK_ID --tail 20
./studio status
bash scripts/stop.sh
bash scripts/start.sh
```

每张 GPU 独立执行任务，不跨卡拼接显存。空闲显存不足或模型文件未就绪时保留排队；OOM 或编码维度错误会暂停对应 GPU。修复配置后运行 `./studio resume --gpu 0`。`min_free_mib` 是调度门槛，不是峰值显存保证。

服务管理仅控制本项目登记且 PID 启动时间匹配的进程。被中断的运行任务在 worker 重启时标为失败，使用 `retry` 创建新版本。SQLite 位于 `runtime/studio.sqlite3`，备份运行中的数据库应使用 SQLite backup API。

`retry` 使用当前模型配置，并保留原任务的显式创意和采样参数变更。所有候选保留独立任务 ID。视频必须依赖当前通过审核的首帧，重新生成首帧后需要重新审核相应视频。

## 可选 ASR 初筛

```bash
bash scripts/setup_asr.sh
.venv-asr/bin/python scripts/asr_check.py
```

结果写入 `runtime/audio-check.json`。当前脚本针对中文配音执行识别比对与基础声学检查；ASR 一致不等于音质和表演通过，仍需听审。

## 扩展模型

作品 YAML 的 `models` 控制权重、LoRA、步数和显存门槛。`engine/providers/` 提供适配器注册；新增适配器应实现 `required_files(params)` 与 `run(task, gpu, out)`，并登记允许的任务类型。Comfy API 图由 `engine/workflows/__init__.py` 根据作品参数生成。

ComfyUI 的提交固定于 `configs/comfy-commit.txt`。自定义扩展仅白名单启用 `narrative_metrics`，记录本地节点耗时与显存，并检查 Wan 文本编码维度。升级 ComfyUI 或更换权重后应重新验证工作流和生成质量。
