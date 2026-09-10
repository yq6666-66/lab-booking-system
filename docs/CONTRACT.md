# 实施契约

目标：C11 + CivetWeb + SQLite + cJSON + libsodium，浏览器静态页面。固定1小时实验室场次，FIFO候补，事务、持久化请求去重、故障验证。仅回环地址。本阶段实际实现与测试，不修改原Word文档。

## HTTP
所有响应 `{code,message,data}`，成功 code=`OK`。ID十进制字符串，时间为UTC epoch秒，展示北京时间。Session cookie `lab_session`，POST（login除外）必须 `X-CSRF-Token`。允许无Origin的命令行客户端，浏览器Origin必须等于当前回环服务origin。JSON请求体上限16384字节。

- POST /api/login `{username,password}` -> data `{user:{id,username,role},csrf_token}`，设置cookie。
- GET /api/me -> 同上。POST /api/logout -> OK。
- GET /api/health -> data `{status:"ok"}`。
- GET /api/labs -> data `{labs:[{id,name,location,description,enabled}]}`。
- GET /api/slots?lab_id=1&date=YYYY-MM-DD -> data `{slots:[{id,lab_id,start_at,end_at,enabled,lab_enabled,occupied,waiting_count,my_reservation_id,my_checked_in_at,my_waitlist_id}],checkin_window}`。my_reservation_id/my_checked_in_at/my_waitlist_id 可为 null；checkin_window 为签到窗口秒数，页面据此判断何时显示签到按钮。
- GET /api/me/records?page=&page_size= -> data `{reservations:[{id,slot_id,lab_name,start_at,end_at,status,source,cancel_reason,checked_in_at}],waitlist:[{id,slot_id,lab_name,start_at,end_at,status,position}],events:[],page,page_size,has_more}`。page_size 取值 1..200（默认 20），has_more 表示是否还有下一页。
- GET /api/admin/records?date=YYYY-MM-DD&page=&page_size= -> 同上但所有用户，记录附username，events含actor/action/entity_id/created_at/request_id。
- GET /api/admin/stats?start_date=&end_date=（最多31个日期）-> data `{stats:[{date,slots,confirmed,cancelled,no_show,checked_in,waiting}],totals:{slots,confirmed,cancelled,no_show,checked_in,waiting}}`。按北京日聚合；仅返回有场次的日期。slots为开放场次数；confirmed为该日有效预约数；cancelled为其中用户主动取消数；no_show为签到超时释放数；checked_in为已签到数；waiting为有效候补人数。
- GET /api/admin/stats/export?start_date=&end_date= -> data `{filename,content}`（最多31天）。content 为带 BOM 的 CSV 文本（日期/开放场次/有效预约/已取消/已爽约/已签到/候补人数，末行为合计），由页面下载为 .csv 文件。
- POST /api/reservations `{slot_id,request_id}` -> data `{reservation_id}`。
- POST /api/reservations/{id}/cancel `{request_id}` -> data `{reservation_id,promoted_reservation_id}`（无补位null）。
- POST /api/waitlist `{slot_id,request_id}` -> data `{waitlist_id}`。
- POST /api/waitlist/{id}/withdraw `{request_id}` -> data `{waitlist_id}`。
- POST /api/reservations/{id}/checkin `{request_id}` -> data `{reservation_id,checked_in_at}`。仅限本人 CONFIRMED 预约，且须在场次开始后、签到窗口内。重复签到与同编号重放均返回首次签到时间；窗口过后返回 409 STATE_CONFLICT（不可补签）。
- GET /api/me/notifications?unread=1&page=&page_size= -> data `{notifications:[{id,kind,title,body,slot_id,reservation_id,read_at,created_at}],unread_count,page,page_size,has_more}`。kind 为 PROMOTED 或 NO_SHOW。
- POST /api/me/notifications/read `{ids?:[...],all?:true,request_id}` -> data `{updated}`。二选一：ids（单次最多 50 条）或 all=true 表示全部已读。
- GET /api/me/sessions -> data `{sessions:[{id,created_at,expires_at,current}]}`。id 为该会话令牌的摘要，不可反推令牌；current 标记发起请求的会话。
- POST /api/me/sessions/{id}/revoke `{request_id}` -> OK。可下线自己的任意会话（含当前会话，等价于登出）；id 须为 64 位十六进制令牌摘要。
- POST /api/me/password `{old_password,new_password,request_id}` -> data `{revoked_sessions}`。新密码 8..128 位且不得与原密码相同；校验旧密码；成功后当前会话保留、该用户其他会话立即失效。
- POST /api/admin/labs `{name,location,description}` -> data `{lab_id}`。
- POST /api/admin/labs/{id}/update `{name,location,description,enabled:bool}`。
- POST /api/admin/slots/publish `{lab_id,start_date,end_date}`（最多14天）-> data `{created}`。08-12/14-18各一小时，不创建已开始场次，重复发布不重复。

