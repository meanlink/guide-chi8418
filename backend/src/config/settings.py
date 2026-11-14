import os
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./local.db')
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
JWT_SECRET = os.environ.get('JWT_SECRET', 'local-development-change-before-deploy')
ARTIFACT_DIR = os.getenv('ARTIFACT_DIR', '/artifacts')
# 每个分段处理的记录条数；分段边界在任务首次创建时固化到 job_segments。
SEGMENT_SIZE = max(1, int(os.getenv('SEGMENT_SIZE', '40')))
# 分段租约时长（秒）。处理进程需在租约内完成一段，否则视为失去接管资格。
SEGMENT_LEASE_SECONDS = max(1.0, float(os.getenv('SEGMENT_LEASE_SECONDS', '120')))
# 单段最多自动尝试次数（含首次）；用尽后保持失败，需人工重试。
SEGMENT_MAX_ATTEMPTS = max(1, int(os.getenv('SEGMENT_MAX_ATTEMPTS', '3')))
