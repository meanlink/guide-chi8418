import logging
import os
import socket
import time
import uuid
from pathlib import Path

from redis import Redis
from sqlalchemy import select, update

from src.config.database import Session
from src.config.settings import REDIS_URL, SEGMENT_LEASE_SECONDS
from src.constants.enums import JobStatus
from src.models import Job, Report
from src.services import segments
from src.services.audit import record
from src.services.reports import deliver

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('worker')

WORKER_ID = os.getenv('WORKER_ID') or f'{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}'


def execute(job_id, owner=WORKER_ID):
    """执行或续跑任务；返回终态或 None（未抢到 / 进行中）。"""
    status = segments.run_job(job_id, owner)
    if status in (JobStatus.completed.value, JobStatus.failed.value):
        with Session() as db:
            job = db.get(Job, job_id)
            record(db, 'worker', 'job.' + status, {'id': job_id, 'worker': owner, 'segments': job.segments_total if job else 0})
            db.commit()
    return status


def reclaim_expired():
    """把租约已过期的 running 任务重新排队，让其他进程可接管（已完成段原样复用）。"""
    now = time.time()
    with Session() as db:
        expired = list(db.scalars(
            select(Job.id).where(
                Job.status == JobStatus.running.value,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= now,
            )
        ))
    for job_id in expired:
        enqueue(job_id)
    return expired


def auto_retry_failed():
    """运行时失败的段在尝试次数内自动重试，避免整批从头再来。"""
    with Session() as db:
        failed = list(db.scalars(select(Job).where(Job.status == JobStatus.failed.value).limit(20)))
        for job in failed:
            indexes = segments.eligible_auto_retry(db, job.id)
            if indexes and segments.retry_segments(db, job, indexes):
                record(db, 'worker', 'job.segment_retry', {'id': job.id, 'segments': [i + 1 for i in indexes]})
                db.commit()
                enqueue(job.id)


def enqueue(job_id):
    try:
        r = Redis.from_url(REDIS_URL, socket_timeout=3)
        r.rpush('analysis:queue', job_id)
        r.close()
    except Exception:
        pass  # Redis 不可用时 SQL 排队状态仍是兜底


def tick():
    now = time.time()
    with Session() as db:
        queued = list(db.scalars(
            select(Job.id).where(
                Job.status.in_((JobStatus.queued.value, JobStatus.running.value))
            ).order_by(Job.id).limit(3)
        ))
    for job_id in queued:
        execute(job_id)
    with Session() as db:
        due = list(db.scalars(
            select(Report.id).where(Report.enabled == True, Report.next_run <= now)  # noqa: E712
        ))
    for report_id in due:
        with Session() as db:
            # 用 next_run 做乐观条件，避免多个 worker 重复分发同一份归档。
            report = db.get(Report, report_id)
            claimed = db.execute(
                update(Report)
                .where(Report.id == report_id, Report.enabled == True, Report.next_run <= now)  # noqa: E712
                .values(next_run=now + report.interval_seconds)
            )
            if claimed.rowcount != 1:
                db.rollback()
                continue
            delivery = deliver(db, report)
            if delivery:
                record(db, 'worker', 'report.delivered', {'id': report.id, 'delivery': delivery.id})
            db.commit()


def main():
    r = Redis.from_url(REDIS_URL, socket_timeout=3)
    log.info('worker %s 启动，租约 %ss', WORKER_ID, SEGMENT_LEASE_SECONDS)
    reclaim_expired()
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item = r.blpop('analysis:queue', timeout=1)
            if item:
                execute(int(item[1]))
            reclaim_expired()
            auto_retry_failed()
            tick()
        except Exception:
            log.exception('Worker retry')
            time.sleep(2)


if __name__ == '__main__':
    main()
