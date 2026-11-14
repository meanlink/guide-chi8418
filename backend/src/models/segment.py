from sqlalchemy import String, JSON, Double, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
class JobSegment(Base):
    """一段不可变输入的核验检查点。

    已完成段只写入一次；重跑/接管时直接复用该行，不再重复汇入计数。
    start_index/records_count 固化分段边界，避免分段大小配置变化后错位。
    epoch 记录写入该检查点时任务的接管代数，用于拦截失去资格的旧进程。
    """
    __tablename__ = 'job_segments'
    __table_args__ = (UniqueConstraint('job_id', 'index', name='uq_job_segment'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    index: Mapped[int] = mapped_column(Integer)
    start_index: Mapped[int] = mapped_column(Integer, default=0)
    records_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    visitors: Mapped[int] = mapped_column(Integer, default=0)
    completed: Mapped[int] = mapped_column(Integer, default=0)
    buckets: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(String(1000), default='')
    error_kind: Mapped[str] = mapped_column(String(20), default='')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[float] = mapped_column(Double, default=0.0)
