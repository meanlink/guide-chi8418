"""数据库初始化与增量补列。

create_all 只建新表，不会给既有 jobs 表补列；这里用幂等 ALTER 保证
升级后仍能保存分段进度。升级前的已完成任务按当前分段大小补建检查点，
使旧结论仍可复核、批准与分发。
"""
import logging

from sqlalchemy import inspect, select, text

from src.config.database import Base, Session, engine
from src import models  # noqa: F401 - 注册所有模型后 create_all 才能建表

log = logging.getLogger(__name__)


NEW_JOB_COLUMNS = {
    'segments_total': ("INTEGER DEFAULT 0 NOT NULL", 0),
    'segments_reused': ("INTEGER DEFAULT 0 NOT NULL", 0),
    'epoch': ("INTEGER DEFAULT 0 NOT NULL", 0),
    'lease_owner': ("VARCHAR(120) DEFAULT '' NOT NULL", ''),
    'lease_expires_at': ("__FLOAT__", None),
}


def _add_columns():
    inspector = inspect(engine)
    if 'jobs' not in inspector.get_table_names():
        return
    existing = {column['name'] for column in inspector.get_columns('jobs')}
    float_type = 'DOUBLE PRECISION' if engine.dialect.name == 'postgresql' else 'FLOAT'
    with engine.begin() as connection:
        for name, (ddl, _) in NEW_JOB_COLUMNS.items():
            if name not in existing:
                connection.execute(text(f'ALTER TABLE jobs ADD COLUMN {name} {ddl.replace("__FLOAT__", float_type)}'))


def _backfill_completed():
    """为升级前的已完成任务补写分段检查点；不重算结论本身。"""
    from src.constants.enums import JobStatus
    from src.models import Dataset, Job, JobSegment
    from src.services.segments import checkpoint_all

    with Session() as db:
        jobs = db.scalars(select(Job).where(Job.status == JobStatus.completed.value).limit(200)).all()
        for job in jobs:
            if job.segments_total or db.scalar(select(JobSegment).where(JobSegment.job_id == job.id).limit(1)):
                continue
            dataset = db.get(Dataset, job.dataset_id)
            if not dataset:
                continue
            try:
                checkpoint_all(db, job, dataset)
                db.commit()
            except Exception:
                db.rollback()
                log.exception('历史任务 %s 的分段检查点补建失败', job.id)


def migrate():
    Base.metadata.create_all(engine)
    _add_columns()
    _backfill_completed()


if __name__ == '__main__':
    migrate()