错误：400 INVALID_INPUT；401 UNAUTHORIZED；403 FORBIDDEN/CSRF；404 NOT_FOUND；409 SLOT_FULL/SLOT_AVAILABLE/STATE_CONFLICT/REQUEST_ID_CONFLICT/ALREADY_RESERVED；413 TOO_LARGE；503 DATABASE_BUSY；500 INTERNAL_ERROR。遇网络错误/503沿用原UUID重试，终态结果后新操作新UUID。日期严格校验。

预约返回409 SLOT_FULL时（已满或名额刚被候补取得），data附带`{alternatives:[{id,start_at,end_at}]}`：同实验室、所选场次之后7天内、按开始时间升序的空闲场次，最多3个。该列表与失败结果一同存入请求回执，同编号重放返回原列表（可能已过期，前端点击时以新请求编号重新校验）。

签到与爽约：场次开始后进入签到窗口（`--checkin-window`，默认900秒），仅本人可在窗口内对有效预约签到。补位产生的预约（source=WAITLIST）以其补位时刻作为签到起点，避免刚补位即被判定超时。窗口结束仍未签到的预约由服务端扫描线程（`--sweep-interval`，默认30秒）在同一事务内标记为 CANCELLED 且 cancel_reason='NO_SHOW'，向原预约人写入 NO_SHOW 通知；若该场次尚未结束，同时按 FIFO 补位给队首有效候补并写入 PROMOTED 通知。用户主动取消记 cancel_reason='USER'。扫描线程同样以 BEGIN IMMEDIATE 短事务执行，与用户请求争用写锁时按 busy_timeout 退避。

## 数据与事务
users(id,username,password_hash,role,enabled)，sessions(token_hash,user_id,csrf_token,expires_at,created_at)，labs(id,name,location,description,enabled)，slots(id,lab_id,start_at,end_at,enabled)，reservations(id,user_id,slot_id,status,source,created_at,cancelled_at,checked_in_at,cancel_reason)，waitlist(id,user_id,slot_id,status,created_at,promoted_reservation_id)，request_receipts(user_id,request_id,action,payload_digest,http_status,result_json,created_at)，operation_events(id,actor_id,action,entity_id,request_id,created_at)，notifications(id,user_id,kind,title,body,slot_id,reservation_id,read_at,created_at)。
CONFIRMED/CANCELLED（cancel_reason 取 USER 或 NO_SHOW，未取消时为 NULL；checked_in_at 仅 CONFIRMED 时可非空）；WAITING/WITHDRAWN/PROMOTED/SKIPPED。SQLite局部唯一索引：每slot一个CONFIRMED、每(user,slot)一个WAITING。所有业务写在BEGIN IMMEDIATE中，每请求独立连接，WAL/FULL/foreign_keys=ON/busy_timeout=3000。schema 版本 user_version=2：启动时按列是否存在做幂等迁移，只追加列、建通知表并回填历史取消原因，不重建表，迁移在单个事务内完成。
查去重结果早于新操作时间校验，相同key参数返回原结果，不同内容409；业务结果、状态变更和事件同事务提交，500/503不保存永久结果。取消具体预约ID，补位FIFO按递增waitlist.id。无效账号跳过。实验室停用阻止新预约/新候补但允许原记录取消与补位。场次开始后禁止新操作，历史重试仍返回原结果。会话有效期7200秒，登录时与服务启动时清理已过期会话行。签到是唯一允许在场次开始后提交的写操作；改密、会话下线与通知标记已读为幂等操作，重复提交安全，不写入请求回执。统计导出不修改任何数据。

