/* Unity 单元测试：纯函数、数据库不变量与预约业务层（不经 HTTP） */
#include "app.h"
#include "unity.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static DB db;
static const char *DBPATH = "build/unit-test.db";

static void remove_files(const char *base){
 char buf[128];
 const char *suffixes[]={"","-wal","-shm"};
 for(size_t i=0;i<sizeof suffixes/sizeof *suffixes;i++){snprintf(buf,sizeof buf,"%s%s",base,suffixes[i]);remove(buf);}
}
static void fresh_seed(void){
 remove_files(DBPATH);
 TEST_ASSERT_TRUE(db_open(&db,DBPATH));
 TEST_ASSERT_TRUE(db_init(&db));
 TEST_ASSERT_TRUE(db_seed(&db,"UnitPassword123!"));
}
static User make_user(const char *name){
 User u={0};cJSON *r=db_first(&db,"SELECT id,username,role FROM users WHERE username=?","s",name);
 TEST_ASSERT_NOT_NULL(r);
 parse_id(jstr(r,"id"),&u.id);u.admin=!strcmp(jstr(r,"role"),"ADMIN");
 snprintf(u.username,sizeof u.username,"%s",name);cJSON_Delete(r);return u;
}
static Id free_slot(void){ /* 最早一个尚无任何预约记录的未来场次 */
 cJSON *r=db_first(&db,"SELECT id FROM slots s WHERE s.start_at>? AND NOT EXISTS(SELECT 1 FROM reservations x WHERE x.slot_id=s.id) ORDER BY s.start_at LIMIT 1","i",now_sec()+3600);
 TEST_ASSERT_NOT_NULL(r);Id id=0;parse_id(jstr(r,"id"),&id);cJSON_Delete(r);return id;
}
static Id slot_field(Id slot,const char *column){
 char sql[128];snprintf(sql,sizeof sql,"SELECT %s FROM slots WHERE id=?",column);
 return db_num(&db,sql,"i",slot);
}
static const char *wait_status(Id id){
 static char buf[16];cJSON *r=db_first(&db,"SELECT status FROM waitlist WHERE id=?","i",id);
 const char *s=jstr(r,"status");snprintf(buf,sizeof buf,"%s",s?s:"?");cJSON_Delete(r);return buf;
}
static const char *rcode(Result r){cJSON *c=cJSON_GetObjectItemCaseSensitive(r.body,"code");return c&&cJSON_IsString(c)?c->valuestring:"";}
static cJSON *rdata(Result r){return cJSON_GetObjectItemCaseSensitive(r.body,"data");}
static Id rid_of(Result r,const char *key){
 Id v=0;cJSON *d=rdata(r);cJSON *c=d?cJSON_GetObjectItemCaseSensitive(d,key):NULL;
 if(c&&cJSON_IsString(c))parse_id(c->valuestring,&v);
 return v;
}
static void drop(Result r){cJSON_Delete(r.body);}
static void new_key(char out[40]){static int n=0;snprintf(out,40,"%08d-0000-4000-8000-000000000000",++n);}
void setUp(void){}
void tearDown(void){}

