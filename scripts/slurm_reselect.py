"""Recheck free production resources after model conversion, before dependency release."""
import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.slurm_submit import select

ROOT = Path(__file__).resolve().parents[1]


def main():
    record = ROOT/'runtime/deploy-jobs.json'
    if not record.exists():
        return
    jobs = json.loads(record.read_text())
    old = jobs['production']
    state = subprocess.run(['squeue', '-h', '-j', old, '-o', '%T'], capture_output=True, text=True)
    if state.stdout.strip() != 'PENDING':
        return
    config = json.loads((ROOT/'configs/slurm-cluster.json').read_text())
    nodes = json.loads(subprocess.check_output(['scontrol', 'show', 'nodes', '--json'], text=True))['nodes']
    choice = select(config, nodes)
    previous = jobs['production_selection']
    if not choice['available_gpus'] or all(choice[key] == previous[key] for key in ['node', 'gpus']):
        print('Production allocation unchanged:', previous, flush=True)
        return
    # Both jobs depend on this still-running CPU job, so neither can start
    # during the replacement. Keep the old job until submission succeeds.
    plan = json.loads(subprocess.check_output([
        sys.executable, str(ROOT/'scripts/slurm_submit.py'), '--submit',
        f'--dependency=afterok:{os.environ["SLURM_JOB_ID"]}'
    ], text=True))
    subprocess.run(['scancel', old], check=True)
    jobs.update(production=plan['job_id'], production_selection=plan['selection'])
    part = record.with_suffix('.tmp')
    part.write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    part.replace(record)
    print('Replaced pending production job', old, 'with', plan['job_id'], plan['selection'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Resource recheck could not finish; inspect existing Slurm jobs:', error, flush=True)
