from sqlalchemy import String, JSON, Double, ForeignKey, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    # 分段续算与多进程接管控制。epoch 是写入栅栏，每次接管递增。
    segments_total: Mapped[int] = mapped_column(Integer, default=0)
    segments_reused: Mapped[int] = mapped_column(Integer, default=0)
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str] = mapped_column(String(120), default='')
    lease_expires_at: Mapped[float | None] = mapped_column(Double, nullable=True)
