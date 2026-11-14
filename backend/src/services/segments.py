"""分段核验的生命周期：租约、纪元栅栏、检查点与最终发布。

并发模型：
- 任务行上的 lease_owner/lease_expires_at 保证同一时刻只有一个执行者；
- 每次接管都会把 jobs.epoch 加一，所有分段与任务的写入都要求
  “分段行 epoch == 我的 epoch” 且 “任务行 epoch == 我的 epoch”，
  因此失去接管资格的旧进程即使晚回结果也无法落库；
- 分段行只允许从非 completed 状态转变为 completed 一次；合并只读取
  completed 行并按序号各带入一次，所以每条计数恰好汇入一次。
"""
import json
import logging
import math
from collections import Counter
import time

from sqlalchemy import and_, exists, func, or_, select, update

from src.config.database import Session
from src.config.settings import SEGMENT_LEASE_SECONDS, SEGMENT_MAX_ATTEMPTS, SEGMENT_SIZE
from src.constants.enums import JobStatus, SegmentStatus
from src.models import Dataset, Job, JobSegment
from src.services import business

log = logging.getLogger(__name__)


class StaleLease(Exception):
    """当前执行者已失去接管资格，必须立即停止写入。"""


def _now():
    return time.time()


def _epoch_guard(job_id, epoch):
    return exists().where(and_(Job.id == job_id, Job.epoch == epoch))


def _job_failed(db, job_id, epoch, message, now=None):
    now = _now() if now is None else now
    written = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.epoch == epoch, Job.status == JobStatus.running.value)
        .values(
            status=JobStatus.failed.value,
            result={},
            error=str(message)[:990],
            finished_at=now,
            lease_owner='',
            lease_expires_at=None,
        )
    )
    db.commit()
    return written.rowcount == 1


def claim_job(db, job_id, owner, now=None):
    """原子抢占任务租约；成功返回本次执行的 epoch，失败返回 None。"""
    now = _now() if now is None else now
    claimed = db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_((JobStatus.queued.value, JobStatus.running.value)),
            or_(
                Job.lease_owner.is_(None),
                Job.lease_owner == '',
                Job.lease_expires_at.is_(None),
                Job.lease_expires_at <= now,
            ),
        )
        .values(
            lease_owner=owner,
            lease_expires_at=now + SEGMENT_LEASE_SECONDS,
            epoch=Job.epoch + 1,
            status=JobStatus.running.value,
        )
    )
    if claimed.rowcount != 1:
        db.rollback()
        return None
    epoch = db.scalar(select(Job.epoch).where(Job.id == job_id))
    reused = db.scalar(
        select(func.count()).select_from(JobSegment).where(
            JobSegment.job_id == job_id, JobSegment.status == SegmentStatus.completed.value
        )
    ) or 0
    db.execute(update(Job).where(Job.id == job_id, Job.epoch == epoch).values(segments_reused=reused))
    db.commit()
    return epoch


def renew(db, job_id, owner, epoch, now=None):
    """续租；返回 False 说明任务已被更高 epoch 的进程接管。"""
    now = _now() if now is None else now
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.epoch == epoch, Job.lease_owner == owner, Job.status == JobStatus.running.value)
        .values(lease_expires_at=now + SEGMENT_LEASE_SECONDS)
    )
    db.commit()
    return result.rowcount == 1


def relabel_reused(db, job_id, epoch):
    reused = db.scalar(
        select(func.count()).select_from(JobSegment).where(
            JobSegment.job_id == job_id, JobSegment.status == SegmentStatus.completed.value
        )
    ) or 0
    db.execute(update(Job).where(Job.id == job_id, Job.epoch == epoch).values(segments_reused=reused))
    db.commit()


