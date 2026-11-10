"""Run an approved episode on one allocated H3 GPU group, then release Slurm."""
import json
import hashlib
import os
from pathlib import Path
import sys
import time
import subprocess

from engine import core
from scripts.services import visible_devices
from engine.scheduler import worker


def main():
    if len(sys.argv)!=3:raise SystemExit('Usage: python -m scripts.run_episode PROJECT EPISODE')
    pid,ep=map(core.ident,sys.argv[1:]);cfg=core.config(pid)
    if not os.environ.get('SLURM_JOB_ID'):raise RuntimeError('Episode production must run inside Slurm')
    devices=visible_devices()
    if len(devices)!=cfg['models']['video']['num_gpus']:raise RuntimeError('Allocated GPU count differs from reviewed project configuration')
    os.environ['NARRATIVE_H3_DEVICES']=json.dumps(devices)
    os.environ['CUDA_VISIBLE_DEVICES']=','.join(devices)
    record=core.project_dir(pid)/'production'/f'episode-{ep}-slurm-{os.environ["SLURM_JOB_ID"]}.json'
    state={'project':pid,'episode':ep,'slurm_job_id':os.environ['SLURM_JOB_ID'],
           'devices':devices,'started':time.time(),'status':'running','quality_downgrade':False}
    state['code_revision']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=core.ROOT,text=True).strip()
    state['sglang_revision']=(core.ROOT/'configs/sglang-commit.txt').read_text().strip()
    state['model_manifest_sha256']=hashlib.sha256((core.ROOT/'model-manifests/models.json').read_bytes()).hexdigest()
    record.write_text(json.dumps(state,indent=2))
    sys.argv=['worker','--gpu','-1','--kinds','video','--project',pid,'--episode',ep,'--exit-when-idle']
    try:
        worker.main();state['status']='generated_waiting_for_output_review'
    except BaseException as exc:
        state.update(status='failed',error=str(exc));raise
    finally:
        state['finished']=time.time()
        state['tasks']=[{k:t[k] for k in ['id','shot','status','output','reason']} for t in core.tasks(pid,ep,limit=500)]
        record.write_text(json.dumps(state,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
