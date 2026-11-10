"""Provider contract: required_files(params); run(task, gpu, out) -> (Path, metrics)."""
import importlib
ADAPTERS={'comfy':('engine.providers.comfy',{'image','edit'}),'minimax-h3':('engine.providers.minimax_h3',{'video'}),'qwen3-tts':('engine.providers.qwen_tts',{'tts'})}
def default_provider(kind):return {'video':'minimax-h3','tts':'qwen3-tts'}.get(kind,'comfy')
def resolve(name,kind):
    if name not in ADAPTERS or kind not in ADAPTERS[name][1]:raise ValueError(f'未注册的适配器或任务类型: {name}/{kind}；视频仅支持 minimax-h3，请迁移旧项目配置')
    return importlib.import_module(ADAPTERS[name][0])