def ensure_segments(db, job, dataset):
    """按分段大小固化边界；已存在则原样保留，恢复时直接沿用已完成段。"""
    existing = db.scalar(select(func.count()).select_from(JobSegment).where(JobSegment.job_id == job.id)) or 0
    if existing:
        return False
    total = len(dataset.records)
    count = max(1, math.ceil(total / SEGMENT_SIZE))
    for index in range(count):
        start = index * SEGMENT_SIZE
        db.add(
            JobSegment(
                job_id=job.id,
                index=index,
                start_index=start,
                records_count=min(SEGMENT_SIZE, total - start),
                status=SegmentStatus.pending.value,
                epoch=0,
                updated_at=_now(),
            )
        )
    db.execute(update(Job).where(Job.id == job.id).values(segments_total=count))
    db.commit()
    return True


def bounds_valid(db, job, dataset):
    """分段边界必须完整覆盖数据且不重不漏。"""
    rows = db.scalars(
        select(JobSegment).where(JobSegment.job_id == job.id).order_by(JobSegment.index)
    ).all()
    cursor = 0
    for expected, row in enumerate(rows):
        if row.index != expected or row.start_index != cursor or row.records_count < 1:
            return False
        cursor += row.records_count
    return cursor == len(dataset.records)


def claim_segment(db, job_id, index, epoch, now=None):
    """抢占一个尚未完成的分段，包括上一代执行者遗留的 running 段。"""
    now = _now() if now is None else now
    result = db.execute(
        update(JobSegment)
        .where(
            JobSegment.job_id == job_id,
            JobSegment.index == index,
            JobSegment.status != SegmentStatus.completed.value,
            JobSegment.epoch <= epoch,
            _epoch_guard(job_id, epoch),
        )
        .values(
            status=SegmentStatus.running.value,
            epoch=epoch,
            attempts=JobSegment.attempts + 1,
            error='',
            error_kind='',
            updated_at=now,
        )
    )
    db.commit()
    return result.rowcount == 1


def complete_segment(db, job_id, index, epoch, partial, now=None):
    now = _now() if now is None else now
    buckets = sorted([[name, arm, list(counts)] for (name, arm), counts in partial['buckets'].items()])
    result = db.execute(
        update(JobSegment)
        .where(
            JobSegment.job_id == job_id,
            JobSegment.index == index,
            JobSegment.status == SegmentStatus.running.value,
            JobSegment.epoch == epoch,
            _epoch_guard(job_id, epoch),
        )
        .values(
            status=SegmentStatus.completed.value,
            visitors=int(partial['visitors']),
            completed=int(partial['completed']),
            buckets=buckets,
            error='',
            error_kind='',
            updated_at=now,
        )
    )
    db.commit()
    if result.rowcount != 1:
        raise StaleLease(f'分段 {index + 1} 的检查点写入被更高 epoch 的执行者取代')


def fail_segment(db, job_id, index, epoch, message, kind='runtime', now=None):
    now = _now() if now is None else now
    result = db.execute(
        update(JobSegment)
        .where(
            JobSegment.job_id == job_id,
            JobSegment.index == index,
            JobSegment.status == SegmentStatus.running.value,
            JobSegment.epoch == epoch,
            _epoch_guard(job_id, epoch),
        )
        .values(
            status=SegmentStatus.failed.value,
            error=str(message)[:990],
            error_kind=kind,
            updated_at=now,
        )
    )
    db.commit()
    return result.rowcount == 1


def partials_from_segments(db, job_id):
    rows = db.scalars(
        select(JobSegment)
        .where(JobSegment.job_id == job_id, JobSegment.status == SegmentStatus.completed.value)
        .order_by(JobSegment.index)
    ).all()
    partials = []
    for row in rows:
        buckets = {}
        for name, arm, counts in row.buckets or []:
            buckets[(str(name), str(arm))] = [int(counts[0]), int(counts[1])]
        partials.append({'visitors': int(row.visitors), 'completed': int(row.completed), 'buckets': buckets})
    return partials


