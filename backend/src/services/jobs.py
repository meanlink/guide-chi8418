import time

from fastapi import HTTPException
from redis import Redis

from src.config.settings import REDIS_URL
from src.models import Dataset, Job
from src.services.business import metadata
from src.services.segments import ensure_segments


def create(db, data, user):
    if not db.get(Dataset, data.dataset_id):
        raise HTTPException(404, '数据批次不存在')
    allowed = {p['key']: p for p in metadata().get('parameters', [])}
    if set(data.params) - set(allowed):
        raise HTTPException(422, '包含未知分析参数')
    params = {k: p['default'] for k, p in allowed.items()}
    params.update(data.params)
    for key, p in allowed.items():
        v = params[key]
        if p['type'] == 'number':
            try:
                v = float(v)
                if not (p.get('min', -1e9) <= v <= p.get('max', 1e9)):
                    raise ValueError()
                params[key] = v
            except (ValueError, TypeError):
                raise HTTPException(422, f'{p["label"]} 超出范围或非数值')
        elif str(v) not in [str(o['value']) for o in p.get('options', [])]:
            raise HTTPException(422, f'{p["label"]} 不是有效选项')
    job = Job(name=data.name, dataset_id=data.dataset_id, params=params, created_by=user.id, created_at=time.time(), status='queued')
    db.add(job)
    db.flush()
    # 分段边界在创建时固化，重跑/接管都按同一组记录边界复用检查点。
    ensure_segments(db, job, db.get(Dataset, data.dataset_id))
    return job


def enqueue(job_id):
    try:
        r = Redis.from_url(REDIS_URL)
        r.rpush('analysis:queue', job_id)
        r.close()
    except Exception:
        pass  # Redis 不可用时 SQL 排队状态仍是兜底
