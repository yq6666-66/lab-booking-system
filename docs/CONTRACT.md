# 实施契约

目标：C11 + CivetWeb + SQLite + cJSON + libsodium，浏览器静态页面。固定1小时实验室场次，FIFO候补，事务、持久化请求去重、故障验证。仅回环地址。本阶段实际实现与测试，不修改原Word文档。

## HTTP
所有响应 `{code,message,data}`，成功 code=`OK`。ID十进制字符串，时间为UTC epoch秒，展示北京时间。Session cookie `lab_session`，POST（login除外）必须 `X-CSRF-Token`。允许无Origin的命令行客户端，浏览器Origin必须等于当前回环服务origin。JSON请求体上限16384字节。

- POST /api/login `{username,password}` -> data `{user:{id,username,role},csrf_token}`，设置cookie。
- GET /api/me -> 同上。POST /api/logout -> OK。
- GET /api/health -> data `{status:"ok"}`。
- GET /api/labs -> data `{labs:[{id,name,location,description,enabled}]}`。
- GET /api/slots?lab_id=1&date=YYYY-MM-DD -> data `{slots:[{id,lab_id,start_at,end_at,enabled,lab_enabled,occupied,waiting_count,my_reservation_id,my_waitlist_id}]}`。后两项可null。
- GET /api/me/records -> data `{reservations:[{id,slot_id,lab_name,start_at,end_at,status,source}],waitlist:[{id,slot_id,lab_name,start_at,end_at,status,position}],events:[]}`。
- GET /api/admin/records?date=YYYY-MM-DD -> 同上但所有用户，记录附username，events含actor/action/entity_id/created_at/request_id。
- POST /api/reservations `{slot_id,request_id}` -> data `{reservation_id}`。
- POST /api/reservations/{id}/cancel `{request_id}` -> data `{reservation_id,promoted_reservation_id}`（无补位null）。
- POST /api/waitlist `{slot_id,request_id}` -> data `{waitlist_id}`。
- POST /api/waitlist/{id}/withdraw `{request_id}` -> data `{waitlist_id}`。
- POST /api/admin/labs `{name,location,description}` -> data `{lab_id}`。
- POST /api/admin/labs/{id}/update `{name,location,description,enabled:bool}`。
- POST /api/admin/slots/publish `{lab_id,start_date,end_date}`（最多14天）-> data `{created}`。08-12/14-18各一小时，不创建已开始场次，重复发布不重复。

错误：400 INVALID_INPUT；401 UNAUTHORIZED；403 FORBIDDEN/CSRF；404 NOT_FOUND；409 SLOT_FULL/SLOT_AVAILABLE/STATE_CONFLICT/REQUEST_ID_CONFLICT/ALREADY_RESERVED；413 TOO_LARGE；503 DATABASE_BUSY；500 INTERNAL_ERROR。遇网络错误/503沿用原UUID重试，终态结果后新操作新UUID。日期严格校验。

## 数据与事务
users(id,username,password_hash,role,enabled)，sessions(token_hash,user_id,csrf_token,expires_at)，labs(id,name,location,description,enabled)，slots(id,lab_id,start_at,end_at,enabled)，reservations(id,user_id,slot_id,status,source,created_at,cancelled_at)，waitlist(id,user_id,slot_id,status,created_at,promoted_reservation_id)，request_receipts(user_id,request_id,action,payload_digest,http_status,result_json,created_at)，operation_events(id,actor_id,action,entity_id,request_id,created_at)。
CONFIRMED/CANCELLED；WAITING/WITHDRAWN/PROMOTED/SKIPPED。SQLite局部唯一索引：每slot一个CONFIRMED、每(user,slot)一个WAITING。所有业务写在BEGIN IMMEDIATE中，每请求独立连接，WAL/FULL/foreign_keys=ON/busy_timeout=3000。
查去重结果早于新操作时间校验，相同key参数返回原结果，不同内容409；业务结果、状态变更和事件同事务提交，500/503不保存永久结果。取消具体预约ID，补位FIFO按递增waitlist.id。无效账号跳过。实验室停用阻止新预约/新候补但允许原记录取消与补位。场次开始后禁止新操作，历史重试仍返回原结果。

## CLI 和故障测试
`build/lab-booking.exe --db <file> --web <dir> --port 8080`，默认data/lab.db和web。`--seed --init-only`初始化3实验室+14天场次+admin/user01...user20，从环境LAB_SEED_PASSWORD读取密码（至少8位），不输出密码。--check仅检查数据库。
测试版 build/lab-booking-test.exe额外支持`--fault cancel-before-promote|after-commit --fault-request <UUID>`，仅匹配该request_id时终止进程（exit 86/87），通过编译宏TEST_FAULTS启用，正常版拒绝这些参数。取消事务未提交的故障86；提交后响应前87。测试独立数据库/子进程，无故障HTTP入口。

## 分工
主Agent：src、依赖、构建、集成。页面Agent仅修改web。测试Agent仅修改tests。任何修改已有文件先在本任务work/backups留备份；不要修改其他Agent拥有的文件。
