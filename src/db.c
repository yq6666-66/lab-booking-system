#include "app.h"
#include <stdarg.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <sodium.h>
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
 return 1;
}
int db_init(DB *d){
 const char *schema=
 "BEGIN IMMEDIATE;"
 "CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN('USER','ADMIN')),enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)));"
 "CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),csrf_token TEXT NOT NULL,expires_at INTEGER NOT NULL,created_at INTEGER);"
 "CREATE TABLE IF NOT EXISTS labs(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE,location TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)));"
 "CREATE TABLE IF NOT EXISTS slots(id INTEGER PRIMARY KEY,lab_id INTEGER NOT NULL REFERENCES labs(id),start_at INTEGER NOT NULL,end_at INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),capacity INTEGER NOT NULL DEFAULT 1 CHECK(capacity BETWEEN 1 AND 200),UNIQUE(lab_id,start_at),CHECK(end_at=start_at+3600));"
 "CREATE TABLE IF NOT EXISTS reservations(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),slot_id INTEGER NOT NULL REFERENCES slots(id),status TEXT NOT NULL CHECK(status IN('CONFIRMED','CANCELLED')),source TEXT NOT NULL CHECK(source IN('DIRECT','WAITLIST')),created_at INTEGER NOT NULL,cancelled_at INTEGER,checked_in_at INTEGER,cancel_reason TEXT CHECK(cancel_reason IS NULL OR cancel_reason IN('USER','NO_SHOW')));"
 "CREATE INDEX IF NOT EXISTS bookings_slot ON reservations(slot_id,status);"
 "CREATE INDEX IF NOT EXISTS bookings_user ON reservations(user_id,created_at);"
 "CREATE TABLE IF NOT EXISTS waitlist(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),slot_id INTEGER NOT NULL REFERENCES slots(id),status TEXT NOT NULL CHECK(status IN('WAITING','WITHDRAWN','PROMOTED','SKIPPED')),created_at INTEGER NOT NULL,promoted_reservation_id INTEGER REFERENCES reservations(id));"
 "CREATE UNIQUE INDEX IF NOT EXISTS one_waiter ON waitlist(user_id,slot_id) WHERE status='WAITING';"
 "CREATE INDEX IF NOT EXISTS wait_order ON waitlist(slot_id,status,id);"
 "CREATE TABLE IF NOT EXISTS request_receipts(user_id INTEGER NOT NULL REFERENCES users(id),request_id TEXT NOT NULL,action TEXT NOT NULL,payload_digest TEXT NOT NULL,http_status INTEGER NOT NULL,result_json TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(user_id,request_id));"
 "CREATE TABLE IF NOT EXISTS operation_events(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL REFERENCES users(id),action TEXT NOT NULL,entity_id INTEGER NOT NULL,request_id TEXT,created_at INTEGER NOT NULL);"
 "CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL REFERENCES users(id),kind TEXT NOT NULL CHECK(kind IN('PROMOTED','NO_SHOW')),title TEXT NOT NULL,body TEXT NOT NULL,slot_id INTEGER REFERENCES slots(id),reservation_id INTEGER REFERENCES reservations(id),read_at INTEGER,created_at INTEGER NOT NULL);"
 "CREATE INDEX IF NOT EXISTS notify_user ON notifications(user_id,id);"
 "PRAGMA user_version=3;COMMIT;";
 cJSON *wal=db_first(d,"PRAGMA journal_mode=WAL","");int ok=wal&&jstr(wal,"journal_mode")&&!strcmp(jstr(wal,"journal_mode"),"wal");cJSON_Delete(wal);if(!ok)return 0;
 int rc=sqlite3_exec(d->sql,schema,NULL,NULL,NULL);if(rc!=SQLITE_OK)d->error=rc;
 if(d->error)return 0;
 return db_migrate(d);
}
int db_check(DB *d){
 cJSON *r=db_first(d,"PRAGMA integrity_check","");int ok=r&&jstr(r,"integrity_check")&&!strcmp(jstr(r,"integrity_check"),"ok");cJSON_Delete(r);
 cJSON *fk=db_rows(d,"PRAGMA foreign_key_check","");if(!fk||cJSON_GetArraySize(fk))ok=0;cJSON_Delete(fk);
 if(db_num(d,"SELECT count(*) FROM waitlist w JOIN reservations r ON w.user_id=r.user_id AND w.slot_id=r.slot_id WHERE w.status='WAITING' AND r.status='CONFIRMED'","")>0)ok=0;
 if(db_num(d,"SELECT count(*) FROM waitlist w LEFT JOIN reservations r ON r.id=w.promoted_reservation_id WHERE w.status='PROMOTED' AND (r.id IS NULL OR r.user_id!=w.user_id OR r.slot_id!=w.slot_id OR r.source!='WAITLIST')","")>0)ok=0;
 if(db_num(d,"SELECT count(*) FROM slots s WHERE (SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED')>s.capacity","")>0)ok=0;
 if(db_num(d,"SELECT count(*) FROM reservations WHERE checked_in_at IS NOT NULL AND status<>'CONFIRMED'","")>0)ok=0;
 if(db_num(d,"SELECT count(*) FROM reservations WHERE cancel_reason='NO_SHOW' AND (status<>'CANCELLED' OR checked_in_at IS NOT NULL)","")>0)ok=0;
 if(db_num(d,"SELECT count(*) FROM reservations WHERE cancel_reason IS NOT NULL AND status<>'CANCELLED'","")>0)ok=0;
 if(db_num(d,"SELECT count(*) FROM reservations WHERE status='CANCELLED' AND cancelled_at IS NOT NULL AND cancel_reason IS NULL","")>0)ok=0;
 return ok&&!d->error;
}
int publish_slots(DB *d,Id lab,Id start,Id end,Id capacity){ /* capacity 必须 Id：绑定格式 i 按 8 字节变参读取 */
 int count=0;const int hours[]={8,9,10,11,14,15,16,17};
 for(Id day=start;day<=end;day+=86400)for(int h=0;h<8;h++){
  Id s=day+hours[h]*3600;if(s<=now_sec())continue;
  if(!db_run(d,"INSERT INTO slots(lab_id,start_at,end_at,capacity) VALUES(?,?,?,?) ON CONFLICT(lab_id,start_at) DO NOTHING","iiii",lab,s,s+3600,capacity))return count;
  count+=sqlite3_changes(d->sql);
 }return count;
}
int db_seed(DB *d,const char *password){
 if(!password||strlen(password)<8||strlen(password)>128){fprintf(stderr,"LAB_SEED_PASSWORD must contain 8..128 bytes.\n");return 0;}
 char hash[crypto_pwhash_STRBYTES];if(crypto_pwhash_str(hash,password,strlen(password),crypto_pwhash_OPSLIMIT_INTERACTIVE,crypto_pwhash_MEMLIMIT_INTERACTIVE))return 0;
 if(!db_run(d,"BEGIN IMMEDIATE",""))return 0;
 for(int i=0;i<=20;i++){char name[32];if(i)snprintf(name,sizeof name,"user%02d",i);else strcpy(name,"admin");db_run(d,"INSERT INTO users(username,password_hash,role) VALUES(?,?,?) ON CONFLICT(username) DO NOTHING","sss",name,hash,i?"USER":"ADMIN");}
 const char *names[]={"软件工程实验室","计算机网络实验室","系统与数据实验室"};
 for(int i=0;i<3;i++)db_run(d,"INSERT INTO labs(name,location,description) VALUES(?,?,?) ON CONFLICT(name) DO NOTHING","sss",names[i],"信息楼","整间实验室 · 固定一小时场次");
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