def summarize(db, job_id):
    rows = db.execute(
        select(JobSegment.status, func.count()).where(JobSegment.job_id == job_id).group_by(JobSegment.status)
    ).all()
    counts = Counter({status.value: 0 for status in SegmentStatus})
    for status, number in rows:
        counts[status] += number
    total = sum(counts.values())
    return {
        'total': total,
        'completed': counts['completed'],
        'failed': counts['failed'],
        'running': counts['running'],
        'pending': counts['pending'],
        'percent': round(counts['completed'] / total * 100, 2) if total else 0.0,
    }


def finalize(db, job_id, epoch, now=None):
    """全部分段成功后一次发布；任一段失败或缺失时不写结论。"""
    now = _now() if now is None else now
    counts = summarize(db, job_id)
    job = db.get(Job, job_id)
    total = counts['total']
    if total == 0 or job.segments_total != total:
        _job_failed(db, job_id, epoch, '任务没有可执行的分段', now)
        return JobStatus.failed.value
    if counts['completed'] == total:
        try:
            result = business.merge_partials(partials_from_segments(db, job_id), job.params)
            json.dumps(result, ensure_ascii=False, allow_nan=False)
        except Exception as error:  # 合并阶段的退化样本等，同样不发布结论
            _job_failed(db, job_id, epoch, str(error), now)
            return JobStatus.failed.value
        written = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.epoch == epoch, Job.status == JobStatus.running.value)
            .values(
                status=JobStatus.completed.value,
                result=result,
                error='',
                finished_at=now,
                lease_owner='',
                lease_expires_at=None,
            )
        )
        db.commit()
        return JobStatus.completed.value if written.rowcount == 1 else None
    # 任一段失败即判定整批判定失败：不发布结论；已完成的检查点保留供重试复用。
    if counts['failed']:
        failed = db.scalars(
            select(JobSegment)
            .where(JobSegment.job_id == job_id, JobSegment.status == SegmentStatus.failed.value)
            .order_by(JobSegment.index)
        ).all()
        reason = '；'.join(
            f'第 {row.index + 1} 段（记录 {row.start_index + 1}-{row.start_index + row.records_count}）：{row.error}'
            for row in failed
        )[:990]
        _job_failed(db, job_id, epoch, reason, now)
        return JobStatus.failed.value
    # 仍有段被其他执行者持有：释放租约，等对方完成或租约到期后接管。
    db.execute(
        update(Job)
        .where(Job.id == job_id, Job.epoch == epoch)
        .values(lease_owner='', lease_expires_at=None)
    )
    db.commit()
    return None


def retry_segments(db, job, indexes=None, kinds=None):
    """把失败段重置为待处理；indexes 为空表示全部失败段。返回重置数量。

    先用条件更新把任务从 failed 原子地转为 queued，避免两个操作者同时重试。
    """
    transitioned = db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == JobStatus.failed.value)
        .values(
            status=JobStatus.queued.value,
            error='',
            result={},
            finished_at=None,
            lease_owner='',
            lease_expires_at=None,
        )
    )
    if transitioned.rowcount != 1:
        db.rollback()
        return 0
    query = select(JobSegment).where(
        JobSegment.job_id == job.id, JobSegment.status == SegmentStatus.failed.value
    )
    if indexes is not None:
        query = query.where(JobSegment.index.in_(list(indexes)))
    if kinds is not None:
        query = query.where(JobSegment.error_kind.in_(list(kinds)))
    rows = db.scalars(query).all()
    for row in rows:
        row.status = SegmentStatus.pending.value
        row.error = ''
        row.error_kind = ''
        row.updated_at = _now()
    db.flush()
    if not rows:
        db.rollback()
        return 0
    return len(rows)


def eligible_auto_retry(db, job_id):
    """仅运行时失败且尝试次数未用尽的段会自动重试；数据错误需修复输入或人工重试。"""
    rows = db.scalars(
        select(JobSegment).where(
            JobSegment.job_id == job_id, JobSegment.status == SegmentStatus.failed.value
        )
    ).all()
    if not rows:
        return []
    if any(row.error_kind != 'runtime' or row.attempts >= SEGMENT_MAX_ATTEMPTS for row in rows):
        return []
    return [row.index for row in rows]


