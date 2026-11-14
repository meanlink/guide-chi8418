from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import Job
from src.constants.enums import JobStatus
from src.schemas.job import JobInput, RetryInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import jobs, segments
from src.services.audit import record
from src.utils.serialization import job_dict
router=APIRouter(prefix='/jobs')


def _detail(db, j):
    return job_dict(j, progress=segments.summarize(db, j.id), segments=segments.segments_view(db, j.id))


@router.get('')
def listing(db=Depends(get_db),u=Depends(current_user)):
    return [job_dict(j, progress=segments.summarize(db, j.id)) for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(100))]


@router.post('',status_code=201)
def create(data:JobInput,db=Depends(get_db),u=Depends(can_write)):
    j=jobs.create(db,data,u); record(db,u.email,'job.create',{'id':j.id,'params':j.params,'segments':j.segments_total})
    db.commit(); jobs.enqueue(j.id); return _detail(db, j)


@router.get('/{id}')
def detail(id:int,db=Depends(get_db),u=Depends(current_user)):
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    return _detail(db, j)


@router.post('/{id}/retry')
def retry(id:int, data:RetryInput | None = None, db=Depends(get_db), u=Depends(can_write)):
    """只重置失败段并重新排队；已完成段原样沿用，不会重复汇入计数。"""
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    if j.status!=JobStatus.failed.value: raise HTTPException(409,'只有失败的任务可以重试')
    indexes=data.segments if data and data.segments is not None else None
    reset=segments.retry_segments(db,j,indexes)
    if not reset: raise HTTPException(409,'没有可重试的失败段')
    record(db,u.email,'job.retry',{'id':id,'segments':[i+1 for i in (indexes or [])],'reset':reset})
    db.commit(); jobs.enqueue(j.id)
    return _detail(db, j)


@router.post('/{id}/approve')
def approve(id:int,db=Depends(get_db),u=Depends(admin_only)):
    j=db.get(Job,id)
    if not j: raise HTTPException(404,'分析任务不存在')
    if j.status!=JobStatus.completed.value:raise HTTPException(409,'仅已完成结果可以批准')
    progress=segments.summarize(db,j.id)
    if progress['total']==0 or progress['completed']!=progress['total']:
        raise HTTPException(409,'仍有分段未成功，不能批准或发布该结论')
    j.approved=True; record(db,u.email,'job.approve',{'id':id,'segments':progress['total']}); db.commit()
    return _detail(db, j)
