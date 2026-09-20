#ifndef LAB_APP_H
#define LAB_APP_H
#include <stdint.h>
#include <stddef.h>
#include "sqlite3.h"
#include "cJSON.h"
typedef sqlite3_int64 Id;
typedef struct { sqlite3 *sql; int error; void *cache; } DB;
typedef struct { const char *db_path; const char *web_path; int port; int checkin_window; int sweep_interval; int rate_burst; int rate_refill_sec; int login_max_fails; int login_lockout; int slow_ms; const char *backup_dest; const char *restore_from; const char *fault; const char *fault_request; int remind_sec; int backup_interval; int quota_weekly; int lead_time; int hold_window; int waitlist_strict; long long fake_now; int waitlist_strategy; int waitlist_daily_limit; } Config;
#define LAB_VERSION "1.16.0"
typedef struct { Id id; int admin; char username[65]; char csrf[65]; } User;
typedef struct { int status; cJSON *body; } Result;
int db_open(DB *db,const char *path);
void db_close(DB *db);
DB *db_thread_get(const char *path);
void db_thread_bad(DB *db);
int db_run(DB *db,const char *sql,const char *fmt,...);
Id db_num(DB *db,const char *sql,const char *fmt,...);
cJSON *db_rows(DB *db,const char *sql,const char *fmt,...);
cJSON *db_first(DB *db,const char *sql,const char *fmt,...);
int db_init(DB *db);
int db_check(DB *db);
int db_seed(DB *db,const char *password);
int publish_slots(DB *db,Id lab,Id start,Id end,Id capacity);
int publish_slots_week(DB *db,Id lab,Id start,Id end,Id capacity,int mask);
Id now_sec(void);
/* r24 统一时间源：默认取系统时间，可通过 --fake-now 固定，使 hold_deadline 等边界可确定性测试 */
Id clock_now(void);
void clock_configure(const Config *cfg);
Id date_start(const char *date);
void date_text(Id day,char out[11]);
int parse_id(const char *s,Id *out);
int uuid_valid(const char *s);
int hex_token(const char *s,size_t len);
int page_arg(const char *s,int fallback,int min,int max);
const char *jstr(const cJSON *j,const char *key);
void jid(cJSON *j,const char *key,Id id);
void hash_text(const char *s,char out[65]);
void random_hex(char out[65]);
Result result(int status,const char *code,const char *message,cJSON *data);
Result db_failure(DB *db);
Result booking(DB *db,const Config *cfg,const User *u,const char *action,Id target,const cJSON *body,const char *request_id);
Result records(DB *db,const User *u,int all,Id date,int page,int size,const char *status,const char *ev_action,const char *ev_user);
Result stats(DB *db,Id start,Id end);
Result stats_export(DB *db,Id start,Id end);
Result users_list(DB *db,int page,int size,const char *q);
Result user_admin(DB *db,const User *actor,Id target,const char *op);
Result assets_list(DB *db,Id lab);
Result asset_admin(DB *db,const User *actor,Id lab,Id asset,const cJSON *body);
Result lab_utilization(DB *db,Id start,Id end);
Result asset_usage(DB *db,Id asset,Id start,Id end);
Result asset_claim_report(DB *db,Id start,Id end);
Result asset_claim_export(DB *db,Id start,Id end);
Result token_auth(DB *db,const char *raw,User *u);
/* r23：信用账户 / 资源时段 / 维护工单 / 日历订阅 */
int credit_apply(DB *db,Id user,Id delta,const char *reason,Id reservation_id);
Result credit_history(DB *db,const User *u,int page,int size);
/* r25 创新方向 1：约束感知替代建议（只读评估，bookable/joinable + 原因解释） */
Result suggestion_list(DB *db,const User *u,const Config *cfg,Id lab,Id start);
/* r26 公平性审计：按用户候补聚合 + 全站 Jain 公平指数 */
Result fairness_admin(DB *db,int days);
Result admin_credit_grant(DB *db,const User *actor,Id target,const cJSON *body);
Result asset_windows_list(DB *db,Id asset);
Result asset_window_admin(DB *db,const User *actor,Id asset,const cJSON *body);
Result asset_maintenance_list(DB *db,Id asset);
Result asset_maintenance_admin(DB *db,const User *actor,Id asset,const cJSON *body);
Result calendar_ics(DB *db,const User *u);
Result reservation_batch(DB *db,const Config *cfg,const User *u,const cJSON *body,const char *request_id);
Result reservation_confirm(DB *db,const User *u,Id target,const char *request_id);
Result asset_quals_list(DB *db,Id asset);
Result asset_qual_admin(DB *db,const User *actor,Id asset,const cJSON *body);
Result reservation_checkout(DB *db,const Config *cfg,const User *u,Id target,const char *request_id);
Result calendar_export(DB *db,const User *u);
Result token_create(DB *db,const User *u,const cJSON *body);
Result token_list(DB *db,const User *u);
Result token_revoke(DB *db,const User *u,Id target,const char *request_id);
Result reservation_reschedule(DB *db,const Config *cfg,const User *u,Id target,Id new_slot,const char *request_id);
Result reservation_approval(DB *db,const User *u,Id target,int approve,const char *request_id);
/* r32 批量审批：一个事务内逐条执行（部分成功语义，失败项入 failed[]） */
Result reservation_approval_batch(DB *db,const User *u,const cJSON *body,const char *request_id);
/* r33 管理员强制操作：force-complete（代签退）/force-cancel（ADMIN 取消+补位+通知） */
Result reservation_force(DB *db,const Config *cfg,const User *u,Id target,const char *op,const char *request_id);
Result utilization_export(DB *db,Id start,Id end);
Result notifications_sent(DB *db,int page,int size);
Result logs_tail(int lines,int level);
Result notifications(DB *db,const User *u,int unread,int page,int size);
Result notifications_read(DB *db,const User *u,const cJSON *body,const char *request_id);
Result slot_update(DB *db,const User *u,Id slot,const cJSON *body);
Result admin_notify(DB *db,const User *u,const cJSON *body);
Result sessions_list(DB *db,const User *u,const char *current_hash);
Result session_revoke(DB *db,const User *u,const char *token_hash,const char *request_id);
Result password_change(DB *db,const User *u,const char *old_password,const char *new_password,const char *current_hash,const char *request_id);
int notify(DB *db,Id user,const char *kind,const char *title,const char *body,Id slot,Id reservation);
int sweep_once(const Config *config);
int remind_once(const Config *config);
int backup_rotate(const char *src,const char *dir,int keep);
void sweep_start(const Config *config);
int serve(const Config *config);
/* r5/security */
typedef struct { double tokens; Id last_ms; int started; } RateBucket;
typedef struct { int fails; Id locked_until; } LoginGuard;
int bucket_allow(RateBucket *bucket,Id now_ms,int burst,int refill_sec);
int login_allow(const LoginGuard *guard,Id now_ms);
void login_record_fail(LoginGuard *guard,Id now_ms,int max_fails,int lockout_sec);
void login_record_ok(LoginGuard *guard);
void rl_configure(const Config *config);
int rl_login_gate(const char *username);
void rl_login_fail(const char *username);
void rl_login_ok(const char *username);
int rl_consume(Id user_id);
int rl_register_gate(void);
/* r5/metrics */
void metrics_init(void);
void metrics_record_request(int status,double elapsed_ms);
void metrics_inc_login(void);
cJSON *metrics_snapshot(void);
char *metrics_prometheus(void);
/* r6/L3 日志；r6/L5 在线备份 */
int log_init(const char *path,long max_bytes);
void log_write(int level,const char *fmt,...);
int db_backup(const char *src,const char *dest);
#endif
