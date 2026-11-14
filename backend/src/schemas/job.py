from pydantic import BaseModel, Field
class JobInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    dataset_id: int
    params: dict = Field(default_factory=dict)
class RetryInput(BaseModel):
    # 分段序号从 0 开始；缺省表示全部失败段。
    segments: list[int] | None = Field(default=None)
