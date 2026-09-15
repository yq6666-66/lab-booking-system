#include "app.h"
#include <stdarg.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <sodium.h>
#include <time.h>
#include <windows.h>
#ifndef WATCHDOG_MS
#define WATCHDOG_MS 5000
#endif
/* r6 语句看门狗：单条语句墙钟超过 WATCHDOG_MS（含锁等待）由 progress 回调打断，
   防止慢查询长期占用写锁；常规短事务不受影响。单测构建以 -DWATCHDOG_MS=50 收紧验证。 */
static _Thread_local unsigned long long tls_tick0;
static int prog_cb(void *p){(void)p;return GetTickCount64()-tls_tick0>WATCHDOG_MS;}
#ifndef STMT_CACHE_MAX
#define STMT_CACHE_MAX 32
#endif
/* r6 语句缓存：每连接按 SQL 文本缓存预处理语句（LRU，上限 32），消除重复 prepare；
   任一步骤出错即淘汰对应条目，保守优先。 */
typedef struct { char *sql; sqlite3_stmt *stmt; unsigned lru; } CacheEntry;
typedef struct { CacheEntry items[STMT_CACHE_MAX]; unsigned tick; } StmtCache;
static sqlite3_stmt *cache_fetch(DB *d,const char *sql){
 StmtCache *c=(StmtCache*)d->cache;unsigned best=0;int bi=-1;
 for(int i=0;i<STMT_CACHE_MAX;i++){
  CacheEntry *e=&c->items[i];
  if(e->sql&&e->lru>best){best=e->lru;bi=i;}
  if(e->sql&&!strcmp(e->sql,sql)){
   if(sqlite3_reset(e->stmt)!=SQLITE_OK||sqlite3_clear_bindings(e->stmt)!=SQLITE_OK){sqlite3_finalize(e->stmt);free(e->sql);e->sql=NULL;e->stmt=NULL;return NULL;}
   e->lru=++c->tick;return e->stmt;
  }
 }
 (void)bi;return NULL;
}
static void cache_store(DB *d,const char *sql,sqlite3_stmt *stmt){
 StmtCache *c=(StmtCache*)d->cache;int slot=-1, victim=-1;unsigned minl=~0u;
 for(int i=0;i<STMT_CACHE_MAX&&slot<0;i++){
  if(!c->items[i].sql)slot=i;
  else if(c->items[i].lru<minl){minl=c->items[i].lru;victim=i;}
 }
 if(slot<0){
  if(victim<0){sqlite3_finalize(stmt);return;}
  CacheEntry *v=&c->items[victim];sqlite3_finalize(v->stmt);free(v->sql);v->sql=NULL;v->stmt=NULL;slot=victim;
 }
 c->items[slot].sql=_strdup(sql);c->items[slot].stmt=stmt;c->items[slot].lru=++c->tick;
 if(!c->items[slot].sql){sqlite3_finalize(stmt);c->items[slot].stmt=NULL;return;}
}
static void cache_drop(DB *d,sqlite3_stmt *stmt){
 StmtCache *c=(StmtCache*)d->cache;
 for(int i=0;i<STMT_CACHE_MAX;i++)if(c->items[i].stmt==stmt){sqlite3_finalize(stmt);free(c->items[i].sql);c->items[i].sql=NULL;c->items[i].stmt=NULL;return;}
}
static void cache_clear(DB *d){
 StmtCache *c=(StmtCache*)d->cache;if(!c)return;
 for(int i=0;i<STMT_CACHE_MAX;i++)if(c->items[i].stmt){sqlite3_finalize(c->items[i].stmt);free(c->items[i].sql);c->items[i].sql=NULL;c->items[i].stmt=NULL;}
}
static sqlite3_stmt *stmt_get(DB *d,const char *sql){
 sqlite3_stmt *s=cache_fetch(d,sql);
 if(s)return s;
 if(sqlite3_prepare_v2(d->sql,sql,-1,&s,NULL)!=SQLITE_OK){d->error=sqlite3_errcode(d->sql);return NULL;}
 cache_store(d,sql,s);return s;
}
static void stmt_done(DB *d,sqlite3_stmt *s){(void)d;sqlite3_reset(s);}
static void stmt_fail(DB *d,sqlite3_stmt *s){cache_drop(d,s);}
static sqlite3_stmt *prepare(DB *d,const char *sql,const char *fmt,va_list a){
 tls_tick0=GetTickCount64();
 sqlite3_stmt *s=stmt_get(d,sql);
 if(!s){ d->error=d->error?d->error:SQLITE_NOMEM;return NULL; }
 int rc=0;
 for(int i=0;fmt&&fmt[i];i++){
   if(fmt[i]=='i')rc=sqlite3_bind_int64(s,i+1,va_arg(a,Id));
  else if(fmt[i]=='s'){const char *v=va_arg(a,const char*);rc=v?sqlite3_bind_text(s,i+1,v,-1,SQLITE_TRANSIENT):sqlite3_bind_null(s,i+1);}
  else rc=SQLITE_MISUSE;
  if(rc!=SQLITE_OK){d->error=rc;stmt_fail(d,s);return NULL;}
 }
 return s;
}
int db_open(DB *d,const char *p){
 memset(d,0,sizeof *d);int rc=sqlite3_open_v2(p,&d->sql,SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_FULLMUTEX,NULL);
 if(rc!=SQLITE_OK){d->error=rc;return 0;}
 d->cache=calloc(1,sizeof(StmtCache));
 if(!d->cache){sqlite3_close(d->sql);d->sql=NULL;d->error=SQLITE_NOMEM;return 0;}
 sqlite3_extended_result_codes(d->sql,1);sqlite3_busy_timeout(d->sql,3000);
 sqlite3_progress_handler(d->sql,200000,prog_cb,0); /* 语句看门狗：超长查询按 20 万 VM 步打断 */
 db_run(d,"PRAGMA foreign_keys=ON","");db_run(d,"PRAGMA synchronous=FULL","");
 db_run(d,"PRAGMA mmap_size=268435456","");   /* 256MB 内存映射读取 */
 db_run(d,"PRAGMA wal_autocheckpoint=512",""); /* WAL 512 页（约 2MB）自动检查点 */
 db_run(d,"PRAGMA cache_size=-2000","");       /* 2MB 页缓存 */return !d->error;
}
void db_close(DB *d){if(d->sql){if(!sqlite3_get_autocommit(d->sql))sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);cache_clear(d);sqlite3_close(d->sql);}d->sql=NULL;d->cache=NULL;}
int db_run(DB *d,const char *sql,const char *fmt,...){
 va_list a;va_start(a,fmt);sqlite3_stmt *s=prepare(d,sql,fmt,a);va_end(a);if(!s)return 0;
 int rc;do{rc=sqlite3_step(s);}while(rc==SQLITE_ROW);if(rc!=SQLITE_DONE)d->error=rc;
 if(rc!=SQLITE_DONE){stmt_fail(d,s);return 0;}
 stmt_done(d,s);return 1;
}
Id db_num(DB *d,const char *sql,const char *fmt,...){
 va_list a;va_start(a,fmt);sqlite3_stmt *s=prepare(d,sql,fmt,a);va_end(a);if(!s)return 0;
 int rc=sqlite3_step(s);Id n=0;
 if(rc==SQLITE_ROW){n=sqlite3_column_int64(s,0);rc=sqlite3_step(s);}
 if(rc!=SQLITE_DONE){d->error=rc;stmt_fail(d,s);return 0;}
 stmt_done(d,s);return n;
}
static cJSON *rowsv(DB *d,const char *sql,const char *fmt,va_list a){
 sqlite3_stmt *s=prepare(d,sql,fmt,a);if(!s)return NULL;cJSON *rows=cJSON_CreateArray();if(!rows){d->error=SQLITE_NOMEM;stmt_fail(d,s);return NULL;}
 int rc;while((rc=sqlite3_step(s))==SQLITE_ROW){
  cJSON *r=cJSON_CreateObject();if(!r){d->error=SQLITE_NOMEM;break;}cJSON_AddItemToArray(rows,r);
  for(int i=0;i<sqlite3_column_count(s);i++){
   const char *n=sqlite3_column_name(s,i);int t=sqlite3_column_type(s,i);size_t len=strlen(n);
   if(t==SQLITE_NULL)cJSON_AddNullToObject(r,n);
   else if(t==SQLITE_INTEGER){
    if(!strcmp(n,"id")||(len>3&&!strcmp(n+len-3,"_id")))jid(r,n,sqlite3_column_int64(s,i));
    else if(!strcmp(n,"enabled")||!strcmp(n,"lab_enabled")||!strcmp(n,"occupied"))cJSON_AddBoolToObject(r,n,sqlite3_column_int(s,i));
    else cJSON_AddNumberToObject(r,n,(double)sqlite3_column_int64(s,i));
   }else cJSON_AddStringToObject(r,n,(const char*)sqlite3_column_text(s,i));
  }
 }
 if(rc!=SQLITE_DONE){if(!d->error)d->error=rc;stmt_fail(d,s);cJSON_Delete(rows);return NULL;}
 stmt_done(d,s);
 if(d->error){cJSON_Delete(rows);return NULL;}return rows;
}
cJSON *db_rows(DB *d,const char *sql,const char *fmt,...){va_list a;va_start(a,fmt);cJSON *j=rowsv(d,sql,fmt,a);va_end(a);return j;}
cJSON *db_first(DB *d,const char *sql,const char *fmt,...){va_list a;va_start(a,fmt);cJSON *j=rowsv(d,sql,fmt,a);va_end(a);if(!j)return NULL;cJSON *r=cJSON_DetachItemFromArray(j,0);cJSON_Delete(j);return r;}
static int has_column(DB *d,const char *table,const char *column){
 char sql[96];snprintf(sql,sizeof sql,"PRAGMA table_info(%s)",table);
 cJSON *rows=d->error?NULL:db_rows(d,sql,"");if(!rows)return 0;
 int found=0;cJSON *it;cJSON_ArrayForEach(it,rows)if(jstr(it,"name")&&!strcmp(jstr(it,"name"),column)){found=1;break;}
 cJSON_Delete(rows);return found;
}
/* v1→v2：仅追加列，不重建表；v2→v3：场次容量制，退役单占用唯一索引。全部幂等。 */
static int db_migrate(DB *d){
 if(!has_column(d,"reservations","checked_in_at")||!has_column(d,"reservations","cancel_reason")||!has_column(d,"sessions","created_at")){
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  if(!has_column(d,"reservations","checked_in_at"))db_run(d,"ALTER TABLE reservations ADD COLUMN checked_in_at INTEGER","");
  if(!has_column(d,"reservations","cancel_reason"))db_run(d,"ALTER TABLE reservations ADD COLUMN cancel_reason TEXT","");
  if(!has_column(d,"sessions","created_at"))db_run(d,"ALTER TABLE sessions ADD COLUMN created_at INTEGER","");
  db_run(d,"UPDATE reservations SET cancel_reason='USER' WHERE status='CANCELLED' AND cancel_reason IS NULL","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
 if(!has_column(d,"slots","capacity")){
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE slots ADD COLUMN capacity INTEGER NOT NULL DEFAULT 1 CHECK(capacity BETWEEN 1 AND 200)","");
  db_run(d,"DROP INDEX IF EXISTS one_booking","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
 if(!has_column(d,"labs","require_approval")){ /* r19 审批开关：幂等追加 */
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE labs ADD COLUMN require_approval INTEGER NOT NULL DEFAULT 0 CHECK(require_approval IN(0,1))","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
 if(has_column(d,"api_tokens","token_hash")&&!has_column(d,"api_tokens","id")){ /* r20 旧结构修复：令牌为可再生数据，直接重建 */
  db_run(d,"DROP TABLE api_tokens","");
 }
 if(!has_column(d,"api_tokens","token_hash")){ /* r20 令牌表：幂等追加 */
  db_run(d,"CREATE TABLE api_tokens(id INTEGER PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,user_id INTEGER NOT NULL REFERENCES users(id),name TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,last_used_at INTEGER);","");
 }
 if(!has_column(d,"reservations","checked_out_at")){ /* r16 签退列：幂等追加 */
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE reservations ADD COLUMN checked_out_at INTEGER","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
 if(!has_column(d,"reservations","note")){ /* r18 预约备注列：幂等追加 */
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE reservations ADD COLUMN note TEXT","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
  if(!has_column(d,"slots","reminded_at")){ /* v3/v4→提醒列：幂等追加 */
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE slots ADD COLUMN reminded_at INTEGER","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
/* r19 审批状态 + r23 抢占态：reservations 的 status/reason CHECK 需扩展 PENDING/REJECTED/PREEMPTED——
    CHECK 无法原地修改，且三张表引用本表，重建须临时关断外键并逐一重建索引。以是否含对应枚举字面量判定，幂等。 */
 {cJSON *rz=db_first(d,"SELECT sql FROM sqlite_master WHERE type='table' AND name='reservations'","");
  if(!rz)return d->error?0:1;
  const char *rdl=jstr(rz,"sql");
  if(rdl&&(!strstr(rdl,"'PENDING'")||!strstr(rdl,"'PREEMPTED'")||!strstr(rdl,"'HELD'"))){
   cJSON_Delete(rz);
   db_run(d,"PRAGMA foreign_keys=OFF","");
   if(!db_run(d,"BEGIN IMMEDIATE","")){return 0;}
   /* r24：status 扩展 HELD（限时保留）与 EXPIRED（确认超时）；新列 priority/hold_deadline 由本表 DDL 提供，
      旧库的既有行取默认值（DEFAULT 0 / NULL）。INSERT SELECT 只搬既有列，避免在旧库上引用不存在的列。 */
   db_run(d,"CREATE TABLE reservations_new(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),slot_id INTEGER NOT NULL REFERENCES slots(id),status TEXT NOT NULL CHECK(status IN('CONFIRMED','PENDING','CANCELLED','HELD','EXPIRED')),source TEXT NOT NULL CHECK(source IN('DIRECT','WAITLIST')),created_at INTEGER NOT NULL,cancelled_at INTEGER,checked_in_at INTEGER,checked_out_at INTEGER,cancel_reason TEXT CHECK(cancel_reason IS NULL OR cancel_reason IN('USER','NO_SHOW','REJECTED','PREEMPTED')),note TEXT,priority INTEGER NOT NULL DEFAULT 0,hold_deadline INTEGER);","");
   db_run(d,"INSERT INTO reservations_new(id,user_id,slot_id,status,source,created_at,cancelled_at,checked_in_at,checked_out_at,cancel_reason,note) SELECT id,user_id,slot_id,status,source,created_at,cancelled_at,checked_in_at,checked_out_at,cancel_reason,note FROM reservations","");
   db_run(d,"DROP TABLE reservations","");
   db_run(d,"ALTER TABLE reservations_new RENAME TO reservations","");
   db_run(d,"CREATE INDEX IF NOT EXISTS bookings_slot ON reservations(slot_id,status);","");
   db_run(d,"CREATE INDEX IF NOT EXISTS bookings_user ON reservations(user_id,created_at);","");
   if(!db_run(d,"COMMIT","")){db_run(d,"PRAGMA foreign_keys=ON","");return 0;}
   db_run(d,"PRAGMA foreign_keys=ON","");
  } else cJSON_Delete(rz);
 }
 /* v3→v4：notifications.kind 增加 'NOTICE'（管理员公告）与 'REMIND'（开场提醒）；CHECK 无法原地修改，检测到旧约束时重建表。 */
 cJSON *nt=db_first(d,"SELECT sql FROM sqlite_master WHERE type='table' AND name='notifications'","");
 if(!nt)return d->error?0:1;
 const char *nddl=jstr(nt,"sql");
 if(nddl&&(!strstr(nddl,"'NOTICE'")||!strstr(nddl,"'REMIND'"))){
  if(!db_run(d,"BEGIN IMMEDIATE","")){cJSON_Delete(nt);return 0;}
  db_run(d,"CREATE TABLE notifications_new(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),kind TEXT NOT NULL CHECK(kind IN('PROMOTED','NO_SHOW','NOTICE','REMIND')),title TEXT NOT NULL,body TEXT NOT NULL,slot_id INTEGER REFERENCES slots(id),reservation_id INTEGER REFERENCES reservations(id),read_at INTEGER,created_at INTEGER NOT NULL)","");
  db_run(d,"INSERT INTO notifications_new(id,user_id,kind,title,body,slot_id,reservation_id,read_at,created_at) SELECT id,user_id,kind,title,body,slot_id,reservation_id,read_at,created_at FROM notifications","");
  db_run(d,"DROP TABLE notifications","");
  db_run(d,"ALTER TABLE notifications_new RENAME TO notifications","");
  db_run(d,"CREATE INDEX IF NOT EXISTS notify_user ON notifications(user_id,id)","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);cJSON_Delete(nt);return 0;}
  if(!db_run(d,"COMMIT","")){cJSON_Delete(nt);return 0;}
 }
 cJSON_Delete(nt);
 /* r23 预约优先级列：抢占判定的依据（与 waitlist.priority 同源，均由服务端按角色校准）。 */
 if(!has_column(d,"reservations","priority")){
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE reservations ADD COLUMN priority INTEGER NOT NULL DEFAULT 0","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
 /* r23 信用账户列：幂等追加；历史用户按默认额度补齐，并把既有"每周配额/爽约信用"语义统一到一本账。 */
 if(!has_column(d,"users","credit")){
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE users ADD COLUMN credit INTEGER NOT NULL DEFAULT 5","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
 /* r23 候补优先级列：幂等追加（旧库 waitlist 原本没有该列）。 */
 if(!has_column(d,"waitlist","priority")){
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE waitlist ADD COLUMN priority INTEGER NOT NULL DEFAULT 0","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
 /* r23 候补排序索引纳入 priority。这里按"索引定义是否含 priority"判定而不是按列判定，
    以保证新库（schema 建的兼容索引）与迁移库最终一致；且必须在 priority 列存在之后执行。 */
 {cJSON *wi=db_first(d,"SELECT sql FROM sqlite_master WHERE type='index' AND name='wait_order'","");
  if(!wi)return d->error?0:1;
  const char *isql=jstr(wi,"sql");
  if(isql&&!strstr(isql,"priority")){
   db_run(d,"DROP INDEX IF EXISTS wait_order","");
   db_run(d,"CREATE INDEX IF NOT EXISTS wait_order ON waitlist(slot_id,status,priority DESC,id)","");
   if(d->error){cJSON_Delete(wi);return 0;}
  }
  cJSON_Delete(wi);
 }
 /* r24 资源资格开关列：幂等追加（只有显式开启的资源才校验使用资格）。 */
 if(!has_column(d,"assets","requires_qualification")){
  if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
  db_run(d,"ALTER TABLE assets ADD COLUMN requires_qualification INTEGER NOT NULL DEFAULT 0 CHECK(requires_qualification IN(0,1))","");
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return 0;}
  if(!db_run(d,"COMMIT",""))return 0;
 }
 return 1;
}
int db_init(DB *d){
 const char *schema=
 "BEGIN IMMEDIATE;"
 "CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN('USER','ADMIN')),enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),credit INTEGER NOT NULL DEFAULT 5);"
 "CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),csrf_token TEXT NOT NULL,expires_at INTEGER NOT NULL,created_at INTEGER);"
 "CREATE TABLE IF NOT EXISTS labs(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,location TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),require_approval INTEGER NOT NULL DEFAULT 0 CHECK(require_approval IN(0,1)));"
 "CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY,lab_id INTEGER NOT NULL REFERENCES labs(id),start_at INTEGER NOT NULL,end_at INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),capacity INTEGER NOT NULL DEFAULT 1 CHECK(capacity BETWEEN 1 AND 200),reminded_at INTEGER,UNIQUE(lab_id,start_at),CHECK(end_at=start_at+3600));"
 "CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),slot_id INTEGER NOT NULL REFERENCES slots(id),status TEXT NOT NULL CHECK(status IN('CONFIRMED','PENDING','CANCELLED','HELD','EXPIRED')),source TEXT NOT NULL CHECK(source IN('DIRECT','WAITLIST')),created_at INTEGER NOT NULL,cancelled_at INTEGER,checked_in_at INTEGER,cancel_reason TEXT CHECK(cancel_reason IS NULL OR cancel_reason IN('USER','NO_SHOW','REJECTED','PREEMPTED')),note TEXT,priority INTEGER NOT NULL DEFAULT 0,hold_deadline INTEGER);"
 "CREATE INDEX IF NOT EXISTS bookings_slot ON reservations(slot_id,status);"
 "CREATE INDEX IF NOT EXISTS bookings_user ON reservations(user_id,created_at);"
 "CREATE TABLE IF NOT EXISTS waitlist(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),slot_id INTEGER NOT NULL REFERENCES slots(id),status TEXT NOT NULL CHECK(status IN('WAITING','WITHDRAWN','PROMOTED','SKIPPED')),created_at INTEGER NOT NULL,promoted_reservation_id INTEGER REFERENCES reservations(id),priority INTEGER NOT NULL DEFAULT 0);"
 "CREATE UNIQUE INDEX IF NOT EXISTS one_waiter ON waitlist(user_id,slot_id) WHERE status='WAITING';"
 "CREATE INDEX IF NOT EXISTS wait_order ON waitlist(slot_id,status,id);"
 "CREATE TABLE IF NOT EXISTS request_receipts(user_id INTEGER NOT NULL REFERENCES users(id),request_id TEXT NOT NULL,action TEXT NOT NULL,payload_digest TEXT NOT NULL,http_status INTEGER NOT NULL,result_json TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(user_id,request_id));"
 "CREATE TABLE IF NOT EXISTS operation_events(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL REFERENCES users(id),action TEXT NOT NULL,entity_id INTEGER NOT NULL,request_id TEXT,created_at INTEGER NOT NULL);"
 "CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),kind TEXT NOT NULL CHECK(kind IN('PROMOTED','NO_SHOW','NOTICE','REMIND')),title TEXT NOT NULL,body TEXT NOT NULL,slot_id INTEGER REFERENCES slots(id),reservation_id INTEGER REFERENCES reservations(id),read_at INTEGER,created_at INTEGER NOT NULL);"
 "CREATE INDEX IF NOT EXISTS notify_user ON notifications(user_id,id);"
 "CREATE TABLE IF NOT EXISTS assets(id INTEGER PRIMARY KEY,lab_id INTEGER NOT NULL REFERENCES labs(id),name TEXT NOT NULL,spec TEXT NOT NULL DEFAULT '',total INTEGER NOT NULL DEFAULT 1 CHECK(total BETWEEN 1 AND 999),status TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK(status IN('AVAILABLE','MAINTENANCE','DISABLED')),created_at INTEGER NOT NULL,requires_qualification INTEGER NOT NULL DEFAULT 0 CHECK(requires_qualification IN(0,1)));"
 "CREATE INDEX IF NOT EXISTS assets_lab ON assets(lab_id,id);"
 "CREATE UNIQUE INDEX IF NOT EXISTS assets_uniq ON assets(lab_id,name);"
 "CREATE TABLE IF NOT EXISTS asset_claims(reservation_id INTEGER NOT NULL REFERENCES reservations(id),asset_id INTEGER NOT NULL REFERENCES assets(id),created_at INTEGER NOT NULL,PRIMARY KEY(reservation_id,asset_id));"
 "CREATE INDEX IF NOT EXISTS claims_asset ON asset_claims(asset_id);"
 "CREATE TABLE IF NOT EXISTS api_tokens(id INTEGER PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,user_id INTEGER NOT NULL REFERENCES users(id),name TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,last_used_at INTEGER);"
 /* r23 信用账户：把"每周配额"与"爽约信用"统一为一本可追溯流水。 */
 "CREATE TABLE IF NOT EXISTS credit_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),delta INTEGER NOT NULL,reason TEXT NOT NULL,reservation_id INTEGER,created_at INTEGER NOT NULL);"
 "CREATE INDEX IF NOT EXISTS credit_user ON credit_ledger(user_id,id);"
 /* r23 资源自身的可用时段（对标 working plans）：与场次时段正交，用于表达"每周三下午检修"等。 */
 "CREATE TABLE IF NOT EXISTS asset_windows(id INTEGER PRIMARY KEY AUTOINCREMENT,asset_id INTEGER NOT NULL REFERENCES assets(id),weekday_mask INTEGER NOT NULL DEFAULT 127 CHECK(weekday_mask BETWEEN 0 AND 127),start_minute INTEGER NOT NULL CHECK(start_minute BETWEEN 0 AND 1439),end_minute INTEGER NOT NULL CHECK(end_minute BETWEEN 1 AND 1440),reason TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,CHECK(end_minute>start_minute));"
 "CREATE INDEX IF NOT EXISTS windows_asset ON asset_windows(asset_id);"
 /* r23 资源维护工单：设备生命周期可追溯，与 assets.status 联动。 */
 "CREATE TABLE IF NOT EXISTS asset_maintenance(id INTEGER PRIMARY KEY AUTOINCREMENT,asset_id INTEGER NOT NULL REFERENCES assets(id),started_at INTEGER NOT NULL,ended_at INTEGER,reason TEXT NOT NULL DEFAULT '',operator_id INTEGER REFERENCES users(id));"
 "CREATE INDEX IF NOT EXISTS maint_asset ON asset_maintenance(asset_id,ended_at);"
 /* r24 资格授权：贵重设备需培训授权后方可声明（默认不启用，资源须显式开启 requires_qualification）。 */
 "CREATE TABLE IF NOT EXISTS qualifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),asset_id INTEGER NOT NULL REFERENCES assets(id),granted_at INTEGER NOT NULL,expires_at INTEGER,note TEXT NOT NULL DEFAULT '',UNIQUE(user_id,asset_id));"
 "CREATE INDEX IF NOT EXISTS qual_asset ON qualifications(asset_id,user_id);"
 /* r24 通知出站队列：同事务写入，提交后派发；失败可重试（至少一次语义）。 */
 "CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,notification_id INTEGER REFERENCES notifications(id),channel TEXT NOT NULL,payload TEXT NOT NULL,sent_at INTEGER,attempts INTEGER NOT NULL DEFAULT 0,last_error TEXT,created_at INTEGER NOT NULL);"
 "CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(sent_at,id);"
 "PRAGMA user_version=5;COMMIT;";
 cJSON *wal=db_first(d,"PRAGMA journal_mode=WAL","");int ok=wal&&jstr(wal,"journal_mode")&&!strcmp(jstr(wal,"journal_mode"),"wal");cJSON_Delete(wal);if(!ok)return 0;
 int rc=sqlite3_exec(d->sql,schema,NULL,NULL,NULL);if(rc!=SQLITE_OK){d->error=rc;}
 if(d->error)return 0;
 return db_migrate(d);
}
int publish_slots(DB *d,Id lab,Id start,Id end,Id capacity){ /* capacity 必须 Id：绑定格式 i 按 8 字节变参读取 */
 return publish_slots_week(d,lab,start,end,capacity,0); /* mask=0 表示每日 */
}
int publish_slots_week(DB *d,Id lab,Id start,Id end,Id capacity,int mask){ /* mask bit0..bit6=周一..周日，0=每日；北京周=(days+3)%7 */
 int count=0;const int hours[]={8,9,10,11,14,15,16,17};
 for(Id day=start;day<=end;day+=86400)for(int h=0;h<8;h++){
  Id s=day+hours[h]*3600;if(s<=now_sec())continue;
  if(mask){int wd=(int)(((s+28800)/86400+3)%7);if(!(mask&(1<<wd)))continue;}
  if(!db_run(d,"INSERT INTO slots(lab_id,start_at,end_at,capacity) VALUES(?,?,?,?) ON CONFLICT(lab_id,start_at) DO NOTHING","iiii",lab,s,s+3600,capacity))return count;
  count+=sqlite3_changes(d->sql);
 }return count;
}
int db_check(DB *d){
 int ok=1;
 {cJSON *ci=db_rows(d,"PRAGMA table_info(reservations)","");char *pt=ci?cJSON_PrintUnformatted(ci):NULL;free(pt);cJSON_Delete(ci);}
 cJSON *r=db_first(d,"PRAGMA integrity_check","");if(!(r&&jstr(r,"integrity_check")&&!strcmp(jstr(r,"integrity_check"),"ok"))){ok=0;}cJSON_Delete(r);
 cJSON *fk=db_rows(d,"PRAGMA foreign_key_check","");if(!fk||cJSON_GetArraySize(fk)){ok=0;}cJSON_Delete(fk);
 if(db_num(d,"SELECT count(*) FROM waitlist w JOIN reservations r ON w.user_id=r.user_id AND w.slot_id=r.slot_id WHERE w.status='WAITING' AND r.status='CONFIRMED'","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM waitlist w LEFT JOIN reservations r ON r.id=w.promoted_reservation_id WHERE w.status='PROMOTED' AND (r.id IS NULL OR r.user_id!=w.user_id OR r.slot_id!=w.slot_id OR r.source!='WAITLIST')","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM slots s WHERE (SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED')>s.capacity","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM reservations WHERE checked_in_at IS NOT NULL AND status<>'CONFIRMED'","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM reservations WHERE cancel_reason='NO_SHOW' AND (status<>'CANCELLED' OR checked_in_at IS NOT NULL)","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM reservations WHERE cancel_reason IS NOT NULL AND status<>'CANCELLED'","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM reservations WHERE status='CANCELLED' AND cancelled_at IS NOT NULL AND cancel_reason IS NULL","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM reservations WHERE checked_out_at IS NOT NULL AND checked_in_at IS NULL","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM reservations WHERE checked_out_at IS NOT NULL AND checked_out_at<checked_in_at","")>0){ok=0;}
 /* r23 新增不变量：候补优先级非负、信用流水无零变动、资源时段区间合法、维护工单时间有序。 */
 if(db_num(d,"SELECT count(*) FROM waitlist WHERE priority<0","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM credit_ledger WHERE delta=0","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM asset_windows WHERE end_minute<=start_minute OR weekday_mask<0 OR weekday_mask>127","")>0){ok=0;}
 if(db_num(d,"SELECT count(*) FROM asset_maintenance WHERE ended_at IS NOT NULL AND ended_at<started_at","")>0){ok=0;}
 /* r25 凭据生命周期：已停用用户不得残留任何 API 令牌（停用/改密/重置密码时同事务吊销）。 */
 if(db_num(d,"SELECT count(*) FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE u.enabled=0","")>0){ok=0;}
 if(d->error){ok=0;}
 return ok;
}
int db_seed(DB *d,const char *password){
 if(!password||strlen(password)<8||strlen(password)>128){fprintf(stderr,"LAB_SEED_PASSWORD must contain 8..128 bytes.\n");return 0;}
 char hash[crypto_pwhash_STRBYTES];if(crypto_pwhash_str(hash,password,strlen(password),crypto_pwhash_OPSLIMIT_INTERACTIVE,crypto_pwhash_MEMLIMIT_INTERACTIVE))return 0;
 if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
 for(int i=0;i<=20;i++){char name[32];if(i)snprintf(name,sizeof name,"user%02d",i);else strcpy(name,"admin");db_run(d,"INSERT INTO users(username,password_hash,role) VALUES(?,?,?) ON CONFLICT(username) DO NOTHING","sss",name,hash,i?"USER":"ADMIN");}
 const char *names[]={"软件工程实验室","计算机网络实验室","系统与数据实验室"};
 for(int i=0;i<3;i++)db_run(d,"INSERT INTO labs(name,location,description) VALUES(?,?,?) ON CONFLICT(name) DO NOTHING","sss",names[i],"信息楼","整间实验室 · 固定一小时场次");
 /* r13 资源清单种子：随实验室名关联（重跑幂等），让「资源管理」开箱即用 */
 db_run(d,"INSERT OR IGNORE INTO assets(lab_id,name,spec,total,status,created_at) SELECT id,'高性能图形工作站','32 核 / 128 GB / RTX 级 GPU',20,'AVAILABLE',strftime('%s','now') FROM labs WHERE name='软件工程实验室'","");
 db_run(d,"INSERT OR IGNORE INTO assets(lab_id,name,spec,total,status,created_at) SELECT id,'软件开发套件终端','双屏开发机',24,'AVAILABLE',strftime('%s','now') FROM labs WHERE name='软件工程实验室'","");
 db_run(d,"INSERT OR IGNORE INTO assets(lab_id,name,spec,total,status,created_at) SELECT id,'网络协议分析套件','抓包网卡 + 分析平台',16,'AVAILABLE',strftime('%s','now') FROM labs WHERE name='计算机网络实验室'","");
 db_run(d,"INSERT OR IGNORE INTO assets(lab_id,name,spec,total,status,created_at) SELECT id,'路由交换实验台','三层交换机 × 4',6,'MAINTENANCE',strftime('%s','now') FROM labs WHERE name='计算机网络实验室'","");
 db_run(d,"INSERT OR IGNORE INTO assets(lab_id,name,spec,total,status,created_at) SELECT id,'数据服务器机架','分布式存储节点',8,'AVAILABLE',strftime('%s','now') FROM labs WHERE name='系统与数据实验室'","");
 Id today=(now_sec()+28800)/86400*86400-28800;
 for(int i=0;i<3;i++){Id lab=db_num(d,"SELECT id FROM labs WHERE name=?","s",names[i]);publish_slots(d,lab,today,today+13*86400,1);}
 sodium_memzero(hash,sizeof hash);
 if(d->error){db_run(d,"ROLLBACK","");return 0;}
 return db_run(d,"COMMIT","");
}
/* r6 线程级连接复用：每个 HTTP 工作线程持有自己的连接（含语句缓存），
   首次使用时打开；请求结束后 db_thread_bad 回收未结事务，致命错误时丢弃重建。
   工作线程随进程存活，缓存随线程生命周期有界。 */
static _Thread_local DB tls_db;
static _Thread_local int tls_alive;
DB *db_thread_get(const char *path){
 if(tls_alive){tls_db.error=0;return &tls_db;}
 if(db_open(&tls_db,path)){tls_alive=1;return &tls_db;}
 memset(&tls_db,0,sizeof tls_db);return NULL;
}
void db_thread_bad(DB *d){
 if(!d||!d->sql)return;
 if(!sqlite3_get_autocommit(d->sql))sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);
 int e=d->error&255;
 if(e==SQLITE_CORRUPT||e==SQLITE_IOERR||e==SQLITE_CANTOPEN||e==SQLITE_NOMEM){db_close(d);memset(d,0,sizeof *d);tls_alive=0;}
}
/* r6/L5 在线备份：SQLite Backup API，不阻塞业务连接 */
int db_backup(const char *src,const char *dest){
 sqlite3 *s=NULL,*d=NULL;
 if(sqlite3_open_v2(src,&s,SQLITE_OPEN_READONLY,NULL)!=SQLITE_OK){if(s)sqlite3_close(s);return 0;}
 if(sqlite3_open(dest,&d)!=SQLITE_OK){sqlite3_close(s);if(d)sqlite3_close(d);return 0;}
 int rc=1;sqlite3_backup *b=sqlite3_backup_init(d,"main",s,"main");
 if(!b)rc=0;
 else if(sqlite3_backup_step(b,-1)!=SQLITE_DONE)rc=0;
 if(b)sqlite3_backup_finish(b);
 sqlite3_close(s);sqlite3_close(d);return rc;
}
/* 定时备份 + 轮转：写 dir/lab-backup-YYYYMMDD-HHMMSS.db，保留最近 keep 份。 */
int backup_rotate(const char *src,const char *dir,int keep){
 if(!src||!dir||keep<1)return 0;
 time_t t=time(NULL);struct tm lt;localtime_s(&lt,&t);
 char dest[MAX_PATH],pattern[MAX_PATH];
 if(snprintf(dest,sizeof dest,"%s\\lab-backup-%04d%02d%02d-%02d%02d%02d.db",dir,lt.tm_year+1900,lt.tm_mon+1,lt.tm_mday,lt.tm_hour,lt.tm_min,lt.tm_sec)>=(int)sizeof dest)return 0;
 if(!db_backup(src,dest))return 0;
 if(snprintf(pattern,sizeof pattern,"%s\\lab-backup-*.db",dir)>=(int)sizeof pattern)return 1; /* 备份已成功，轮转失败不回退 */
 for(int guard=0;guard<1000;guard++){ /* 反复删最旧一份直到数量<=keep；防御性上限防死循环 */
  WIN32_FIND_DATAA fd;int count=0;char oldest[MAX_PATH]="";FILETIME ft;ft.dwHighDateTime=MAXDWORD;ft.dwLowDateTime=MAXDWORD;
  HANDLE h=FindFirstFileA(pattern,&fd);if(h==INVALID_HANDLE_VALUE)break;
  do{
   if(fd.dwFileAttributes&FILE_ATTRIBUTE_DIRECTORY)continue;
   count++;
   if(CompareFileTime(&fd.ftCreationTime,&ft)<0){ft=fd.ftCreationTime;int k=snprintf(oldest,sizeof oldest,"%s\\%s",dir,fd.cFileName);if(k>0&&(size_t)k>=sizeof oldest)oldest[0]=0;}
  }while(FindNextFileA(h,&fd));
  FindClose(h);
  if(count<=keep||!oldest[0])break;
  DeleteFileA(oldest);
 }
 return 1;
}
