#include "app.h"
#include <stdarg.h>
#include <string.h>
#include <stdio.h>
#include <sodium.h>
static sqlite3_stmt *prepare(DB *d,const char *sql,const char *fmt,va_list a){
 sqlite3_stmt *s=NULL; int rc=sqlite3_prepare_v2(d->sql,sql,-1,&s,NULL);
 if(rc!=SQLITE_OK){ d->error=rc;return NULL; }
 for(int i=0;fmt&&fmt[i];i++){
  if(fmt[i]=='i')rc=sqlite3_bind_int64(s,i+1,va_arg(a,Id));
  else if(fmt[i]=='s'){const char *v=va_arg(a,const char*);rc=v?sqlite3_bind_text(s,i+1,v,-1,SQLITE_TRANSIENT):sqlite3_bind_null(s,i+1);}
  else rc=SQLITE_MISUSE;
  if(rc!=SQLITE_OK){d->error=rc;sqlite3_finalize(s);return NULL;}
 }
 return s;
}
int db_open(DB *d,const char *p){
 memset(d,0,sizeof *d);int rc=sqlite3_open_v2(p,&d->sql,SQLITE_OPEN_READWRITE|SQLITE_OPEN_CREATE|SQLITE_OPEN_FULLMUTEX,NULL);
 if(rc!=SQLITE_OK){d->error=rc;return 0;}
 sqlite3_extended_result_codes(d->sql,1);sqlite3_busy_timeout(d->sql,3000);
 db_run(d,"PRAGMA foreign_keys=ON","");db_run(d,"PRAGMA synchronous=FULL","");return !d->error;
}
void db_close(DB *d){if(d->sql){if(!sqlite3_get_autocommit(d->sql))sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);sqlite3_close(d->sql);}d->sql=NULL;}
int db_run(DB *d,const char *sql,const char *fmt,...){
 va_list a;va_start(a,fmt);sqlite3_stmt *s=prepare(d,sql,fmt,a);va_end(a);if(!s)return 0;
 int rc;do{rc=sqlite3_step(s);}while(rc==SQLITE_ROW);if(rc!=SQLITE_DONE)d->error=rc;
 int fr=sqlite3_finalize(s);if(fr!=SQLITE_OK)d->error=fr;return rc==SQLITE_DONE&&fr==SQLITE_OK;
}
Id db_num(DB *d,const char *sql,const char *fmt,...){
 va_list a;va_start(a,fmt);sqlite3_stmt *s=prepare(d,sql,fmt,a);va_end(a);if(!s)return 0;
 int rc=sqlite3_step(s);Id n=0;if(rc==SQLITE_ROW)n=sqlite3_column_int64(s,0);else if(rc!=SQLITE_DONE)d->error=rc;
 sqlite3_finalize(s);return n;
}
static cJSON *rowsv(DB *d,const char *sql,const char *fmt,va_list a){
 sqlite3_stmt *s=prepare(d,sql,fmt,a);if(!s)return NULL;cJSON *rows=cJSON_CreateArray();if(!rows){sqlite3_finalize(s);d->error=SQLITE_NOMEM;return NULL;}
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
 if(rc!=SQLITE_DONE&&!d->error)d->error=rc;
 sqlite3_finalize(s);if(d->error){cJSON_Delete(rows);return NULL;}return rows;
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
