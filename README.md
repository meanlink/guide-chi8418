# 场馆导览转化核验

分类独立性检验的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker，按记录分段保存检查点）→ 结果复核（指标、图形、明细、解释、分段进度、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18204；后端文档 http://localhost:19204/docs；数据库宿主端口 17704。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

## 分段核验与断点恢复
大批量核验按 `SEGMENT_SIZE`（默认 40 条记录）切段，段边界在任务创建时固化。每段完成后立即写入 `job_segments` 检查点；worker 停机或加开进程后，恢复时只处理未完成段，已完成段原样复用，计数不会重复汇入。

- 失败隔离：坏记录只让所在段失败，任务立即进入失败态且不写结论；修好输入后可 `POST /jobs/{id}/retry`（可传 `segments` 只重试指定段）或在结果页点“重试失败段 ↗”。
- 并发接管：任务带租约（`SEGMENT_LEASE_SECONDS`）和单调递增的 `epoch`。抢到租约的进程负责续租；失去资格的旧进程即使晚回结果，写检查点与发布结论都会因 epoch 不匹配被拒绝。`docker compose up -d --scale worker=2` 可加开执行进程。
- 发布门禁：只有全部分段成功、且获批准的任务才能创建分发计划或生成归档；任一段未成功时结果页只显示分段进度、失败原因与恢复进度。
- 校验：分段合并结果与整批一次算完完全一致（列联表、χ²、p、Cramer V）；`SEGMENT_MAX_ATTEMPTS` 控制运行时异常的自动重试上限，数据校验错误不自动重试。

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；PostgreSQL 16；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=guide-chi；FRONTEND_PORT=18204；BACKEND_PORT=19204；DB_PORT=17704；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1005/，源码不含日志/截图/测试报告。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