## CLI 和故障测试
`build/lab-booking.exe --db <file> --web <dir> --port 8080 [--checkin-window SEC] [--sweep-interval SEC]`，默认data/lab.db和web。`--checkin-window` 为签到窗口秒数（1..86400，默认900），`--sweep-interval` 为爽约扫描间隔秒数（1..3600，默认30，数值越小释放越及时，测试用 1 秒以获得确定性）。`--seed --init-only`初始化3实验室+14天场次+admin/user01...user20，从环境LAB_SEED_PASSWORD读取密码（至少8位），不输出密码。--check仅检查数据库（含新增列、通知表与取消原因不变式）。
测试版 build/lab-booking-test.exe额外支持`--fault cancel-before-promote|after-commit --fault-request <UUID>`，仅匹配该request_id时终止进程（exit 86/87），通过编译宏TEST_FAULTS启用，正常版拒绝这些参数。取消事务未提交的故障86；提交后响应前87。测试独立数据库/子进程，无故障HTTP入口。

## 运行指标
- GET /api/admin/metrics -> data `{counters:{requests_total,ok_2xx,err_4xx,err_5xx,db_busy_503,logins},latency_ms:{count,sum,max,buckets}}`。仅管理员；非管理员 403、匿名 401。
- 计数口径：请求在**响应写出之后**统一计数，因此 requests_total 为"已写完响应的累计请求数"；快照读取发生在本次响应写出之前，故返回值不含本次 `/api/admin/metrics` 请求自身，且恒有 requests_total = ok_2xx + err_4xx + err_5xx（本服务不返回 3xx）。ok_2xx/err_4xx/err_5xx 按 HTTP 状态分类；db_busy_503 为数据库忙碌导致的 503 次数（已含于 err_5xx，不重复累加）；logins 在 /api/login 成功路径单独计数。
- 延迟直方图桶边界（毫秒）：1/2/5/10/20/50/100/200/500/1000/2000 及 >2000 溢出桶，共 12 桶；latency_ms.sum 与 max 单位为毫秒。
- 计数器为进程内存态（无锁原子计数），服务重启清零，不持久化。

## 分工
主Agent：src、依赖、构建、集成。页面Agent仅修改web。测试Agent仅修改tests。任何修改已有文件先在本任务work/backups留备份；不要修改其他Agent拥有的文件。

## 限流与防爆破（r5/security）
登录按用户名防爆破：连续失败达 `--login-max-fails`（默认 5）次后锁定 `--login-lockout` 秒（默认 900），期间正确密码同样返回 429 `LOGIN_LOCKED`；成功登录清零计数；失败事件仅对已存在用户写入 operation_events（action=LOGIN_FAILED，不泄露用户名是否存在）。已登录用户的全部 POST 写操作经每用户令牌桶限速：`--rate-burst`（默认 30）个突发、每 `--rate-refill-sec`（默认 1）秒补充 1 个，超限返回 429 `RATE_LIMITED`。限流状态为进程内存态，重启清零；服务仅回环部署，不区分来源 IP。

## 容量制（r5/capacity，schema v3）
slots 增加 capacity 列（1..200，默认 1），schema user_version=3；v2 库启动时幂等迁移：追加列、DROP INDEX one_booking（单占用唯一索引退役，容量约束改由 BEGIN IMMEDIATE 单写者事务内校验保证）。种子数据容量保持 1。
预约：事务内校验 `CONFIRMED 数 < capacity` 才可预约；同一用户同场次仅一条有效预约（409 ALREADY_RESERVED 优先于满员判定）。候补补位改为 promote_fill 循环：取消/爽约释放后连续按 FIFO 补位直至满员或队列空；取消响应 promoted_reservation_id 报告首个补位。候补守卫：尚有余位时返回 409 SLOT_AVAILABLE 引导直约；满员才可入队。替代时段查询按 `CONFIRMED 数 < capacity` 判空闲。
`GET /api/slots` 每项新增 `capacity` 与 `confirmed_count`（移除 occupied，前端以 confirmed_count>=capacity 判满）。`POST /api/admin/slots/publish` 增加可选 `capacity`（数字或字符串，1..200，默认 1；已存在场次不受影响）。统计口径不变。
