"""Select the best schedulable production GPU class within this account's limits."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def gpu_count(tres):
    return sum(int(value) for value in re.findall(r'(?:^|,)gres/gpu(?:\:[^=,]+)?=(\d+)', tres))


def select(config, nodes, requested=2, profile='a800', min_vram=80000):
    candidates = []
    for cls in config['gpu_classes']:
        if cls['vram_mib'] < min_vram:
            continue
        if profile != 'auto' and profile.lower() not in cls['model'].lower():
            continue
        for node in nodes:
            if node['name'] not in cls['nodes']:
                continue
            if set(node['state']) & {'DOWN', 'DRAIN', 'DRAINED', 'FAIL', 'NOT_RESPONDING', 'MAINT'}:
                continue
            total = gpu_count(node['tres'])
            count = min(requested, config['limits']['gpus_per_job'], total)
            if not count:
                continue
            free = max(0, total - gpu_count(node.get('tres_used', '')))
            memory = node['real_memory'] - node.get('alloc_memory', 0)
            available = min(count, free) if memory >= config['limits']['memory_gib_per_job'] * 1024 else 0
            candidates.append({**cls, 'node': node['name'], 'gpus': count,
                               'free_gpus': free, 'available_gpus': available})
    if not candidates:
        raise ValueError('No healthy node matches the production GPU requirement')
    # Prefer an immediately schedulable class; within it, choose quality then
    # maximum usable GPU count. Otherwise queue the best supported hardware.
    ready = [c for c in candidates if c['available_gpus'] >= requested]
    chosen = max(ready or candidates, key=lambda c: (c['rank'], c['available_gpus'], c['gpus']))
    if ready:
        chosen['gpus'] = min(requested,chosen['available_gpus'])
    return chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['auto', 'a800', '4090', '3090'], default='a800')
    parser.add_argument('--gpus', type=int, default=2)
    parser.add_argument('--time', default='1-00:00:00')
    parser.add_argument('--submit', action='store_true', help='Without this option, print a plan only')
    parser.add_argument('--dependency', help='Slurm dependency such as afterok:12345')
    parser.add_argument('--project',help='Run only this project and release GPUs after episode tasks finish')
    parser.add_argument('--episode',default='01')
    args = parser.parse_args()
    config = json.loads((ROOT/'configs/slurm-cluster.json').read_text())
    if not 2 <= args.gpus <= config['limits']['gpus_per_job']:
        parser.error('--gpus must request the full H3 group (>=2) within the configured account limit')
    nodes = json.loads(subprocess.check_output(['scontrol', 'show', 'nodes', '--json'], text=True))['nodes']
    choice = select(config, nodes, args.gpus, args.profile)
    if choice['gpus'] != args.gpus:parser.error('No node supports the complete requested H3 allocation')
    (ROOT/'logs').mkdir(exist_ok=True)
    (ROOT/'runtime').mkdir(exist_ok=True)
    job_name=f'h3-{args.project}-{args.episode}' if args.project else 'narrative-h3'
    command = ['sbatch', '--parsable', f'--job-name={job_name}',
               f"--partition={choice['partition']}", f"--nodelist={choice['node']}",
               f"--account={config['account']}", f"--qos={config['qos']}",
               '--nodes=1', '--ntasks=1', f"--cpus-per-task={min(16,config['limits']['cpus_per_job'])}",
               f"--mem={config['limits']['memory_gib_per_job']}G",
               f"--gres=gpu:{choice['gpus']}", f'--time={args.time}',
               f'--chdir={ROOT}', f'--output={ROOT}/logs/slurm-%j.log',
               '--signal=B:TERM@60', '--export=ALL', str(ROOT/'scripts/slurm_run.sh'),
               choice['vram_mode']]
    if args.dependency:
        command.insert(1, f'--dependency={args.dependency}')
    if args.project:
        import sys
        sys.path.insert(0,str(ROOT))
        from engine.core import config,ident
        cfg=config(args.project);ident(args.episode)
        if cfg['models']['video'].get('provider')!='minimax-h3':raise ValueError('API projects use scripts/start.sh --only api review; do not allocate GPUs')
        command.extend([args.project,args.episode])
    plan = {'selection': choice, 'command': command,
            'note': 'Local H3 uses the entire allocation with tensor parallelism. No quality fallback; idle GPU counts do not guarantee a start time.'}
    if args.submit:
        job_id = subprocess.check_output(command, text=True).strip().split(';')[0]
        plan['job_id'] = job_id
        (ROOT/'runtime/slurm-submission.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
