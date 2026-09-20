# 实施契约

目标：C11 + CivetWeb + SQLite + cJSON + libsodium，浏览器静态页面。固定1小时实验室场次，FIFO候补，事务、持久化请求去重、故障验证。仅回环地址。本阶段实际实现与测试，不修改原Word文档。

## HTTP
所有响应 `{code,message,data}`，成功 code=`OK`。ID十进制字符串，时间为UTC epoch秒，展示北京时间。Session cookie `lab_session`，POST（login除外）必须 `X-CSRF-Token`。允许无Origin的命令行客户端，浏览器Origin必须等于当前回环服务origin。JSON请求体上限16384字节。

- POST /api/login `{username,password,role_hint?}` -> data `{user:{id,username,role},csrf_token}`，设置cookie。role_hint 为可选的登录入口声明：仅接受 `"ADMIN"` 或 `"USER"`，其他值返回 400 INVALID_INPUT；缺省视为 `"USER"`，不携带该字段的旧客户端行为不变。口令校验通过后才比对入口——role_hint="ADMIN" 而账号实际为 USER 时返回 403 ROLE_MISMATCH（消息"该账号不是管理员，请使用用户入口登录"），不建立会话、不计入登录失败计数（这不是口令错误）；role_hint="USER" 时管理员账号可正常登录（管理员也可用用户入口）。
- 管理控制台：`/admin.html`（独立页面，静态文件）。页面加载时经 GET /api/me 校验会话与角色，非管理员跳回 `/`；全部管理操作仍走上列 /api/admin/* 端点，服务端 403 鉴权不变。登录页提供「用户登录 / 管理员登录」双入口，管理员入口提交 role_hint="ADMIN"，成功后跳转控制台。
- GET /api/me -> 同上。POST /api/logout -> OK。
- GET /api/health -> data `{status:"ok",version,uptime_s}`。
- GET /api/labs -> data `{labs:[{id,name,location,description,enabled,require_approval}]}`（r43 补齐：审批开关对用户端/管理台列表可见，布尔输出）。
- GET /api/slots?lab_id=1&date=YYYY-MM-DD -> data `{slots:[{id,lab_id,start_at,end_at,enabled,lab_enabled,require_approval,capacity,confirmed_count,waiting_count,my_reservation_id,my_checked_in_at,my_waitlist_id}],checkin_window}`（r43 补齐 require_approval 布尔，提交前可说明该场次需审批）。my_reservation_id/my_checked_in_at/my_waitlist_id 可为 null；checkin_window 为签到窗口秒数，页面据此判断何时显示签到按钮。
- GET /api/me/records?page=&page_size= -> data `{reservations:[{id,slot_id,lab_id,lab_name,username,start_at,end_at,status,source,cancel_reason,checked_in_at,note}],waitlist:[{id,slot_id,lab_name,username,start_at,end_at,status,position}],events:[],page,page_size,has_more}`。page_size 取值 1..200（默认 20），has_more 表示是否还有下一页。
- GET /api/admin/records?date=YYYY-MM-DD&page=&page_size= -> 同上但所有用户；records 与 waitlist 均附 username，events 含 id/actor/action/entity_id/created_at/request_id。支持可选 `status` 参数（CONFIRMED/CANCELLED/NO_SHOW/PENDING，其他值 400）过滤预约列表，不传 `date` 时不按日期过滤（用于跨天场景，如待审批列表）。
- GET /api/admin/stats?start_date=&end_date=（最多31个日期）-> data `{stats:[{date,slots,confirmed,cancelled,no_show,checked_in,waiting}],totals:{slots,confirmed,cancelled,no_show,checked_in,waiting}}`。按北京日聚合；仅返回有场次的日期。slots为开放场次数；confirmed为该日有效预约数；cancelled为其中用户主动取消数；no_show为签到超时释放数；checked_in为已签到数；waiting为有效候补人数。
- GET /api/admin/stats/export?start_date=&end_date= -> data `{filename,content}`（最多31天）。content 为带 BOM 的 CSV 文本（日期/开放场次/有效预约/已取消/已爽约/已签到/候补人数，末行为合计），由页面下载为 .csv 文件。
- POST /api/reservations `{slot_id,request_id}` -> data `{reservation_id,status}`（r43 补齐：status 为落库状态 PENDING/CONFIRMED，页面据此区分待审批与已确认；同编号重放按回执原样返回）。
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
- POST /api/admin/slots/{id}/update `{capacity?:number|string,enabled?:bool}` -> data `{slot_id,capacity,enabled}`。capacity 与 enabled 至少提供一个，都缺返回 400；capacity 取值 1..200 否则 400；capacity 小于该场次当前 CONFIRMED 数返回 409 STATE_CONFLICT（容量不能小于已确认预约数）；场次不存在返回 404；普通用户 403。停用 enabled=false 后用户端 GET /api/slots 该场次 enabled=false，新预约/新候补返回 409 STATE_CONFLICT（场次未开放）；容量校验与更新同处一个立即事务，避免与预约并发竞争。
- POST /api/admin/notifications `{all?:true,username?,title,body?}` -> data `{sent}`。all 与 username 必须二选一，都缺或都有返回 400；title 必填且 1..120 字符，body 最多 500 字符，否则 400。all=true 发送给全部 enabled 用户，username 定向发送给单个 enabled 用户（不存在或已停用返回 404「用户不存在」）。通知 kind='NOTICE'、slot_id/reservation_id 为空，出现在用户 GET /api/me/notifications（unread=1 过滤，unread_count 计入），可经 POST /api/me/notifications/read 标记已读；普通用户调用 403。

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

- GET /api/admin/logs?lines=&level= -> data `{lines:[...],file_size,truncated}`。仅管理员；读取 data/logs/app.log 尾部（lines 1..500 默认 100，level 1/2/3 按行前缀过滤，缺省全部）。
- 业务规则（r12）：① 时段重叠——同一用户不能预约/候补两个时间重叠的场次（409 TIME_CONFLICT）；取消或爽约释放后自动解除。② 爽约信用——近 7 天爽约（NO_SHOW）达 2 次后禁止新的预约与候补（409 PENALTY_ACTIVE），限制期至第 2 次爽约场次时间 +7 天；GET /api/admin/users 每行附 no_show_count。③ 历史归档——扫描线程周期清理结束超 30 天的候补记录与请求回执（预约记录保留供统计）。

## 资源管理与利用率（r13）
- GET /api/labs/{id}/assets -> data `{assets:[{id,name,spec,total,status}]}`。登录即可调用（用户端预约页展示）；不含 status=DISABLED 的资源。
- POST /api/admin/labs/{id}/assets `{name,spec?,total?,status?}` -> data `{asset_id}`。仅管理员；total 1..999；status ∈ AVAILABLE/MAINTENANCE/DISABLED（缺省 AVAILABLE）；同一实验室内资源名唯一（409 STATE_CONFLICT「该实验室已有同名资源」）；实验室不存在 404；审计 ASSET_CREATE。
- POST /api/admin/assets/{id}/update `{name,spec?,total?,status?}` -> data `{asset_id}`。仅管理员；语义同上；资源不存在 404；审计 ASSET_UPDATE。
- GET /api/admin/labs/utilization?start_date=&end_date= -> data `{utilization:[{lab_id,lab_name,slots,seats,confirmed,checked_in,no_show,utilization,actual_minutes,seat_minutes,utilization_actual}]}`。仅管理员；区间 ≤31 天；utilization=confirmed÷seats×100（保留 1 位小数，seats=0 时为 0）；actual_minutes 为实机时合计（分钟），seat_minutes 为席位分钟合计，utilization_actual=actual_minutes÷seat_minutes×100（同上口径）。
- GET /api/admin/asset-claims?start_date=&end_date= -> data `{claims:[{asset_id,asset_name,claims,last_start}]}`。仅管理员；区间 ≤31 天；claims 为该资源在区间内的有效声明次数，last_start 为最近一次声明所在场次的开始时间（无声明为 0）。
- GET /api/admin/asset-claims/export?start_date=&end_date= -> data `{filename,content}`。声明占用 CSV（带 BOM，列为资源编号/资源名称/声明次数/最近占用日期，末行为合计），不修改任何数据。
- GET /api/admin/stats/utilization/export?start_date=&end_date= -> data `{filename,content}`。利用率 CSV（BOM，Excel 直开）。
- 数据不变量：assets(lab_id,name) 唯一；status CHECK 约束；实验室内资源随 labs 保留（停用实验室不清空清单）。
- GET /api/admin/assets/{id}/usage?start_date=&end_date= -> data `{asset:{...},usage:[{date,claims}]}`。仅管理员；区间 ≤31 天；按有效预约（CONFIRMED）逐日聚合资源声明次数。
- POST /api/admin/slots/publish 请求体可选 `weekdays`（7 位 0/1 串，bit0=周一…bit6=周日，缺省全发）：周期性发布，仅创建命中星期、未开始的场次（r16）。
- POST /api/reservations/{id}/checkout -> data `{reservation_id,checked_out_at}`（r16）。仅本人、CONFIRMED、已签到且场次未结束；重复签退返回首次时间；审计 CHECKOUT。
- GET /api/admin/assets/{id}/usage?start_date=&end_date= -> data `{asset:{...},usage:[{date,claims}]}`（r14，仅管理员）。
- GET /api/me/calendar/export -> data `{filename,content}`（r16）。全部有效预约导出 iCalendar VEVENT（UTC）。
- GET /metrics（r17，非 /api 封套）-> Prometheus 文本格式 0.0.4（`text/plain; version=0.0.4`）：请求/响应计数器、延迟直方图（累积 bucket + sum/count）。仅回环监听，供本机 Prometheus 抓取。
- CLI（r17）：`--restore FILE` 从备份文件灌回主库并自动执行 integrity_check（退出码 0/1）；`--lead-time SEC` 预约提前量（0..86400，0=关闭）。
- POST /api/reservations/{id}/reschedule `{slot_id,request_id}` -> data `{reservation_id,new_slot_id,old_slot_id,promoted_reservation_id}`（r18）。仅本人、CONFIRMED、原/新场次均未开始；限**同实验室**（资源声明语义）；事务内重校新槽开放/容量(排除自身)/重叠(排除自身)/提前量/资源声明配额（声明保留按新时段计入）；原子改期后旧槽触发 FIFO 补位；审计 RESCHEDULE；摘要含新槽（同编号不同目标仍 REQUEST_ID_CONFLICT）。
- POST /api/reservations 请求体可选 `note`（≤200 字符，r18）：预约备注，随记录返回（RES_ROW.note）。
- **可执行 FIFO 递补（r24，BR19）**：递补按 `priority DESC, id` **顺序扫描**候补队列——账号停用者置 `SKIPPED`；**临时时间冲突者暂跳且保留原序号**（不改变其 `id`/`priority`，故下次扫描仍优先于后来的申请）；只递补第一个当前可执行的候选，直到容量用尽。这正是"可执行申请间的 FIFO"：新请求不能绕过队内更早且当前可执行的候补，但不因队首暂时冲突而阻塞全队。`--waitlist-strategy=strict` 回退为"队首不可执行即阻塞"的旧语义。
- **HELD 限时保留（r24，BR20）**：需以 `--hold-window=N`（秒）启用。启用后递补写入 `status='HELD'` 与 `hold_deadline=min(now+N, 时段开始时刻)`；`POST /api/reservations/{id}/confirm {request_id}` -> `{reservation_id}` 在截止前确认后转 `CONFIRMED`（**恰好等于截止时刻视为超时**，409 HOLD_EXPIRED）；扫描器将超时 HELD 转 `EXPIRED` 并立即重新递补。HELD **占用容量**，并与 CONFIRMED 同等地构成时间冲突。默认 `--hold-window=0` 时该机制不生效，补位仍直接确认。
- **可注入时钟（r24）**：`--fake-now=<epoch 秒>` 固定服务端当前时刻（仅供测试/演示），用于确定性验证保留截止等边界。
- **候补优先级与抢占（r23，BR15）**：`waitlist` 与 `reservations` 均有 `priority`（由服务端按角色校准，管理员 10、普通用户 0，不接受客户端自报）。候补出队顺序为 `priority DESC, id ASC`（高优先级内仍是 FIFO）。管理员预约遇场次满员时，可抢占该场次内"未签到且优先级更低"的确认预约：被抢占预约置为 `status='CANCELLED', cancel_reason='PREEMPTED'`（不计爽约），其所有者获 +1 信用补偿并收到通知；被抢占者若已签到则不可被抢占。
- **信用账户（r23，BR16）**：`GET /api/me/credits?page=&page_size=` -> data `{ledger:[{id,delta,reason,reservation_id,created_at}],balance,base,page,page_size,has_more}`；`reason ∈ RESERVE/CANCEL/CHECKIN/NO_SHOW/PREEMPTED/WEEKLY/GRANT`。`POST /api/admin/users/{id}/credit {delta,request_id}` -> data `{granted}`，delta 取值 1..基准额度。余额钳制在 `0..CREDIT_BASE(5)`；签到 +1、爽约 -1、被抢占 +1、每周一自动回补至基准；**预约不消耗信用**，余额为 0 时禁止新预约与候补（409 CREDIT_EXHAUSTED）。
- **资源自身可用时段（r23，BR17）**：`GET /api/admin/assets/{id}/windows` -> data `{windows:[{id,asset_id,weekday_mask,start_minute,end_minute,reason}]}`；`POST /api/admin/assets/{id}/windows {weekday_mask?,start_minute?,end_minute?,reason?,id?,request_id}` -> data `{window_id}`（带 `id` 为删除）。`weekday_mask` 0..127（位 0=周一），`start_minute`/`end_minute` 为北京时间的当日分钟数。**未配置时段的资源视为全天可用**（向后兼容）；配置后声明该资源的场次起点必须落在匹配窗口内，否则 409 WINDOW_CONFLICT。
- **资源维护工单（r23，BR18）**：`GET /api/admin/assets/{id}/maintenance` -> data `{maintenance:[{id,asset_id,started_at,ended_at,reason,operator}]}`；`POST /api/admin/assets/{id}/maintenance {op:"open"|"close",reason?,request_id}` -> `{maintenance_id}`（open）或 `{closed}`（close）。open 时资源置为 `MAINTENANCE`，close 时恢复 `AVAILABLE`；已有未关闭工单时再次 open 返回 409。
- **跨时段连续预约（r23）**：`POST /api/reservations/batch {slot_ids:[...],request_id}` -> data `{created}`。1..8 个场次，须同实验室且时间首尾相连；同一 `BEGIN IMMEDIATE` 事务内校验容量/重复/重叠/信用，全成或全败。该路径不处理资源声明与审批（走单场次路径）。
- **日历导出（r23）**：`GET /api/me/calendar.ics` -> data `{filename,content}`，`content` 为 ICS 文本（UTC 时间、含 CONFIRMED 与 PENDING 预约）。
- API 令牌（r20，对标 Cal.com API keys）：POST /api/me/tokens `{name}` -> data `{token,name}`（64 位十六进制，明文仅此一次返回）；GET /api/me/tokens -> `{tokens:[{id,name,created_at,last_used_at}]}`；POST /api/me/tokens/{id}/revoke `{token}` -> 404/200。令牌存哈希，仅限本人自助管理，审计 TOKEN_CREATE/TOKEN_REVOKE。
- 业务规则（r17）BR14 预约提前量：开始前不足 lead_time 秒的场次停止受理预约与候补（409 LEAD_TIME），防止临开始抢占；默认 0 关闭。
- 业务规则（r16）BR13 每周预约配额：`--quota-weekly N`（0=不限）启用后，本周（北京周一 0 点起）有效预约数达 N 时新的预约返回 409 WEEKLY_QUOTA；候补不入配额（补位为 FIFO 公平结果）。
- 业务规则（r14）BR12 资源时段配额：POST /api/reservations 请求体可携带 `assets:[资源id]`（≤5 项，仅预约，候补无效）；服务在单写者事务内逐项校验——资源存在、属于该场次实验室且状态 AVAILABLE、同时段（区间相交）内声明该资源的有效预约数 < total，任一不满足返回 409 STATE_CONFLICT / ASSET_QUOTA；校验通过后资源声明（asset_claims）与预约同事务落库；预约取消/爽约后声明自动失效（统计口径仅计 CONFIRMED）；资源列表纳入请求摘要，同编号不同资源仍为 REQUEST_ID_CONFLICT。

## 用户管理与运营（r11）
- GET /api/admin/users?page=&page_size=&q? -> data `{users:[{id,username,role,enabled,reservations,waitlisted}],total,page,page_size,has_more}`。仅管理员；q 为用户名前缀过滤；reservations 为该用户有效预约（CONFIRMED）数，waitlisted 为候补中数。
- POST /api/admin/users/{id}/disable -> data `{user_id,enabled:false}`。停用账号同时删除其全部会话（立即下线）且无法登录；目标不存在 404；管理员停用自己返回 409 STATE_CONFLICT「不能停用当前登录的管理员」；审计 USER_DISABLE。
- POST /api/admin/users/{id}/enable -> data `{user_id,enabled:true}`。审计 USER_ENABLE。
- POST /api/admin/users/{id}/reset-password -> data `{user_id,password}`。生成 12 位随机口令（Argon2id 入库），明文仅本次响应返回一次、不落库明文；同时删除该用户全部会话；审计 RESET_PW。
- GET /api/admin/notifications/sent?page=&page_size= -> data `{sent:[{id,kind,title,body,username,read_at,created_at}],total,page,page_size,has_more}`。全站通知（含 PROMOTED/NO_SHOW/NOTICE/REMIND）按时间倒序，附接收人用户名与已读时间。
- 记录过滤：GET /api/me/records 支持可选 `status` 参数（CONFIRMED/CANCELLED/NO_SHOW，其他值 400）；GET /api/admin/records 支持可选 `action`（≤32）与 `user`（≤64）参数，仅过滤操作日志列表。
- 场次开始提醒：服务端扫描线程在每个扫描周期检查「开始时间在 now+remind_sec 窗口内且 reminded_at 为空」的场次，向其全部有效预约用户发送 kind='REMIND' 站内通知，随后置 reminded_at=1 防重；`--remind-sec SEC` 配置窗口（默认 1800，0 关闭）。slots 表经幂等迁移新增 reminded_at 列；notifications.kind 约束扩展为 PROMOTED/NO_SHOW/NOTICE/REMIND（旧库自动重建迁移）。
- 自动备份：`--backup-interval SEC`（默认 21600=6 小时，0 关闭）配合 `--backup DEST` 目录，服务内后台线程定时执行在线快照 `DEST/lab-backup-YYYYMMDD-HHMMSS.db` 并轮转保留最近 7 份；一次性 `--backup FILE` 语义不变。
- 安全头：CSP（default-src 'self'）、X-Content-Type-Options、X-Frame-Options、Referrer-Policy、Permissions-Policy（camera/microphone/geolocation 一律禁用）五头对全部响应下发——静态页经 CivetWeb additional_header，API 与 /metrics 响应由 handler 内共享常量下发（additional_header 不覆盖 handler 自建响应）。

## 分工
主Agent：src、依赖、构建、集成。页面Agent仅修改web。测试Agent仅修改tests。任何修改已有文件先在本任务work/backups留备份；不要修改其他Agent拥有的文件。

## 限流与防爆破（r5/security）
登录按用户名防爆破：连续失败达 `--login-max-fails`（默认 5）次后锁定 `--login-lockout` 秒（默认 900），期间正确密码同样返回 429 `LOGIN_LOCKED`；成功登录清零计数；失败事件仅对已存在用户写入 operation_events（action=LOGIN_FAILED，不泄露用户名是否存在）。已登录用户的全部 POST 写操作经每用户令牌桶限速：`--rate-burst`（默认 30）个突发、每 `--rate-refill-sec`（默认 1）秒补充 1 个，超限返回 429 `RATE_LIMITED`。限流状态为进程内存态，重启清零；服务仅回环部署，不区分来源 IP。

## 容量制（r5/capacity，schema v3）
slots 增加 capacity 列（1..200，默认 1），schema user_version=3；v2 库启动时幂等迁移：追加列、DROP INDEX one_booking（单占用唯一索引退役，容量约束改由 BEGIN IMMEDIATE 单写者事务内校验保证）。种子数据容量保持 1。
预约：事务内校验 `CONFIRMED 数 < capacity` 才可预约；同一用户同场次仅一条有效预约（409 ALREADY_RESERVED 优先于满员判定）。候补补位改为 promote_fill 循环：取消/爽约释放后连续按 FIFO 补位直至满员或队列空；取消响应 promoted_reservation_id 报告首个补位。候补守卫：尚有余位时返回 409 SLOT_AVAILABLE 引导直约；满员才可入队。替代时段查询按 `CONFIRMED 数 < capacity` 判空闲。
`GET /api/slots` 每项新增 `capacity` 与 `confirmed_count`（移除 occupied，前端以 confirmed_count>=capacity 判满）。`POST /api/admin/slots/publish` 增加可选 `capacity`（数字或字符串，1..200，默认 1；已存在场次不受影响）。统计口径不变。

## 运维与观测（r6）
- GET /api/health 附 version 与 uptime_s。
- 响应头：X-Frame-Options: DENY、Content-Security-Policy: default-src 'self'、Referrer-Policy: no-referrer。
- `--backup DEST`：SQLite Backup API 在线备份到 DEST（不阻塞业务），返回 0 表示成功。
- `--slow-ms MS`（默认 500）：单请求超过阈值记 WARN 日志。日志落 data/logs/app.log（分级 INFO/WARN/ERROR，5MB×3 轮转，含 ACCESS 行与 SWEEP 摘要）。SQLITE_INTERRUPT 映射为 503 DATABASE_BUSY。

## 自助注册（r7）
- POST /api/register `{username,password}` -> 同登录响应 `{user:{id,username,role:"USER"},csrf_token}` 并直接建立会话（注册即登录）。
- 规则：用户名 2..64 字节可见字符（禁空白/控制符）；密码 8..128 位；角色恒为 USER。重名 409 USERNAME_TAKEN；弱口令/非法用户名 400。
- 防刷：全局注册桶（突发 20、每 30 秒补 1）超限 429 RATE_LIMITED。注册成功写 REGISTER 审计事件。事件仅在登录/注册路径写入，本接口无需 CSRF（会话尚未建立）。

## 凭据生命周期（r25/P1-7）
- 用户停用（admin disable）、本人改密（POST /api/me/password）、管理员重置密码（reset-password）三者均在**同一事务内** `DELETE FROM api_tokens WHERE user_id=?`：会话与 API 访问令牌同时失效；改密响应含 `revoked_tokens` 计数。
- 令牌校验链路本身要求用户 `enabled=1`（token_auth 与 users 表 JOIN），即使令牌行残留也不可用；本节不变量进一步保证停用后无残留行（db_check 校验「已停用用户无 api_tokens」）。
- **恢复边界（运维须知）**：`--restore` 将数据库整体回到备份时点，会重新激活备份时仍存在的 API 令牌与口令——备份之后吊销的凭据会随恢复回来。应在恢复完成后立即重置相关账号口令或手动清理 `api_tokens` 表；这是整库快照备份的固有语义，记录为已知边界而非缺陷。
- 敏感信息：运行日志仅含事件与访问行，不含口令/令牌明文；`api_tokens` 仅存 SHA-256 哈希；备份文件为整库快照（含口令哈希），应与主库同等权限保护。

## 约束感知建议（r25/创新方向 1 最小版）
- GET /api/suggestions?lab_id=&date=（需登录，只读）：对该实验室自 date 起 7 天内启用的场次逐项返回 `{slot_id,start_at,end_at,capacity,taken,waiting_count,bookable,joinable,reasons:[{code,message}]}`。评估维度与原因码：已开始（STARTED）、已持有预约/候补（ALREADY_RESERVED）、信用为零（CREDIT_EXHAUSTED）、周配额将满（WEEKLY_QUOTA——仅阻塞预约，不阻塞候补，BR13）、提前量不足（LEAD_TIME）、时间重叠（TIME_CONFLICT）、满员（SLOT_FULL——bookable=false，joinable 仍可为 true 引导候补）。
- 评估与真实提交之间存在竞态，结果属**建议性质**；预约提交仍由 booking() 单写者事务内全量校验兜底（语义见「语义澄清」节）。
- 前端预约页「约束感知建议」面板消费本端点；记录接口（/api/me/records、/api/admin/records）行内新增 `hold_deadline` 字段（HELD 的确认截止，其余状态为 null），支撑状态解释 UI（HELD 倒计时确认、PENDING 提示、取消原因四分展示）。

## 候补策略三档与知情候补（r26）
- `--waitlist-strategy` 取值扩为 **strict | executable（默认）| weighted**。weighted（老化加权）按 score 降序扫描可执行候选：**score = 1000×priority + 200×(credit−5) + 60×log₂(1+等待小时数)**——等待每翻倍 +60 分；同优先级下 1 点信用差约等于 8 小时等待；管理员（10000 分）不被普通用户老化越级。老化只解决"同档饿死"，冲突暂跳语义与 executable 一致（T70）。
- **知情候补**：GET /api/suggestions 每场次新增 `queue_ahead`（本人若入队的排位，0 = 第 1 位）与 `promote_probability`（同实验室同星期过去 35 天已开场场次中"释放名额数 ≥ 排位"的经验占比；释放 = CANCELLED(USER/NO_SHOW/REJECTED)+EXPIRED；样本 <5 场为 null）。POST /api/waitlist 成功响应同附两字段（T71）。评估为建议口径，提交仍由事务全量校验兜底。
- GET /api/admin/fairness?days=28（1..90，默认 28）：按用户聚合 {joined,promoted,withdrawn,skipped,avg_wait_s,granted}，全站 `jain_index = (Σx)²/(n·Σx²)`（x=窗口内该用户获得预约数），附口径定义；非管理员 403（T72）。
- `--waitlist-daily-limit N`（0..100，默认 0 不限）：每用户每北京日候补入队（含历史入队计数）达 N 后返回 409 WAITLIST_LIMIT（T73）。

## 评估与工程取舍（r26 备查）
- 三策略 × 四档负载（ρ=请求/容量，0.7/1.0/1.3/1.6 过载递增）对照实验：tests/experiment.py --matrix，指标含候补转化率、SQL 口径等待时长（转正时刻−入队时刻）、Jain 公平指数；证据 docs/evidence/experiment/strategy_matrix.{json,md}。
- 位图冲突检测对比（tests/bitmap_bench.py，纯算法模拟口径）：48 片/天位图按位与 vs 朴素区间线扫——命中最坏场景 n=10k 时约 1845 倍；生产路径仍用 SQLite 索引区间查询，位图作为复杂度阶论证素材（docs/evidence/bitmap/）。
- **明确不做**（评估为负收益/超范围，防后续重复评估）：①事件驱动内核/最小堆时间轮——sweeper 1 秒周期对小时级业务延迟可忽略，重写引入回归风险；②匈牙利算法全局递补匹配——单场次递补场景退化为贪心，跨场次匹配与"场次独立容量"模型冲突；③分桶锁/CAS 乐观并发——SQLite 单写者事务已保证正确性；④ncurses TUI/存储抽象层——与 B/S 一体化定位不符。

## 语义澄清（r25 组合验证基准）
以下五条是规则组合语义的唯一正确答案，由集成测试 T63–T67 钉死；与既有 BR 条目的单条描述冲突时以本节为准。

1. **容量口径**：仅 CONFIRMED 与 HELD 占用场次容量（容量校验、替代时段、候补守卫同口径）。PENDING 是待审批意向，不占容量——因此容量未满时审批实验室可同时存在多条 PENDING；批准事务内重校容量，已满则 409 APPROVAL_CAPACITY。候补 WAITING 不占容量，也不计入每周配额（BR13）。
2. **抢占边界**：仅管理/高优先级角色触发；受害者限定「未签到（checked_in_at IS NULL）且 priority 低于请求者」的 CONFIRMED/HELD 预约，取 priority 最低者；**已签到者永不被抢占**。受害者置 CANCELLED/PREEMPTED、补偿 +1 信用（钳制 0..5，满额时补偿被吸收）、通知，可凭恢复的余额重新预约。
3. **递补与审批**：候补递补是系统行为，不经过 require_approval——审批实验室的候补递补同样直接落 CONFIRMED（`--hold-window`>0 时落 HELD，截止=min(now+窗口, 开场)，确认接口 `/confirm`）；审批流只作用于用户主动预约。
4. **资源维护与声明**：声明校验仅发生在预约事务内（资源存在、属于该场次实验室、状态 AVAILABLE）。资源转入 MAINTENANCE 后新声明返回 409 STATE_CONFLICT；**既有声明与对应预约不受影响、无需回收**；恢复 AVAILABLE 后可再次声明（BR12 同时段声明配额继续生效）。
5. **改期与配额**：reschedule 不做每周配额检查；预约记录自旧场次更新到新场次后自然移出旧周、计入新周，本周有效预约（CONFIRMED/HELD，按场次 start_at 落周）总数守恒不增加——改期不会绕过也不会放松 BR13。