/* ---------- 纯函数 ---------- */
static void test_parse_id(void){
 Id v=0;
 TEST_ASSERT_TRUE(parse_id("42",&v));TEST_ASSERT_EQUAL_INT64(42,v);
 TEST_ASSERT_TRUE(parse_id("999999999999999999",&v));TEST_ASSERT_EQUAL_INT64(999999999999999999LL,v);
 TEST_ASSERT_FALSE(parse_id("0",&v));
 TEST_ASSERT_FALSE(parse_id("",&v));
 TEST_ASSERT_FALSE(parse_id(NULL,&v));
 TEST_ASSERT_FALSE(parse_id("12x",&v));
 TEST_ASSERT_FALSE(parse_id("-3",&v));
 TEST_ASSERT_FALSE(parse_id(" 1",&v));
 TEST_ASSERT_FALSE(parse_id("1234567890123456789",&v)); /* 19 位拒绝 */
}
static void test_uuid_valid(void){
 TEST_ASSERT_TRUE(uuid_valid("2dd1ab34-26ab-4cf0-a529-7c1f52b4e5d6"));
 TEST_ASSERT_TRUE(uuid_valid("2DD1AB34-26AB-4CF0-A529-7C1F52B4E5D6"));
 TEST_ASSERT_FALSE(uuid_valid("2dd1ab3426ab4cf0a5297c1f52b4e5d6"));
 TEST_ASSERT_FALSE(uuid_valid("2dd1ab34-26ab-4cf0a529-7c1f52b4e5d6"));
 TEST_ASSERT_FALSE(uuid_valid("2dd1ab34-26ab-4cf0-a529-7c1f52b4e5ddd")); /* 37 位 */
 TEST_ASSERT_FALSE(uuid_valid("2dd1ab34-26ab-4cf0-a529-7c1f52b4e5g"));
 TEST_ASSERT_FALSE(uuid_valid(NULL));
}
static void test_date_start(void){
 TEST_ASSERT_EQUAL_INT64(1789056000,date_start("2026-09-11"));
 TEST_ASSERT_EQUAL_INT64(1709136000,date_start("2024-02-29"));
 TEST_ASSERT_EQUAL_INT64(951840000,date_start("2000-03-01"));
 TEST_ASSERT_EQUAL_INT64(-28800,date_start("1970-01-01"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start("2023-02-29"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start("2026-13-01"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start("2026-00-10"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start("2026-09-00"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start("2026/09/11"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start("20260911"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start("abcd-09-11"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start("1969-12-31"));
 TEST_ASSERT_EQUAL_INT64(-1,date_start(NULL));
}
static void test_date_text_roundtrip(void){
 const char *dates[]={"1970-01-02","2000-02-29","2024-02-29","2026-09-11","2026-12-31","2100-02-28"};
 char buf[11];
 for(size_t i=0;i<sizeof dates/sizeof *dates;i++){
  Id v=date_start(dates[i]);TEST_ASSERT_TRUE(v>=0);
  date_text((v+28800)/86400,buf);
  TEST_ASSERT_EQUAL_STRING(dates[i],buf);
 }
 date_text(-5,buf);TEST_ASSERT_EQUAL_STRING("1970-01-01",buf); /* 防御性钳制 */
}
static void test_hash_and_random(void){
 char a[65],b[65],r1[65],r2[65];
 hash_text("abc",a);hash_text("abc",b);
 TEST_ASSERT_EQUAL_STRING(a,b);TEST_ASSERT_EQUAL_INT64(64,(Id)strlen(a));
 for(int i=0;i<64;i++)TEST_ASSERT_TRUE(isxdigit((unsigned char)a[i]));
 hash_text("abd",b);TEST_ASSERT_FALSE(strcmp(a,b)==0);
 random_hex(r1);random_hex(r2);
 TEST_ASSERT_EQUAL_INT64(64,(Id)strlen(r1));TEST_ASSERT_FALSE(strcmp(r1,r2)==0);
}
static void test_result_envelope(void){
 Result r=result(409,"SLOT_FULL","测试",NULL);
 TEST_ASSERT_EQUAL_INT(409,r.status);
 TEST_ASSERT_EQUAL_STRING("SLOT_FULL",rcode(r));
 drop(r);
}

/* ---------- 数据库层 ---------- */
static void test_seed_shape_and_invariants(void){
 TEST_ASSERT_EQUAL_INT64(21,db_num(&db,"SELECT count(*) FROM users",""));
 TEST_ASSERT_EQUAL_INT64(3,db_num(&db,"SELECT count(*) FROM labs",""));
 Id slots=db_num(&db,"SELECT count(*) FROM slots","");
 TEST_ASSERT_TRUE(slots>0&&slots<=3*14*8);
 TEST_ASSERT_EQUAL_INT64(0,db_num(&db,"SELECT count(*) FROM slots WHERE end_at!=start_at+3600",""));
 TEST_ASSERT_EQUAL_INT64(0,db_num(&db,"SELECT count(*) FROM (SELECT lab_id,start_at FROM slots GROUP BY lab_id,start_at HAVING count(*)>1)",""));
 TEST_ASSERT_TRUE(db_check(&db));
}
static void test_unique_booking_index(void){
 Id s=free_slot();User u1=make_user("user01"),u2=make_user("user02");
 TEST_ASSERT_TRUE(db_run(&db,"INSERT INTO reservations(user_id,slot_id,status,source,created_at) VALUES(?,?,'CONFIRMED','DIRECT',?)","iii",u1.id,s,now_sec()));
 TEST_ASSERT_FALSE(db_run(&db,"INSERT INTO reservations(user_id,slot_id,status,source,created_at) VALUES(?,?,'CONFIRMED','DIRECT',?)","iii",u2.id,s,now_sec()));
 TEST_ASSERT_EQUAL_INT(SQLITE_CONSTRAINT,db.error&255); /* 唯一占用索引拦截重复预约 */
 db_run(&db,"DELETE FROM reservations WHERE slot_id=?","i",s);db.error=0;
 TEST_ASSERT_TRUE(db_check(&db));
}
static void test_db_check_rejects_coexistence(void){
 const char *dirty="build/unit-dirty.db";DB d;Id u,lab,s;
 remove_files(dirty);
 TEST_ASSERT_TRUE(db_open(&d,dirty));TEST_ASSERT_TRUE(db_init(&d));
 TEST_ASSERT_TRUE(db_run(&d,"INSERT INTO users(username,password_hash,role) VALUES('x','h','USER')",""));
 u=db_num(&d,"SELECT id FROM users WHERE username='x'","");
 TEST_ASSERT_TRUE(db_run(&d,"INSERT INTO labs(name,location,description) VALUES('脏数据实验室','a','')",""));
 lab=db_num(&d,"SELECT id FROM labs WHERE name='脏数据实验室'","");
 s=(now_sec()/3600+48)*3600;
 TEST_ASSERT_TRUE(db_run(&d,"INSERT INTO slots(lab_id,start_at,end_at) VALUES(?,?,?)","iii",lab,s,s+3600));
 s=sqlite3_last_insert_rowid(d.sql); /* s 改存场次编号 */
 TEST_ASSERT_TRUE(db_run(&d,"INSERT INTO reservations(user_id,slot_id,status,source,created_at) VALUES(?,?,'CONFIRMED','DIRECT',?)","iii",u,s,now_sec()));
 TEST_ASSERT_TRUE(db_run(&d,"INSERT INTO waitlist(user_id,slot_id,status,created_at) VALUES(?,?,'WAITING',?)","iii",u,s,now_sec()));
 TEST_ASSERT_FALSE(db_check(&d)); /* 同一用户同场次既有预约又候补，启动检查必须发现 */
 db_close(&d);remove_files(dirty);
}

/* ---------- 业务层（直接调用 booking，不经 HTTP） ---------- */
static void test_reserve_conflict_and_alternatives(void){
 User u1=make_user("user01"),u2=make_user("user02");
 Id s=free_slot(),lab=slot_field(s,"lab_id"),start=slot_field(s,"start_at");
 char k1[40],k2[40],k3[40],kc[40];new_key(k1);new_key(k2);new_key(k3);new_key(kc);
 Result r=booking(&db,NULL,&u1,"reserve",s,k1);
 TEST_ASSERT_EQUAL_INT(200,r.status);TEST_ASSERT_EQUAL_STRING("OK",rcode(r));
 Id rid=rid_of(r,"reservation_id");TEST_ASSERT_TRUE(rid>0);
 TEST_ASSERT_EQUAL_INT64(1,db_num(&db,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",s));
 drop(r);
 r=booking(&db,NULL,&u2,"reserve",s,k2);
 TEST_ASSERT_EQUAL_INT(409,r.status);TEST_ASSERT_EQUAL_STRING("SLOT_FULL",rcode(r));
 cJSON *alts=cJSON_GetObjectItemCaseSensitive(rdata(r),"alternatives");
 TEST_ASSERT_TRUE(cJSON_IsArray(alts));
 int n=cJSON_GetArraySize(alts);TEST_ASSERT_TRUE(n>=1&&n<=3);
 Id prev=0;
 for(cJSON *it=alts->child;it;it=it->next){ /* 同实验室、更晚、7 天内、空闲、按时间升序 */
  Id aid=0;parse_id(jstr(it,"id"),&aid);TEST_ASSERT_TRUE(aid>0);
  TEST_ASSERT_EQUAL_INT64(lab,slot_field(aid,"lab_id"));
  Id at=slot_field(aid,"start_at");TEST_ASSERT_TRUE(at>start&&at<=start+7*86400);
  if(prev)TEST_ASSERT_TRUE(at>prev);
  prev=at;
  TEST_ASSERT_EQUAL_INT64(0,db_num(&db,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",aid));
 }
 char *first=cJSON_PrintUnformatted(r.body);drop(r);
 Result again=booking(&db,NULL,&u2,"reserve",s,k2); /* 同编号同参数重放：返回原结果 */
 char *second=cJSON_PrintUnformatted(again.body);
 TEST_ASSERT_EQUAL_STRING(first,second);
 cJSON_free(first);cJSON_free(second);drop(again);
 r=booking(&db,NULL,&u2,"reserve",s,k3); /* 新编号同场次：仍满，属新的失败请求 */
 TEST_ASSERT_EQUAL_INT(409,r.status);drop(r);
 drop(booking(&db,NULL,&u1,"cancel",rid,kc)); /* 取消使用新编号；释放该场次 */
 TEST_ASSERT_EQUAL_INT64(0,db_num(&db,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",s));
}
static void test_wait_guards(void){
 User u1=make_user("user01");
 Id s=free_slot();char k[3][40];for(int i=0;i<3;i++)new_key(k[i]);
 Result r=booking(&db,NULL,&u1,"wait",s,k[0]); /* 空闲场次禁止候补 */
 TEST_ASSERT_EQUAL_INT(409,r.status);TEST_ASSERT_EQUAL_STRING("SLOT_AVAILABLE",rcode(r));drop(r);
 r=booking(&db,NULL,&u1,"reserve",s,k[1]);TEST_ASSERT_EQUAL_INT(200,r.status);drop(r);
 r=booking(&db,NULL,&u1,"wait",s,k[2]); /* 已预约者禁止再候补 */
 TEST_ASSERT_EQUAL_INT(409,r.status);TEST_ASSERT_EQUAL_STRING("ALREADY_RESERVED",rcode(r));drop(r);
}
static void test_wait_queue_idempotent_reentry(void){
 User u1=make_user("user01"),u2=make_user("user02"),u3=make_user("user03");
 Id s=free_slot();char k[6][40];for(int i=0;i<6;i++)new_key(k[i]);
 drop(booking(&db,NULL,&u1,"reserve",s,k[0]));
 Result r=booking(&db,NULL,&u2,"wait",s,k[1]);TEST_ASSERT_EQUAL_INT(200,r.status);
 Id w2=rid_of(r,"waitlist_id");TEST_ASSERT_TRUE(w2>0);drop(r);
 r=booking(&db,NULL,&u2,"wait",s,k[1]); /* 同编号重放：同一候补编号 */
 TEST_ASSERT_EQUAL_INT(200,r.status);TEST_ASSERT_EQUAL_INT64(w2,rid_of(r,"waitlist_id"));drop(r);
 r=booking(&db,NULL,&u3,"wait",s,k[2]);TEST_ASSERT_EQUAL_INT(200,r.status);
 Id w3=rid_of(r,"waitlist_id");TEST_ASSERT_TRUE(w3>w2); /* 入队顺序 */
 drop(r);
 r=booking(&db,NULL,&u2,"withdraw",w2,k[3]);TEST_ASSERT_EQUAL_INT(200,r.status);drop(r);
 TEST_ASSERT_EQUAL_STRING("WITHDRAWN",wait_status(w2));
 r=booking(&db,NULL,&u2,"wait",s,k[4]); /* 重新入队排在队尾 */
 Id w2b=rid_of(r,"waitlist_id");TEST_ASSERT_TRUE(w2b>w3);drop(r);
 Id rid1=db_num(&db,"SELECT id FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",s);
 r=booking(&db,NULL,&u1,"cancel",rid1,k[5]); /* 取消触发 FIFO 补位 */
 TEST_ASSERT_EQUAL_INT(200,r.status);
 Id promoted=rid_of(r,"promoted_reservation_id");TEST_ASSERT_TRUE(promoted>0);
 TEST_ASSERT_EQUAL_INT64(db_num(&db,"SELECT user_id FROM reservations WHERE id=?","i",promoted),u3.id);
 TEST_ASSERT_EQUAL_STRING("PROMOTED",wait_status(w3));
 TEST_ASSERT_EQUAL_STRING("WAITING",wait_status(w2b));
 TEST_ASSERT_EQUAL_INT64(1,db_num(&db,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",s));
 drop(r);
}
static void test_stats_totals_match_sql(void){
 Id day0=(now_sec()+28800)/86400,start=day0*86400-28800,end=start+13*86400;
 Result r=stats(&db,start,end);
 TEST_ASSERT_EQUAL_INT(200,r.status);TEST_ASSERT_EQUAL_STRING("OK",rcode(r));
 cJSON *d=rdata(r),*t=cJSON_GetObjectItemCaseSensitive(d,"totals"),*rows=cJSON_GetObjectItemCaseSensitive(d,"stats");
 TEST_ASSERT_TRUE(cJSON_IsObject(t)&&cJSON_IsArray(rows));
 int n=cJSON_GetArraySize(rows);TEST_ASSERT_TRUE(n>=1&&n<=14);
 TEST_ASSERT_EQUAL_INT64(db_num(&db,"SELECT count(*) FROM slots WHERE start_at>=? AND start_at<?","ii",start,end+86400),(Id)cJSON_GetObjectItemCaseSensitive(t,"slots")->valuedouble);
 TEST_ASSERT_EQUAL_INT64(db_num(&db,"SELECT count(*) FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE s.start_at>=? AND s.start_at<? AND r.status='CONFIRMED'","ii",start,end+86400),(Id)cJSON_GetObjectItemCaseSensitive(t,"confirmed")->valuedouble);
 TEST_ASSERT_EQUAL_INT64(db_num(&db,"SELECT count(*) FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE s.start_at>=? AND s.start_at<? AND r.status='CANCELLED'","ii",start,end+86400),(Id)cJSON_GetObjectItemCaseSensitive(t,"cancelled")->valuedouble);
 TEST_ASSERT_EQUAL_INT64(db_num(&db,"SELECT count(*) FROM waitlist w JOIN slots s ON s.id=w.slot_id JOIN users u ON u.id=w.user_id WHERE s.start_at>=? AND s.start_at<? AND w.status='WAITING' AND u.enabled=1","ii",start,end+86400),(Id)cJSON_GetObjectItemCaseSensitive(t,"waiting")->valuedouble);
 for(cJSON *it=rows->child;it;it=it->next){ /* 逐行日期与 SQL 对账 */
  const char *date=jstr(it,"date");TEST_ASSERT_TRUE(date&&strlen(date)==10);
  Id ds=date_start(date);TEST_ASSERT_TRUE(ds>=start&&ds<=end);
  TEST_ASSERT_EQUAL_INT64(db_num(&db,"SELECT count(*) FROM slots WHERE start_at>=? AND start_at<?","ii",ds,ds+86400),(Id)cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble);
 }
 drop(r);
}

/* ---------- 新增功能：签到、通知、会话、改密 ---------- */
static void test_hex_and_page_args(void){
 TEST_ASSERT_TRUE(hex_token("0123456789abcdef",16));
 TEST_ASSERT_TRUE(hex_token("ABCDEF0123456789",16));
 TEST_ASSERT_FALSE(hex_token("0123456789abcde",16));
 TEST_ASSERT_FALSE(hex_token("0123456789abcdeg",16));
 TEST_ASSERT_FALSE(hex_token(NULL,16));
 TEST_ASSERT_FALSE(hex_token("",16));
 TEST_ASSERT_EQUAL_INT(7,page_arg("7",1,1,100));
 TEST_ASSERT_EQUAL_INT(1,page_arg(NULL,1,1,100));
 TEST_ASSERT_EQUAL_INT(1,page_arg("",1,1,100));
 TEST_ASSERT_EQUAL_INT(-1,page_arg("0",1,1,100));
 TEST_ASSERT_EQUAL_INT(-1,page_arg("101",1,1,100));
 TEST_ASSERT_EQUAL_INT(-1,page_arg("abc",1,1,100));
}
static void test_checkin_business(void){
 User u1=make_user("user01"),u2=make_user("user02");
 Id s=free_slot();char k[40];
 new_key(k);Result r=booking(&db,NULL,&u1,"reserve",s,k);TEST_ASSERT_EQUAL_INT(200,r.status);
 Id rid=rid_of(r,"reservation_id");drop(r);
 new_key(k);r=booking(&db,NULL,&u1,"checkin",rid,k);TEST_ASSERT_EQUAL_INT(409,r.status);drop(r); /* 未开始 */
 db_run(&db,"UPDATE slots SET start_at=?,end_at=? WHERE id=?","iii",now_sec()-1,now_sec()+3599,s);
 new_key(k);r=booking(&db,NULL,&u1,"checkin",rid,k);TEST_ASSERT_EQUAL_INT(200,r.status);
 cJSON *ci=cJSON_GetObjectItemCaseSensitive(rdata(r),"checked_in_at");
 TEST_ASSERT_TRUE(cJSON_IsNumber(ci));Id when=(Id)ci->valuedouble;TEST_ASSERT_TRUE(when>0);drop(r);
 TEST_ASSERT_EQUAL_INT64(when,db_num(&db,"SELECT checked_in_at FROM reservations WHERE id=?","i",rid));
 new_key(k);r=booking(&db,NULL,&u1,"checkin",rid,k);TEST_ASSERT_EQUAL_INT(200,r.status); /* 重复签到幂等 */
 cJSON *ci2=cJSON_GetObjectItemCaseSensitive(rdata(r),"checked_in_at");TEST_ASSERT_TRUE(cJSON_IsNumber(ci2));TEST_ASSERT_EQUAL_INT64(when,(Id)ci2->valuedouble);drop(r);
 new_key(k);r=booking(&db,NULL,&u2,"checkin",rid,k);TEST_ASSERT_EQUAL_INT(403,r.status);drop(r); /* 非本人 */
 Config cfg={"build/unit-test.db",NULL,0,900,30,NULL,NULL};
 TEST_ASSERT_EQUAL_INT(0,sweep_once(&cfg)); /* 已签到不被判爽约 */
 TEST_ASSERT_EQUAL_INT64(1,db_num(&db,"SELECT count(*) FROM reservations WHERE id=? AND status='CONFIRMED' AND checked_in_at IS NOT NULL","i",rid));
}
static void test_notify_sessions_and_password(void){
 User u1=make_user("user01");char k[40];
 Id s=free_slot();
 new_key(k);Result rr=booking(&db,NULL,&u1,"reserve",s,k);TEST_ASSERT_EQUAL_INT(200,rr.status);
 Id rid=rid_of(rr,"reservation_id");drop(rr);
 notify(&db,u1.id,"PROMOTED","候补补位成功","你的候补已补位，请按时签到。",s,rid);
 Result r=notifications(&db,&u1,1,1,20);TEST_ASSERT_EQUAL_INT(200,r.status);
 TEST_ASSERT_EQUAL_INT(1,cJSON_GetArraySize(cJSON_GetObjectItemCaseSensitive(rdata(r),"notifications")));
 TEST_ASSERT_EQUAL_INT(1,(int)cJSON_GetObjectItemCaseSensitive(rdata(r),"unread_count")->valuedouble);drop(r);
 cJSON *body=cJSON_CreateObject();cJSON_AddBoolToObject(body,"all",1);
 new_key(k);r=notifications_read(&db,&u1,body,k);TEST_ASSERT_EQUAL_INT(200,r.status);drop(r);cJSON_Delete(body);
 r=notifications(&db,&u1,1,1,20);TEST_ASSERT_EQUAL_INT(0,(int)cJSON_GetObjectItemCaseSensitive(rdata(r),"unread_count")->valuedouble);drop(r);
 /* 会话列表：曾因绑定格式串与占位符不匹配而崩溃，此处保留回归保护 */
 TEST_ASSERT_TRUE(db_run(&db,"INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)","sisii","unit-token",u1.id,"unit-csrf",now_sec()+3600,now_sec()));
 r=sessions_list(&db,&u1,"unit-token");TEST_ASSERT_EQUAL_INT(200,r.status);
 cJSON *list=cJSON_GetObjectItemCaseSensitive(rdata(r),"sessions");TEST_ASSERT_EQUAL_INT(1,cJSON_GetArraySize(list));
 TEST_ASSERT_EQUAL_STRING("unit-token",jstr(list->child,"id"));
 TEST_ASSERT_TRUE(cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(list->child,"current")));drop(r);
 new_key(k);r=session_revoke(&db,&u1,"unit-token",k);TEST_ASSERT_EQUAL_INT(200,r.status);drop(r);
 new_key(k);r=session_revoke(&db,&u1,"unit-token",k);TEST_ASSERT_EQUAL_INT(404,r.status);drop(r);
 new_key(k);r=password_change(&db,&u1,"wrong-password","NewPassword123!",NULL,k);TEST_ASSERT_EQUAL_INT(401,r.status);drop(r);
 new_key(k);r=password_change(&db,&u1,"UnitPassword123!","short",NULL,k);TEST_ASSERT_EQUAL_INT(400,r.status);drop(r);
 new_key(k);r=password_change(&db,&u1,"UnitPassword123!","UnitPassword123!",NULL,k);TEST_ASSERT_EQUAL_INT(400,r.status);drop(r);
 TEST_ASSERT_TRUE(db_run(&db,"INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)","sisii","keep-me",u1.id,"c2",now_sec()+3600,now_sec()));
 TEST_ASSERT_TRUE(db_run(&db,"INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)","sisii","drop-me",u1.id,"c3",now_sec()+3600,now_sec()));
 new_key(k);r=password_change(&db,&u1,"UnitPassword123!","NewPassword123!","keep-me",k);TEST_ASSERT_EQUAL_INT(200,r.status);
 TEST_ASSERT_EQUAL_INT(1,(int)cJSON_GetObjectItemCaseSensitive(rdata(r),"revoked_sessions")->valuedouble);drop(r);
 TEST_ASSERT_EQUAL_INT64(0,db_num(&db,"SELECT count(*) FROM sessions WHERE token_hash='drop-me'",""));
 TEST_ASSERT_EQUAL_INT64(1,db_num(&db,"SELECT count(*) FROM sessions WHERE token_hash='keep-me'",""));
 new_key(k);r=password_change(&db,&u1,"NewPassword123!","UnitPassword123!","keep-me",k);TEST_ASSERT_EQUAL_INT(200,r.status);drop(r); /* 复原口令 */
}
int main(void){
 setvbuf(stdout,NULL,_IONBF,0); /* 崩溃时也能看到已完成用例，便于定位 */
 UnityBegin("tests/unit.c");
 fresh_seed();
 RUN_TEST(test_parse_id);
 RUN_TEST(test_uuid_valid);
 RUN_TEST(test_date_start);
 RUN_TEST(test_date_text_roundtrip);
 RUN_TEST(test_hash_and_random);
 RUN_TEST(test_result_envelope);
 RUN_TEST(test_seed_shape_and_invariants);
 RUN_TEST(test_unique_booking_index);
 RUN_TEST(test_wait_guards);
 RUN_TEST(test_reserve_conflict_and_alternatives);
 RUN_TEST(test_wait_queue_idempotent_reentry);
 RUN_TEST(test_stats_totals_match_sql);
 RUN_TEST(test_hex_and_page_args);
 RUN_TEST(test_checkin_business);
 RUN_TEST(test_notify_sessions_and_password);
 RUN_TEST(test_db_check_rejects_coexistence);
 db_close(&db);remove_files(DBPATH);
 return UnityEnd();
}
