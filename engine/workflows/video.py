"""Provider-aware validation with one material/configuration review contract."""
from engine.workflows.h3 import references, fingerprint, digest, compose_prompt


def validate(p, pid, media=True):
    if p.get('provider', 'minimax-h3') == 'minimax-h3':
        from engine.workflows.h3 import validate as local_validate
        return local_validate(p, pid, media)
    from engine.workflows.cloud_video import validate as cloud_validate
    return cloud_validate(p, pid, media)