def segments_view(db, job_id):
    rows = db.scalars(select(JobSegment).where(JobSegment.job_id == job_id).order_by(JobSegment.index)).all()
    return [
        {
            'index': row.index,
            'label': f'第 {row.index + 1} 段',
            'start_index': row.start_index,
            'end_index': row.start_index + row.records_count,
            'records': row.records_count,
            'status': row.status,
            'visitors': row.visitors,
            'completed': row.completed,
            'error': row.error,
            'error_kind': row.error_kind,
            'attempts': row.attempts,
            'updated_at': row.updated_at,
        }
        for row in rows
    ]


def checkpoint_all(db, job, dataset):
    """用于离线补算的基线任务：一次性写好全部检查点，不经过租约。"""
    ensure_segments(db, job, dataset)
    for row in db.scalars(select(JobSegment).where(JobSegment.job_id == job.id).order_by(JobSegment.index)).all():
        if row.status == SegmentStatus.completed.value:
            continue
        partial = business.accumulate(dataset.records[row.start_index: row.start_index + row.records_count])
        row.status = SegmentStatus.completed.value
        row.visitors = int(partial['visitors'])
        row.completed = int(partial['completed'])
        row.buckets = sorted([[name, arm, list(counts)] for (name, arm), counts in partial['buckets'].items()])
        row.updated_at = _now()
    job.segments_total = db.scalar(select(func.count()).select_from(JobSegment).where(JobSegment.job_id == job.id))
    job.segments_reused = 0
    db.flush()


def run_job(job_id, owner, process=None, now=None):
    """执行或续跑一个任务。可在多个进程/线程中安全并发调用。"""
    db = Session()
    try:
        epoch = claim_job(db, job_id, owner, now)
        if epoch is None:
            return None
        job = db.get(Job, job_id)
        dataset = db.get(Dataset, job.dataset_id)
        if dataset is None:
            finalize_failed = _job_failed(db, job_id, epoch, '数据批次不存在', now)
            return 'failed' if finalize_failed else None
        ensure_segments(db, job, dataset)
        if not bounds_valid(db, job, dataset):
            return 'failed' if _job_failed(db, job_id, epoch, '分段边界与数据批次不一致，请重新创建任务', now) else None
        relabel_reused(db, job_id, epoch)
        processed = set()
        while True:
            rows = db.scalars(
                select(JobSegment)
                .where(JobSegment.job_id == job_id, JobSegment.status != SegmentStatus.completed.value)
                .order_by(JobSegment.index)
            ).all()
            pending = [row for row in rows if row.index not in processed]
            if not pending:
                break
            row = pending[0]
            if not renew(db, job_id, owner, epoch, now):
                log.info('job %s 的租约已被接管，停止写入', job_id)
                return 'stale'
            if not claim_segment(db, job_id, row.index, epoch, now):
                processed.add(row.index)  # 已完成或被更高 epoch 抢先
                continue
            records = dataset.records[row.start_index: row.start_index + row.records_count]
            try:
                partial = (process or business.accumulate)(records)
            except ValueError as error:
                fail_segment(db, job_id, row.index, epoch, error, kind='data', now=now)
                processed.add(row.index)
                continue
            except Exception as error:  # noqa: BLE001 - 单段异常不拖垮整批，留待重试
                log.exception('job %s 分段 %s 执行失败', job_id, row.index)
                fail_segment(db, job_id, row.index, epoch, error, kind='runtime', now=now)
                processed.add(row.index)
                continue
            try:
                complete_segment(db, job_id, row.index, epoch, partial, now=now)
            except StaleLease:
                return 'stale'
            processed.add(row.index)
        return finalize(db, job_id, epoch, now)
    except StaleLease:
        log.info('job %s 失去接管资格，放弃本次结果', job_id)
        return 'stale'
    except Exception:
        log.exception('job %s 执行中断', job_id)
        return None
    finally:
        db.close()
