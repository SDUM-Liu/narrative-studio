"""Allocation isolation and resource selection; no cluster or CUDA needed."""
import json
import os
import socket
import time
import pytest
from pathlib import Path

from scripts import services
from scripts.slurm_submit import select
from engine.scheduler.worker import gpu_stats
from engine import core


def configuration():
    return json.loads((Path(__file__).resolve().parents[1]/'configs/slurm-cluster.json').read_text())


def node(name, total=4, used=0, state=None):
    return {'name': name, 'tres': f'cpu=64,mem=483716M,gres/gpu={total}',
            'tres_used': f'gres/gpu={used}', 'state': state or ['IDLE'],
            'real_memory': 221636, 'alloc_memory': 0}


def test_free_11gb_gpus_do_not_displace_queued_production_gpus():
    choice = select(configuration(), [node('gl8'), node('gh6', used=4)])
    assert choice['node'] == 'gh6'
    assert choice['gpus'] == 2
    assert choice['available_gpus'] == 0


def test_best_available_class_and_account_gpu_limit():
    choice = select(configuration(), [node('gh6', used=4), node('gn12'), node('gn5')], requested=8)
    assert choice['node'] == 'gh6'
    assert choice['gpus'] == 2
    assert select(configuration(), [node('gh6'), node('gn12')])['node'] == 'gh6'


def test_drained_a800_never_falls_back_to_consumer_gpus():
    with pytest.raises(ValueError,match='No healthy node'):
        select(configuration(), [node('gh6', state=['IDLE', 'DRAIN']), node('gn12', used=3)])
    choice=select(configuration(),[node('gh6',used=3)])
    assert choice['gpus']==2  # wait for the full tensor-parallel allocation
    assert choice['available_gpus']==1


def test_nvml_queries_allocated_uuid_instead_of_logical_lane(monkeypatch):
    monkeypatch.setenv('NARRATIVE_GPU_DEVICE', 'GPU-assigned-card')
    def run(command, **kwargs):
        assert command[-1] == '--id=GPU-assigned-card'
        return type('Result', (), {'stdout': '24000, 500'})()
    monkeypatch.setattr('engine.scheduler.worker.subprocess.run', run)
    assert gpu_stats(0) == [24000, 500]


def test_torch_uuid_is_normalized_for_cuda_and_nvml(monkeypatch):
    def run(command, **kwargs):
        return type('Result', (), {'stdout': '["card-uuid", "GPU-already-prefixed", "MIG-partition"]'})()
    monkeypatch.setattr(services.subprocess, 'run', run)
    assert services.visible_devices() == ['GPU-card-uuid', 'GPU-already-prefixed', 'MIG-partition']


def test_remote_process_id_is_never_treated_as_local_or_killed(tmp_path, monkeypatch):
    runtime = tmp_path/'runtime'
    runtime.mkdir()
    monkeypatch.setattr(services, 'ROOT', tmp_path)
    monkeypatch.setattr(services, 'STATE', runtime/'services.json')
    remote = {'pid': os.getpid(), 'start_ticks': 'wrong', 'host': socket.gethostname()+'-other', 'job_id': '123'}
    assert not services.alive(remote)
    heartbeat = runtime/'slurm-123.heartbeat'
    heartbeat.touch()
    assert services.alive(remote)
    services.STATE.write_text(json.dumps({'worker-0': remote}))
    monkeypatch.setattr(services.os, 'killpg', lambda *_: (_ for _ in ()).throw(AssertionError('foreign host PID')))
    services.stop()
    os.utime(heartbeat, (time.time()-60, time.time()-60))
    assert not services.alive(remote)


def test_shared_database_uses_configured_rollback_journal(tmp_path, monkeypatch):
    (tmp_path/'configs').mkdir()
    (tmp_path/'configs/sqlite-journal-mode.txt').write_text('DELETE\n')
    monkeypatch.setattr(core, 'ROOT', tmp_path)
    with core.db() as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
