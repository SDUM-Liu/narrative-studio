# Slurm 本地 H3 部署

视频只使用本地 H3，不调用云端生成服务。控制层、下载、网页审核可在允许联网的登录节点运行；GPU 模型推理只在计算分配中运行。

## 当前账号与排队事实

2026-11-10 查询 `sacctmgr show assoc`：账号 grads / q_grads，每作业上限 **2 GPU、32 CPU、64 GiB RAM**。配置见 configs/slurm-cluster.json。GPUHigh 唯一在线节点 gh6 的 4 张 A800 已全部分配，其他节点为 down。

当时 `squeue --start -j 19979` 对原有双 A800 作业的预测为 **2026-11-18 22:39:17**（集群显示时间），约还要 9 天。它是预测，不是预约保证。其他作业提前结束、新高优先级任务等都会改变等待时间。H3 真实推理时长尚未测量，排队时间和推理时间应分开。

```bash
squeue --start -j JOB_ID
squeue -p GPUHigh -o '%.12i %.10u %.10Q %.2t %.19S %.19e %.12l %.22R'
scontrol show job JOB_ID
scontrol show node gh6
sacctmgr show assoc where user="$USER" format=User,Account,Partition,QOS,MaxTRESPJ -P
```

## 安装、下载和提交

```bash
bash scripts/install_control.sh
bash scripts/bootstrap.sh
.venv-control/bin/python scripts/download_models.py --group h3 --jobs 4
# 计划模式不提交资源
.venv-control/bin/python scripts/slurm_submit.py --profile a800 --gpus 2
.venv-control/bin/python scripts/slurm_submit.py --profile a800 --gpus 2 --submit
bash scripts/start.sh --only review
```

已排队的旧作业保留的是提交时脚本副本。更新仓库不会改变它的脚本；切换到 H3 要重新提交。项目不会自动取消旧作业，避免丢失已有排队位置。旧作业不应被当作新的 H3 推理验收。

默认不因 4090/3090 空闲而换卡，不因只有一张空闲 A800 就改为单卡。整个 2 卡分配由一个 h3 worker 持有，SGLang TP2 / Ulysses1。`slurm_run.sh` 检查 H3 环境和权重状态，再校验分配内的 GPU。服务解析实际分配 UUID，CUDA_VISIBLE_DEVICES 保留整个双卡掩码，不再每卡一个视频服务。锁和心跳保留在共享 runtime 下；SQLite 使用本集群已配置的 DELETE journal，不能在不支持共享锁的文件系统上直接跑多节点队列。

运行日志为 logs/slurm-JOB.log 和 logs/h3.log；每候选还有 h3-inference.log / h3-performance.json。模型文件和精度不随失败改变，OOM 暂停 lane -1；修复资源后 `./studio resume --gpu -1` 并显式 retry / review-inputs。

## 资源容量

原始 FL2VA 与 Ref2VA 共约 268 GiB，按任务只加载其中一个分区；不同时启动两套权重服务。不对稠密模型使用少步加速 LoRA、量化或近似缓存。启动时和 15 秒多参考任务的峰值尚未在本集群验证，**64 GiB RAM 可能不足**；如失败，申请管理员提高主机内存配额（可评估 256 GiB 以上）或 GPU 配额，再显式调整配置。这里的建议是部署容量规划，并非已有 A800 基准数据。

更大 GPU 配额时，明确同步项目 video.num_gpus / tp_size / ulysses_degree 和 Slurm 请求。TP / Ulysses 拓扑必须符合 H3 头数及 SGLang 约束，不能只增加 sbatch 数字。当前默认两卡配方保持原始权重质量，不声称已完成 15 秒最大参考负载验证。

本地 Qwen 图像和可选 TTS 使用单独分配内的 `scripts/services.py run --only compute`，不要与 H3 占用同一组 GPU。Qwen 原生 BF16 图像模型约 38 GiB，编码器另占空间；这条辅助资产流程不再下载或执行 Wan。

## 有限分集制作任务

`slurm_submit.py --project PROJECT --episode 01 --submit` 提交有限作业，使用单独项目过滤，不执行其他作品的队列。其启动脚本调用 scripts/run_episode.py，验证分配数量并建立 UUID 掩码；失败时停止，全部任务进入 review 后正常退出并释放 GPU。它不会自动通过视频审核或导出未审成片。

旧版已排队脚本应暂停或取消，再提交新入口，不能只修改仓库文件就假定旧脚本已更新。安装脚本明确跳过上游可选 Rust 扩展，因此不需要为 H3 原生推理另装 Cargo。
