"""Local adapter registry. Extend here without changing scripts or queue storage.

Contract: required_files(params) -> list[Path]; run(task,gpu,out) -> (Path,dict).
Cloud/billed providers are deliberately absent from the default registry.
"""
import importlib
ADAPTERS={'comfy':('engine.providers.comfy',{'image','edit','video'}),'qwen3-tts':('engine.providers.qwen_tts',{'tts'})}
def resolve(name,kind):
    if name not in ADAPTERS or kind not in ADAPTERS[name][1]:raise ValueError(f'未注册的本地适配器或任务类型: {name}/{kind}；未启用外部计费API')
    return importlib.import_module(ADAPTERS[name][0])
