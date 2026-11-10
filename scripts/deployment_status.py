"""Local deployment progress without exposing proxy settings or tokens."""
import json
import getpass
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
items = json.loads((ROOT/'model-manifests/models.json').read_text())
progress = {}
for group in ['h3', 'production', 'tts']:
    selected = [i for i in items if i['group'] == group]
    finished = [i for i in selected if (ROOT/i['path']).is_file() and (ROOT/i['path']).stat().st_size == i['size']]
    progress[group] = {'files_ready': len(finished), 'files_total': len(selected),
                       'gib_ready': round(sum(i['size'] for i in finished)/1024**3, 2),
                       'gib_total': round(sum(i['size'] for i in selected)/1024**3, 2)}
markers = {name: (p.read_text().strip() if (p:=ROOT/'runtime'/f'{name}.status').exists() else 'pending')
           for name in ['h3-runtime', 'h3-models', 'bootstrap', 'models', 'tts', 'deploy']}
print(json.dumps({'markers': markers, 'models': progress}, ensure_ascii=False, indent=2))
subprocess.run(['squeue', '-u', getpass.getuser(),
                '-o', '%.15i %.12P %.24j %.10T %R'], check=False)
