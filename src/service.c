#include "app.h"
#include <sodium.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
/* -- r12 业务规则常量 -- */
#define NO_SHOW_GRACE 2      /* 窗口内第 2 次爽约起触发限制 */
#define PENALTY_DAYS 7       /* 限制时长：从触发爽约的场次时间起算 */
#define ARCHIVE_DAYS 30      /* 候补/请求回执保留天数（过期归档清理） */
static void event(DB *d,Id actor,const char *action,Id entity,const char *request){db_run(d,"INSERT INTO operation_events(actor_id,action,entity_id,request_id,created_at) VALUES(?,?,?,?,?)","isisi",actor,action,entity,request,now_sec());}
int notify(DB *d,Id user,const char *kind,const char *title,const char *body,Id slot,Id reservation){return db_run(d,"INSERT INTO notifications(user_id,kind,title,body,slot_id,reservation_id,created_at) VALUES(?,?,?,?,?,?,?)","isssiii",user,kind,title,body,slot,reservation,now_sec());}
static void slot_when(DB *d,Id slot,char out[40]){
 Id st=db_num(d,"SELECT start_at FROM slots WHERE id=?","i",slot);
 if(!st){snprintf(out,40,"场次 %lld",(long long)slot);return;}
 Id bj=st+28800;char day[11];date_text(bj/86400,day);
 snprintf(out,40,"%s %02d:%02d",day,(int)((bj%86400)/3600),(int)((bj%3600)/60));
}
static void fault(const Config *c,const char *stage,const char *key){
#ifdef TEST_FAULTS
 if(c->fault&&!strcmp(c->fault,stage)){
  if(!strcmp(stage,"sweep-mid"))_Exit(88); /* 扫描事务中途崩溃：验证回收+补位的原子性 */
  if(c->fault_request&&key&&!strcmp(c->fault_request,key))_Exit(!strcmp(stage,"cancel-before-promote")?86:87);
 }
#else
 (void)c;(void)stage;(void)key;
#endif
}
static Id promote(DB *d,Id slot,Id actor,const char *key){
 db_run(d,"UPDATE waitlist SET status='SKIPPED' WHERE slot_id=? AND status='WAITING' AND user_id IN(SELECT id FROM users WHERE enabled=0)","i",slot);
 cJSON *w=db_first(d,"SELECT id,user_id FROM waitlist WHERE slot_id=? AND status='WAITING' ORDER BY id LIMIT 1","i",slot);
 if(!w)return 0;
 Id wid=0,uid=0;parse_id(jstr(w,"id"),&wid);parse_id(jstr(w,"user_id"),&uid);cJSON_Delete(w);
 if(!db_run(d,"INSERT INTO reservations(user_id,slot_id,status,source,created_at) VALUES(?,?,'CONFIRMED','WAITLIST',?)","iii",uid,slot,now_sec()))return 0;
 Id rid=sqlite3_last_insert_rowid(d->sql);
 db_run(d,"UPDATE waitlist SET status='PROMOTED',promoted_reservation_id=? WHERE id=?","ii",rid,wid);event(d,actor,"PROMOTE",rid,key);
 char when[40],body[192];slot_when(d,slot,when);snprintf(body,sizeof body,"你候补的场次 %s 已补位成功，预约编号 %lld，请按时到场并在签到时间内签到。",when,(long long)rid);
 notify(d,uid,"PROMOTED","候补补位成功",body,slot,rid);
 return rid;
}
/* 按容量反复补位直至满员或队列空；返回首个新增预约编号（无则 0）。 */
static Id promote_fill(DB *d,Id slot,Id actor,const char *key){
 Id first=0;
 for(;;){
  Id capacity=db_num(d,"SELECT capacity FROM slots WHERE id=?","i",slot);
  if(d->error)return first;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",slot)>=capacity)break;
  Id rid=promote(d,slot,actor,key);
  if(d->error)return first;
  if(!rid)break;
  if(!first)first=rid;
 }
 return first;
}
static Result ok_id(const char *field,Id value){cJSON *j=cJSON_CreateObject();jid(j,field,value);return result(200,"OK","操作成功",j);}
static Result slot_full(DB *d,Id slot,const char *message){
 cJSON *list=db_rows(d,"SELECT s.id,s.start_at,s.end_at FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.lab_id=(SELECT lab_id FROM slots WHERE id=?) AND s.start_at>(SELECT start_at FROM slots WHERE id=?) AND s.start_at<=(SELECT start_at FROM slots WHERE id=?)+604800 AND s.enabled=1 AND l.enabled=1 AND (SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED')<s.capacity ORDER BY s.start_at LIMIT 3","iii",slot,slot,slot);
 if(d->error)return db_failure(d);
 cJSON *data=cJSON_CreateObject();if(!data){cJSON_Delete(list);d->error=SQLITE_NOMEM;return db_failure(d);}
 cJSON_AddItemToObject(data,"alternatives",list);return result(409,"SLOT_FULL",message,data);
}
Result booking(DB *d,const Config *cfg,const User *u,const char *action,Id target,const cJSON *body,const char *key){
 Result r={500,NULL};cJSON *old=NULL,*row=NULL,*slot=NULL;char canonical[224],digest[65];Id sid=target;
 snprintf(canonical,sizeof canonical,"%s:%lld",action,(long long)target);
 /* r14：预约可声明所需资源，资源列表纳入请求摘要（同编号异参数仍为冲突） */
 cJSON *claim_arr=NULL;
 if(!strcmp(action,"reserve")&&body){
  cJSON *arr=cJSON_GetObjectItemCaseSensitive(body,"assets");
  if(arr&&cJSON_IsArray(arr)&&cJSON_GetArraySize(arr)){
   if(cJSON_GetArraySize(arr)>5)return result(400,"INVALID_INPUT","最多声明 5 项资源",NULL);
   claim_arr=arr;
   char *ser=cJSON_PrintUnformatted(arr);
   if(!ser)return result(500,"INTERNAL_ERROR","内存不足",NULL);
   size_t len=strlen(canonical);
   snprintf(canonical+len,sizeof canonical-len,"|%s",ser);
   cJSON_free(ser);
  }
 }
 hash_text(canonical,digest);
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 if(!db_num(d,"SELECT enabled FROM users WHERE id=?","i",u->id)){r=result(401,"UNAUTHORIZED","账号不可用",NULL);goto finish_nosave;}
 old=db_first(d,"SELECT action,payload_digest,http_status,result_json FROM request_receipts WHERE user_id=? AND request_id=?","is",u->id,key);
 if(d->error)goto failed;
 if(old){
  if(strcmp(jstr(old,"action"),action)||strcmp(jstr(old,"payload_digest"),digest))r=result(409,"REQUEST_ID_CONFLICT","请求编号已用于其他操作",NULL);
  else {r.status=cJSON_GetObjectItemCaseSensitive(old,"http_status")->valueint;r.body=cJSON_Parse(jstr(old,"result_json"));if(!r.body){d->error=SQLITE_CORRUPT;goto failed;}}
  goto finish_nosave;
 }
 if(!strcmp(action,"cancel")||!strcmp(action,"withdraw")||!strcmp(action,"checkin")){
  const char *sel=!strcmp(action,"withdraw")?"SELECT user_id,slot_id,status FROM waitlist WHERE id=?":!strcmp(action,"checkin")?"SELECT user_id,slot_id,status,checked_in_at,source,created_at FROM reservations WHERE id=?":"SELECT user_id,slot_id,status FROM reservations WHERE id=?";
  row=db_first(d,sel,"i",target);
  if(d->error)goto failed;
  if(!row){r=result(404,"NOT_FOUND","记录不存在",NULL);goto save;}
  Id owner=0;parse_id(jstr(row,"user_id"),&owner);parse_id(jstr(row,"slot_id"),&sid);
  if(owner!=u->id){r=result(403,"FORBIDDEN","只能操作自己的记录",NULL);goto save;}
 }
 slot=db_first(d,"SELECT s.id,s.start_at,s.enabled,s.capacity,l.enabled AS lab_enabled FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.id=?","i",sid);
 if(d->error)goto failed;
 if(!slot){r=result(404,"NOT_FOUND","场次不存在",NULL);goto save;}
 Id start_at=(Id)cJSON_GetObjectItemCaseSensitive(slot,"start_at")->valuedouble;
 if(!strcmp(action,"checkin")){
  Id at=now_sec();Id window=cfg?cfg->checkin_window:900;Id begin=start_at;
  if(jstr(row,"source")&&!strcmp(jstr(row,"source"),"WAITLIST")){
   cJSON *cr=cJSON_GetObjectItemCaseSensitive(row,"created_at");
   if(cr&&cJSON_IsNumber(cr)&&(Id)cr->valuedouble>begin)begin=(Id)cr->valuedouble;
  }
  if(at<begin){r=result(409,"STATE_CONFLICT","场次尚未开始，暂不能签到",NULL);goto save;}
  if(at>=begin+window){r=result(409,"STATE_CONFLICT","签到时间已过，不能补签",NULL);goto save;}
 }else if(start_at<=now_sec()) {r=result(409,"STATE_CONFLICT","场次已开始，不能再修改",NULL);goto save;}
 if((!strcmp(action,"reserve")||!strcmp(action,"wait"))&&(!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(slot,"enabled"))||!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(slot,"lab_enabled")))){
  r=result(409,"STATE_CONFLICT","场次未开放",NULL);goto save;
 }
 if(!strcmp(action,"reserve")){
  Id capacity=(Id)cJSON_GetObjectItemCaseSensitive(slot,"capacity")->valuedouble;
  Id taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",sid);
  if(d->error)goto failed;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=? AND status='CONFIRMED'","ii",sid,u->id)){r=result(409,"ALREADY_RESERVED","你已预约该场次",NULL);goto save;}
  if(d->error)goto failed;
  /* 爽约信用：窗口内爽约达 NO_SHOW_GRACE 次，PENALTY_DAYS 内禁止新的预约与候补 */
  {
   Id window_start=now_sec()-(Id)PENALTY_DAYS*86400;
   Id strikes=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=? AND r.cancel_reason='NO_SHOW' AND s2.start_at>=?","ii",u->id,window_start);
   if(d->error)goto failed;
   if(strikes>=NO_SHOW_GRACE){
    char until[11];date_text((window_start+86400*(Id)PENALTY_DAYS+28800)/86400,until);
    char msg[128];snprintf(msg,sizeof msg,"近 %d 天内爽约 %d 次，预约受限至 %s，如有疑问请联系管理员",PENALTY_DAYS,(int)strikes,until);
    r=result(409,"PENALTY_ACTIVE",msg,NULL);goto save;
   }
  }
  /* 时段重叠：同一用户不能预约/候补时间重叠的两个场次 */
  {
   Id overlap=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id JOIN slots s ON s.id=? WHERE r.user_id=? AND r.status='CONFIRMED' AND s.start_at<s2.end_at AND s2.start_at<s.end_at","ii",sid,u->id);
   if(d->error)goto failed;
   if(overlap){r=result(409,"TIME_CONFLICT","与您已预约的场次时间重叠，请先取消原场次",NULL);goto save;}
  }
  /* r16 BR13 每周配额：本周（北京周一 0 点起）有效预约数达上限则拒绝（候补不入配额，补位为 FIFO 公平结果） */
  if(cfg&&cfg->quota_weekly>0){
   Id day0=(now_sec()+28800)/86400*86400-28800;
   int wd=(int)(((day0+28800)/86400+3)%7); /* 0=周一 */
   Id week_start=day0-(Id)wd*86400;
   Id used=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=? AND r.status='CONFIRMED' AND s2.start_at>=?","ii",u->id,week_start);
   if(d->error)goto failed;
   if(used>=(Id)cfg->quota_weekly){
    char msg[96];snprintf(msg,sizeof msg,"本周预约已达上限 %d 场，下周一 0 点后重试",(int)cfg->quota_weekly);
    r=result(409,"WEEKLY_QUOTA",msg,NULL);goto save;
   }
  }
  if(taken>=capacity){r=slot_full(d,sid,"该场次已约满，可加入候补或选择替代时段");goto save;}
  promote_fill(d,sid,u->id,key);if(d->error)goto failed;
  taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",sid);
  if(d->error)goto failed;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=? AND status='CONFIRMED'","ii",sid,u->id)){r=slot_full(d,sid,"你已通过候补补位获得该场次，可在我的记录查看");goto save;}
  if(taken>=capacity){r=slot_full(d,sid,"名额已按顺序分配给候补用户，可选择替代时段");goto save;}
  /* r14 资源声明：先在事务内校验归属/可用性与时段配额（BR12），通过后再创建预约并落 claims */
  if(claim_arr){
   Id slot_end=start_at+3600;
   cJSON *it;int n=0;
   cJSON_ArrayForEach(it,claim_arr){
    if(n>=5)break;n++;
    Id aid=0;const char *sv=cJSON_IsString(it)?cJSON_GetStringValue(it):NULL;
    if(!sv||!parse_id(sv,&aid)||aid<1){r=result(400,"INVALID_INPUT","资源编号不合法",NULL);goto save;}
    if(!db_num(d,"SELECT count(*) FROM assets WHERE id=? AND lab_id=(SELECT lab_id FROM slots WHERE id=?) AND status='AVAILABLE'","ii",aid,sid)){
     r=result(409,"STATE_CONFLICT","所声明的资源不存在、已停用或不属于该实验室",NULL);goto save;}
    Id total=db_num(d,"SELECT total FROM assets WHERE id=?","i",aid);
    Id used=db_num(d,"SELECT count(*) FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id JOIN slots s2 ON s2.id=r.slot_id WHERE c.asset_id=? AND r.status='CONFIRMED' AND s2.start_at<? AND s2.end_at>?",
                      "iii",aid,slot_end,start_at);
    if(d->error)goto failed;
    if(used>=total){r=result(409,"ASSET_QUOTA","该时段所声明的资源已被约满，可减少资源或改约其他时段",NULL);goto save;}
   }
  }
  if(!db_run(d,"INSERT INTO reservations(user_id,slot_id,status,source,created_at) VALUES(?,?,'CONFIRMED','DIRECT',?)","iii",u->id,sid,now_sec()))goto failed;
  Id rid=sqlite3_last_insert_rowid(d->sql);
  if(claim_arr){
   cJSON *it2;cJSON_ArrayForEach(it2,claim_arr){
    const char *sv2=cJSON_IsString(it2)?cJSON_GetStringValue(it2):NULL;Id aid2=0;
    if(!sv2||!parse_id(sv2,&aid2))continue;
    if(!db_run(d,"INSERT OR IGNORE INTO asset_claims(reservation_id,asset_id,created_at) VALUES(?,?,?)","iii",rid,aid2,now_sec()))goto failed;
   }
  }
  event(d,u->id,"RESERVE",rid,key);r=ok_id("reservation_id",rid);
 }else if(!strcmp(action,"wait")){
  Id capacity=(Id)cJSON_GetObjectItemCaseSensitive(slot,"capacity")->valuedouble;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=? AND status='CONFIRMED'","ii",sid,u->id)){r=result(409,"ALREADY_RESERVED","你已预约该场次",NULL);goto save;}
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",sid)<capacity){r=result(409,"SLOT_AVAILABLE","场次尚有余位，请直接预约",NULL);goto save;}
  if(d->error)goto failed;
  /* 候补同样受爽约信用与时间重叠约束（补位成功即占用该时段） */
  {
   Id window_start=now_sec()-(Id)PENALTY_DAYS*86400;
   Id strikes=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=? AND r.cancel_reason='NO_SHOW' AND s2.start_at>=?","ii",u->id,window_start);
   if(d->error)goto failed;
   if(strikes>=NO_SHOW_GRACE){r=result(409,"PENALTY_ACTIVE","爽约次数过多，预约受限，如有疑问请联系管理员",NULL);goto save;}
   Id overlap=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id JOIN slots s ON s.id=? WHERE r.user_id=? AND r.status='CONFIRMED' AND s.start_at<s2.end_at AND s2.start_at<s.end_at","ii",sid,u->id);
   if(d->error)goto failed;
   if(overlap){r=result(409,"TIME_CONFLICT","与您已预约的场次时间重叠，不能加入候补",NULL);goto save;}
  }
  Id wid=db_num(d,"SELECT id FROM waitlist WHERE user_id=? AND slot_id=? AND status='WAITING'","ii",u->id,sid);
  if(!wid){db_run(d,"INSERT INTO waitlist(user_id,slot_id,status,created_at) VALUES(?,?,'WAITING',?)","iii",u->id,sid,now_sec());wid=sqlite3_last_insert_rowid(d->sql);event(d,u->id,"WAIT",wid,key);}
  r=ok_id("waitlist_id",wid);
 }else if(!strcmp(action,"cancel")){
  Id promoted=0;
  if(!strcmp(jstr(row,"status"),"CONFIRMED")){
   if(!db_run(d,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='USER' WHERE id=?","ii",now_sec(),target))goto failed;
   fault(cfg,"cancel-before-promote",key);event(d,u->id,"CANCEL",target,key);promoted=promote_fill(d,sid,u->id,key);
  }
  cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);if(promoted)jid(j,"promoted_reservation_id",promoted);else cJSON_AddNullToObject(j,"promoted_reservation_id");r=result(200,"OK","预约已取消",j);
 }else if(!strcmp(action,"withdraw")){
  if(!strcmp(jstr(row,"status"),"WAITING")){db_run(d,"UPDATE waitlist SET status='WITHDRAWN' WHERE id=?","i",target);event(d,u->id,"WITHDRAW",target,key);}
  else if(strcmp(jstr(row,"status"),"WITHDRAWN")){r=result(409,"STATE_CONFLICT","候补已结束，若已补位请取消对应预约",NULL);goto save;}
  r=ok_id("waitlist_id",target);
 }else if(!strcmp(action,"checkin")){
  if(strcmp(jstr(row,"status"),"CONFIRMED")){r=result(409,"STATE_CONFLICT","该预约当前状态不能签到",NULL);goto save;}
  cJSON *ci=cJSON_GetObjectItemCaseSensitive(row,"checked_in_at");
  int done=ci&&cJSON_IsNumber(ci)&&ci->valuedouble>0;
  Id when=done?(Id)ci->valuedouble:now_sec();
  if(!done){if(!db_run(d,"UPDATE reservations SET checked_in_at=? WHERE id=?","ii",when,target))goto failed;event(d,u->id,"CHECKIN",target,key);}
  cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);cJSON_AddNumberToObject(j,"checked_in_at",(double)when);r=result(200,"OK",done?"该预约已签到":"签到成功",j);
 }else {r=result(400,"INVALID_INPUT","未知操作",NULL);goto finish_nosave;}
save:
 if(d->error)goto failed;
 if(!r.body){d->error=SQLITE_NOMEM;goto failed;}
 char *serialized=cJSON_PrintUnformatted(r.body);if(!serialized){d->error=SQLITE_NOMEM;goto failed;}
 db_run(d,"INSERT INTO request_receipts(user_id,request_id,action,payload_digest,http_status,result_json,created_at) VALUES(?,?,?,?,?,?,?)","isssisi",u->id,key,action,digest,(Id)r.status,serialized,now_sec());cJSON_free(serialized);
 if(d->error)goto failed;
finish_nosave:
 if(d->error)goto failed;
 if(!db_run(d,"COMMIT",""))goto failed;
 cJSON_Delete(old);cJSON_Delete(row);cJSON_Delete(slot);
 if(r.status==200)fault(cfg,"after-commit",key);
 return r;
failed:
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);cJSON_Delete(old);cJSON_Delete(row);cJSON_Delete(slot);cJSON_Delete(r.body);return db_failure(d);
}
Result records(DB *d,const User *u,int all,Id date,int page,int size,const char *status,const char *ev_action,const char *ev_user){
 /* status 为白名单枚举（调用方已校验，直接拼接无注入面），过滤预约列表；
    ev_action/ev_user 仅过滤管理端操作日志（参数化绑定）。 */
 const char *where=all?"(?=0 OR (s.start_at>=? AND s.start_at<?))":"r.user_id=?";
 char sql[1900];
 Id limit=(Id)size+1,offset=(Id)(page-1)*size;int more=0;
 snprintf(sql,sizeof sql,"SELECT r.id,r.slot_id,l.name AS lab_name,u.username,s.start_at,s.end_at,r.status,r.source,r.cancel_reason,r.checked_in_at FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=r.user_id WHERE %s%s ORDER BY r.id DESC LIMIT ? OFFSET ?",
  where,status?" AND r.status='?'":"");
 cJSON *a=all?db_rows(d,sql,"iiiii",date,date,date+86400,limit,offset):db_rows(d,sql,"iii",u->id,limit,offset);
 if(a&&cJSON_GetArraySize(a)>size){cJSON_DeleteItemFromArray(a,(int)size);more=1;}
 snprintf(sql,sizeof sql,"SELECT r.id,r.slot_id,l.name AS lab_name,u.username,s.start_at,s.end_at,r.status,CASE WHEN r.status='WAITING' THEN (SELECT count(*) FROM waitlist w JOIN users wu ON wu.id=w.user_id WHERE w.slot_id=r.slot_id AND w.status='WAITING' AND w.id<=r.id AND wu.enabled=1) ELSE 0 END AS position FROM waitlist r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=r.user_id WHERE %s ORDER BY r.id DESC LIMIT ? OFFSET ?",where);
 cJSON *b=all?db_rows(d,sql,"iiiii",date,date,date+86400,limit,offset):db_rows(d,sql,"iii",u->id,limit,offset);
 if(b&&cJSON_GetArraySize(b)>size){cJSON_DeleteItemFromArray(b,(int)size);more=1;}
 cJSON *e=cJSON_CreateArray();
 if(all){
  char esql[380],fmt[12];size_t fl=0;
  snprintf(esql,sizeof esql,"SELECT e.id,u.username AS actor,e.action,e.entity_id,e.request_id,e.created_at FROM operation_events e JOIN users u ON u.id=e.actor_id WHERE 1=1%s%s ORDER BY e.id DESC LIMIT ? OFFSET ?",
   ev_action?" AND e.action=?":"",ev_user?" AND u.username=?":"");
  if(ev_action){fmt[fl++]='s';}
  if(ev_user){fmt[fl++]='s';}
  fmt[fl++]='i';fmt[fl++]='i';fmt[fl]=0;
  if(ev_action&&ev_user)e=db_rows(d,esql,fmt,ev_action,ev_user,limit,offset);
  else if(ev_action)e=db_rows(d,esql,fmt,ev_action,limit,offset);
  else if(ev_user)e=db_rows(d,esql,fmt,ev_user,limit,offset);
  else e=db_rows(d,esql,fmt,limit,offset);
 }
 if(d->error){cJSON_Delete(a);cJSON_Delete(b);cJSON_Delete(e);return db_failure(d);}
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"reservations",a);cJSON_AddItemToObject(j,"waitlist",b);cJSON_AddItemToObject(j,"events",e);
 cJSON_AddNumberToObject(j,"page",(double)page);cJSON_AddNumberToObject(j,"page_size",(double)size);cJSON_AddBoolToObject(j,"has_more",more);
 return result(200,"OK","查询成功",j);
}
Result stats(DB *d,Id start,Id end){
 cJSON *days=db_rows(d,"SELECT (s.start_at+28800)/86400 AS day,count(DISTINCT s.id) AS slots,count(DISTINCT CASE WHEN r.status='CONFIRMED' THEN r.id END) AS confirmed,count(DISTINCT CASE WHEN r.status='CANCELLED' AND (r.cancel_reason IS NULL OR r.cancel_reason='USER') THEN r.id END) AS cancelled,count(DISTINCT CASE WHEN r.cancel_reason='NO_SHOW' THEN r.id END) AS no_show,count(DISTINCT CASE WHEN r.checked_in_at IS NOT NULL THEN r.id END) AS checked_in FROM slots s LEFT JOIN reservations r ON r.slot_id=s.id WHERE s.start_at>=? AND s.start_at<? GROUP BY day ORDER BY day","ii",start,end+86400);
 if(d->error)return db_failure(d);
 cJSON *waiting=db_rows(d,"SELECT (s.start_at+28800)/86400 AS day,count(*) AS waiting FROM waitlist w JOIN slots s ON s.id=w.slot_id JOIN users u ON u.id=w.user_id WHERE s.start_at>=? AND s.start_at<? AND w.status='WAITING' AND u.enabled=1 GROUP BY day ORDER BY day","ii",start,end+86400);
 if(d->error){cJSON_Delete(days);return db_failure(d);}
 cJSON *rows=cJSON_CreateArray(),*totals=cJSON_CreateObject();
 if(rows&&totals){
  double ts=0,tc=0,tx=0,tn=0,ti=0,tw=0;cJSON *it;
  cJSON_ArrayForEach(it,days){
   cJSON *row=cJSON_CreateObject();if(!row)break;
   char date[11];date_text((Id)cJSON_GetObjectItemCaseSensitive(it,"day")->valuedouble,date);
   double w=0;cJSON *jt;cJSON_ArrayForEach(jt,waiting)if((Id)cJSON_GetObjectItemCaseSensitive(jt,"day")->valuedouble==(Id)cJSON_GetObjectItemCaseSensitive(it,"day")->valuedouble){w=cJSON_GetObjectItemCaseSensitive(jt,"waiting")->valuedouble;break;}
   double s=cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble,c=cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble,x=cJSON_GetObjectItemCaseSensitive(it,"cancelled")->valuedouble,ns=cJSON_GetObjectItemCaseSensitive(it,"no_show")->valuedouble,ci=cJSON_GetObjectItemCaseSensitive(it,"checked_in")->valuedouble;
   cJSON_AddStringToObject(row,"date",date);cJSON_AddNumberToObject(row,"slots",s);cJSON_AddNumberToObject(row,"confirmed",c);cJSON_AddNumberToObject(row,"cancelled",x);cJSON_AddNumberToObject(row,"no_show",ns);cJSON_AddNumberToObject(row,"checked_in",ci);cJSON_AddNumberToObject(row,"waiting",w);
   cJSON_AddItemToArray(rows,row);ts+=s;tc+=c;tx+=x;tn+=ns;ti+=ci;tw+=w;
  }
  cJSON_AddNumberToObject(totals,"slots",ts);cJSON_AddNumberToObject(totals,"confirmed",tc);cJSON_AddNumberToObject(totals,"cancelled",tx);cJSON_AddNumberToObject(totals,"no_show",tn);cJSON_AddNumberToObject(totals,"checked_in",ti);cJSON_AddNumberToObject(totals,"waiting",tw);
 }
 cJSON_Delete(days);cJSON_Delete(waiting);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"stats",rows);cJSON_AddItemToObject(j,"totals",totals);
 return result(200,"OK","查询成功",j);
}
Result stats_export(DB *d,Id start,Id end){
 Result r=stats(d,start,end);if(r.status!=200)return r;
 cJSON *data=cJSON_GetObjectItemCaseSensitive(r.body,"data"),*rows=cJSON_GetObjectItemCaseSensitive(data,"stats"),*totals=cJSON_GetObjectItemCaseSensitive(data,"totals");
 char a[11],b[11];date_text((start+28800)/86400,a);date_text((end+28800)/86400,b);
 size_t cap=4096+(size_t)(rows?cJSON_GetArraySize(rows):0)*160;char *csv=malloc(cap);
 if(!csv){cJSON_Delete(r.body);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(csv,cap,"\xEF\xBB\xBF" "日期,开放场次,有效预约,已取消,已爽约,已签到,候补人数\n");
 if(n<0||(size_t)n>=cap)n=0;
 cJSON *it;cJSON_ArrayForEach(it,rows){
  if((size_t)n+160>=cap)break;
  const char *day=jstr(it,"date");
  n+=snprintf(csv+n,cap-(size_t)n,"%s,%g,%g,%g,%g,%g,%g\n",day?day:"",
   cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble,
   cJSON_GetObjectItemCaseSensitive(it,"cancelled")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"no_show")->valuedouble,
   cJSON_GetObjectItemCaseSensitive(it,"checked_in")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"waiting")->valuedouble);
 }
 if(totals&&(size_t)n+160<cap)n+=snprintf(csv+n,cap-(size_t)n,"合计,%g,%g,%g,%g,%g,%g\n",
  cJSON_GetObjectItemCaseSensitive(totals,"slots")->valuedouble,cJSON_GetObjectItemCaseSensitive(totals,"confirmed")->valuedouble,
  cJSON_GetObjectItemCaseSensitive(totals,"cancelled")->valuedouble,cJSON_GetObjectItemCaseSensitive(totals,"no_show")->valuedouble,
  cJSON_GetObjectItemCaseSensitive(totals,"checked_in")->valuedouble,cJSON_GetObjectItemCaseSensitive(totals,"waiting")->valuedouble);
 cJSON_Delete(r.body);
 char name[64];snprintf(name,sizeof name,"lab-stats-%s_%s.csv",a,b);
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"filename",name);cJSON_AddStringToObject(j,"content",csv);free(csv);
 return result(200,"OK","导出完成",j);
}
Result notifications(DB *d,const User *u,int unread,int page,int size){
 char sql[320];
 snprintf(sql,sizeof sql,"SELECT id,kind,title,body,slot_id,reservation_id,read_at,created_at FROM notifications WHERE user_id=?%s ORDER BY id DESC LIMIT ? OFFSET ?",unread?" AND read_at IS NULL":"");
 cJSON *rows=db_rows(d,sql,"iii",u->id,(Id)size+1,(Id)(page-1)*size);
 if(!rows)return db_failure(d);
 int more=cJSON_GetArraySize(rows)>size;if(more)cJSON_DeleteItemFromArray(rows,size);
 Id unread_count=db_num(d,"SELECT count(*) FROM notifications WHERE user_id=? AND read_at IS NULL","i",u->id);
 if(d->error){cJSON_Delete(rows);return db_failure(d);}
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"notifications",rows);
 cJSON_AddNumberToObject(j,"unread_count",(double)unread_count);cJSON_AddNumberToObject(j,"page",(double)page);
 cJSON_AddNumberToObject(j,"page_size",(double)size);cJSON_AddBoolToObject(j,"has_more",more);
 return result(200,"OK","查询成功",j);
}
Result notifications_read(DB *d,const User *u,const cJSON *body,const char *key){
 cJSON *all=cJSON_GetObjectItemCaseSensitive(body,"all"),*ids=cJSON_GetObjectItemCaseSensitive(body,"ids");
 int mark_all=cJSON_IsTrue(all);
 if(!mark_all&&!cJSON_IsArray(ids))return result(400,"INVALID_INPUT","请提供 ids 数组，或将 all 置为 true",NULL);
 if(!mark_all&&cJSON_GetArraySize(ids)>50)return result(400,"INVALID_INPUT","单次最多标记 50 条通知",NULL);
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 Id changed=0,ts=now_sec();
 if(mark_all){db_run(d,"UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL","ii",ts,u->id);changed=(Id)sqlite3_changes(d->sql);}
 else{cJSON *it;cJSON_ArrayForEach(it,ids){
  Id id=0;
  if(cJSON_IsString(it)){if(!parse_id(it->valuestring,&id))id=0;}
  else if(cJSON_IsNumber(it)&&it->valuedouble>0)id=(Id)it->valuedouble;
  if(!id){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return result(400,"INVALID_INPUT","通知编号不合法",NULL);}
  db_run(d,"UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL","iii",ts,id,u->id);changed+=(Id)sqlite3_changes(d->sql);
 }}
 event(d,u->id,"NOTIFY_READ",0,key);
 if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 if(!db_run(d,"COMMIT",""))return db_failure(d);
 cJSON *j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"updated",(double)changed);return result(200,"OK","已标记为已读",j);
}
/* 管理员修改已发布场次（容量/启停）；容量校验与更新同处一个立即事务，避免与预约并发竞争。 */
Result slot_update(DB *d,const User *u,Id target,const cJSON *body){
 cJSON *cap=cJSON_GetObjectItemCaseSensitive(body,"capacity"),*en=cJSON_GetObjectItemCaseSensitive(body,"enabled");
 int has_cap=cap&&(cJSON_IsString(cap)||cJSON_IsNumber(cap))?1:0,has_en=cJSON_IsBool(en)?1:0;
 if((cap&&!has_cap)||(en&&!has_en))return result(400,"INVALID_INPUT","capacity 需为数字或字符串，enabled 需为布尔值",NULL);
 if(!has_cap&&!has_en)return result(400,"INVALID_INPUT","请至少提供 capacity 或 enabled",NULL);
 Id capacity=0;int enabled=0;
 if(has_cap){capacity=1;
  if(cJSON_IsString(cap)&&!parse_id(cap->valuestring,&capacity))return result(400,"INVALID_INPUT","capacity 不合法",NULL);
  if(cJSON_IsNumber(cap))capacity=(Id)cap->valuedouble;
  if(capacity<1||capacity>200)return result(400,"INVALID_INPUT","容量需为 1..200 的整数",NULL);}
 if(has_en)enabled=cJSON_IsTrue(en)?1:0;
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 if(!db_num(d,"SELECT count(*) FROM slots WHERE id=?","i",target)){
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  db_run(d,"ROLLBACK","");return result(404,"NOT_FOUND","场次不存在",NULL);}
 if(has_cap){Id taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",target);
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  if(capacity<taken){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return result(409,"STATE_CONFLICT","容量不能小于已确认预约数",NULL);}}
 int ok=has_cap&&has_en?db_run(d,"UPDATE slots SET capacity=?,enabled=? WHERE id=?","iii",capacity,(Id)enabled,target):
  has_cap?db_run(d,"UPDATE slots SET capacity=? WHERE id=?","ii",capacity,target):
  db_run(d,"UPDATE slots SET enabled=? WHERE id=?","ii",(Id)enabled,target);
 if(!ok||d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 cJSON *row=db_first(d,"SELECT capacity,enabled FROM slots WHERE id=?","i",target);
 if(!row){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 jid(row,"slot_id",target);
 event(d,u->id,"SLOT_UPDATE",target,NULL);
 if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);cJSON_Delete(row);return db_failure(d);}
 if(!db_run(d,"COMMIT","")){cJSON_Delete(row);return db_failure(d);}
 return result(200,"OK","场次已更新",row);
}
/* 管理员发布通知（公告）：全量用户或指定用户，kind='NOTICE'，slot/reservation 置空。 */
Result admin_notify(DB *d,const User *u,const cJSON *body){
 const char *title=jstr(body,"title"),*text=jstr(body,"body"),*name=jstr(body,"username");
 cJSON *all=cJSON_GetObjectItemCaseSensitive(body,"all");
 int to_all=cJSON_IsTrue(all)?1:0,has_name=name&&*name?1:0;
 if(all&&!cJSON_IsBool(all))return result(400,"INVALID_INPUT","all 需为布尔值",NULL);
 if(to_all==has_name)return result(400,"INVALID_INPUT","all 与 username 必须二选一",NULL);
 if(!title||!*title||strlen(title)>120)return result(400,"INVALID_INPUT","标题需为 1..120 个字符",NULL);
 if(text&&strlen(text)>500)return result(400,"INVALID_INPUT","正文最多 500 个字符",NULL);
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 Id sent=0;
 if(to_all){
  cJSON *users=db_rows(d,"SELECT id FROM users WHERE enabled=1","");
  if(!users){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  cJSON *it;cJSON_ArrayForEach(it,users){
   Id uid=0;if(!parse_id(jstr(it,"id"),&uid))continue;
   db_run(d,"INSERT INTO notifications(user_id,kind,title,body,slot_id,reservation_id,created_at) VALUES(?,?,?,?,NULL,NULL,?)","isssi",uid,"NOTICE",title,text?text:"",now_sec());
   if(d->error){cJSON_Delete(users);sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
   sent++;
  }
  cJSON_Delete(users);
 }else{
  cJSON *row=db_first(d,"SELECT id FROM users WHERE username=? AND enabled=1","s",name);
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  if(!row){db_run(d,"ROLLBACK","");return result(404,"NOT_FOUND","用户不存在",NULL);}
  Id uid=0;parse_id(jstr(row,"id"),&uid);cJSON_Delete(row);
  db_run(d,"INSERT INTO notifications(user_id,kind,title,body,slot_id,reservation_id,created_at) VALUES(?,?,?,?,NULL,NULL,?)","isssi",uid,"NOTICE",title,text?text:"",now_sec());
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  sent=1;
 }
 event(d,u->id,"NOTIFY",0,NULL);
 if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 if(!db_run(d,"COMMIT",""))return db_failure(d);
 cJSON *j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"sent",(double)sent);return result(200,"OK","通知发布完成",j);
}
Result sessions_list(DB *d,const User *u,const char *current){
 cJSON *rows=db_rows(d,"SELECT token_hash,created_at,expires_at FROM sessions WHERE user_id=? AND expires_at>? ORDER BY expires_at DESC","ii",u->id,now_sec());
 if(!rows)return db_failure(d);
 cJSON *out=cJSON_CreateArray();
 if(!out){cJSON_Delete(rows);return db_failure(d);}
 cJSON *it;cJSON_ArrayForEach(it,rows){
  const char *h=jstr(it,"token_hash");
  cJSON *o=cJSON_CreateObject();if(!o){d->error=SQLITE_NOMEM;break;}
  cJSON_AddStringToObject(o,"id",h?h:"");
  cJSON *ca=cJSON_GetObjectItemCaseSensitive(it,"created_at"),*ex=cJSON_GetObjectItemCaseSensitive(it,"expires_at");
  if(ca&&cJSON_IsNumber(ca))cJSON_AddNumberToObject(o,"created_at",ca->valuedouble);else cJSON_AddNullToObject(o,"created_at");
  cJSON_AddNumberToObject(o,"expires_at",ex?ex->valuedouble:0);
  cJSON_AddBoolToObject(o,"current",h&&!strcmp(h,current));
  cJSON_AddItemToArray(out,o);
 }
 cJSON_Delete(rows);
 if(d->error){cJSON_Delete(out);return db_failure(d);}
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"sessions",out);
 return result(200,"OK","查询成功",j);
}
Result session_revoke(DB *d,const User *u,const char *hash,const char *key){
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 if(!db_num(d,"SELECT count(*) FROM sessions WHERE token_hash=? AND user_id=?","si",hash,u->id)){
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  db_run(d,"ROLLBACK","");return result(404,"NOT_FOUND","会话不存在或已失效",NULL);
 }
 db_run(d,"DELETE FROM sessions WHERE token_hash=? AND user_id=?","si",hash,u->id);
 event(d,u->id,"SESSION_REVOKE",0,key);
 if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 if(!db_run(d,"COMMIT",""))return db_failure(d);
 return result(200,"OK","该会话已下线",NULL);
}
Result password_change(DB *d,const User *u,const char *old_pw,const char *new_pw,const char *current_hash,const char *key){
 if(!current_hash)current_hash="";
 if(!old_pw||!*old_pw||strlen(old_pw)>128)return result(400,"INVALID_INPUT","请填写原密码",NULL);
 if(!new_pw||strlen(new_pw)<8||strlen(new_pw)>128)return result(400,"INVALID_INPUT","新密码长度需为 8..128 位",NULL);
 if(!strcmp(old_pw,new_pw))return result(400,"INVALID_INPUT","新密码不能与原密码相同",NULL);
 cJSON *r=db_first(d,"SELECT password_hash FROM users WHERE id=? AND enabled=1","i",u->id);
 if(d->error)return db_failure(d);
 if(!r)return result(401,"UNAUTHORIZED","账号不可用",NULL);
 int bad=crypto_pwhash_str_verify(jstr(r,"password_hash"),old_pw,strlen(old_pw));cJSON_Delete(r);
 if(bad)return result(401,"UNAUTHORIZED","原密码不正确",NULL);
 char hash[crypto_pwhash_STRBYTES];
 if(crypto_pwhash_str(hash,new_pw,strlen(new_pw),crypto_pwhash_OPSLIMIT_INTERACTIVE,crypto_pwhash_MEMLIMIT_INTERACTIVE))return result(500,"INTERNAL_ERROR","密码处理失败",NULL);
 if(!db_run(d,"BEGIN IMMEDIATE","")){sodium_memzero(hash,sizeof hash);return db_failure(d);}
 db_run(d,"UPDATE users SET password_hash=? WHERE id=?","si",hash,u->id);
 db_run(d,"DELETE FROM sessions WHERE user_id=? AND token_hash<>?","is",u->id,current_hash);
 Id revoked=(Id)sqlite3_changes(d->sql);
 event(d,u->id,"PASSWORD_CHANGE",u->id,key);
 sodium_memzero(hash,sizeof hash);
 if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 if(!db_run(d,"COMMIT",""))return db_failure(d);
 cJSON *j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"revoked_sessions",(double)revoked);
 return result(200,"OK","密码已更新，其他设备的登录已失效",j);
}
/* 签到窗口结束仍未签到的预约：标记爽约并释放名额；场次尚未结束时按 FIFO 补位。 */
int sweep_once(const Config *config){
 DB d={0};int count=0;
 if(!db_open(&d,config->db_path)){db_close(&d);return 0;}
 Id now=now_sec();
 cJSON *due=db_rows(&d,"SELECT r.id,r.user_id,r.slot_id,s.end_at>? AS live FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE r.status='CONFIRMED' AND r.checked_in_at IS NULL AND (CASE WHEN r.source='WAITLIST' THEN r.created_at ELSE s.start_at END)+?<=?","iii",now,(Id)config->checkin_window,now);
 if(!due){db_close(&d);return 0;}
 if(!db_run(&d,"BEGIN IMMEDIATE","")){cJSON_Delete(due);db_close(&d);return 0;}
 cJSON *it;cJSON_ArrayForEach(it,due){
  Id rid=0,uid=0,sid=0;
  if(!parse_id(jstr(it,"id"),&rid)||!parse_id(jstr(it,"user_id"),&uid)||!parse_id(jstr(it,"slot_id"),&sid))continue;
  cJSON *lv=cJSON_GetObjectItemCaseSensitive(it,"live");int live=lv&&cJSON_IsNumber(lv)&&lv->valuedouble>0;
  if(!db_run(&d,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='NO_SHOW' WHERE id=? AND status='CONFIRMED' AND checked_in_at IS NULL","ii",now,rid))break;
  if(!sqlite3_changes(d.sql))continue;
  char when[40],body[192];slot_when(&d,sid,when);
  snprintf(body,sizeof body,"你预约的场次 %s 因超过签到时限未签到，已自动释放%s",when,live?"，名额按候补顺序转给下一位。":"。");
  notify(&d,uid,"NO_SHOW","预约已爽约释放",body,sid,rid);
  event(&d,uid,"NO_SHOW",rid,NULL);
  if(live)promote_fill(&d,sid,uid,NULL);
  count++;
 }
 cJSON_Delete(due);
 fault(config,"sweep-mid",NULL); /* 事务已写未提交：崩溃后应整体回滚（exit 88） */
 if(d.error){sqlite3_exec(d.sql,"ROLLBACK",NULL,NULL,NULL);db_close(&d);return count;}
 if(!db_run(&d,"COMMIT","")){db_close(&d);return count;}
 log_write(1,"SWEEP released=%d",count);
 /* -- r12 历史归档：每 10 个扫描周期清理过期候补与请求回执（预约记录保留供统计） -- */
 {static int archive_tick=0;
  if(++archive_tick%10==0){
   Id cutoff=now_sec()-(Id)ARCHIVE_DAYS*86400,removed=0;
   if(db_run(&d,"BEGIN IMMEDIATE","")){
    db_run(&d,"DELETE FROM waitlist WHERE slot_id IN (SELECT id FROM slots WHERE end_at<?)","i",cutoff);
    removed+=(Id)sqlite3_changes(d.sql);
    db_run(&d,"DELETE FROM request_receipts WHERE created_at<?","i",cutoff);
    removed+=(Id)sqlite3_changes(d.sql);
    if(d.error)sqlite3_exec(d.sql,"ROLLBACK",NULL,NULL,NULL);
    else if(db_run(&d,"COMMIT",""))log_write(1,"ARCHIVE removed=%lld",(long long)removed);
   }
  }
 }
 db_close(&d);return count;
}
/* -- r11 用户管理与运营增强 -- */
/* 管理员用户列表：含有效预约数与候补数；q 为用户名前缀过滤（substr 比较，避免 LIKE 通配符转义问题）。 */
Result users_list(DB *d,int page,int size,const char *q){
 cJSON *j=cJSON_CreateObject();
 char sql[640];Id limit=(Id)size+1,offset=(Id)(page-1)*size;
 Id window_start=now_sec()-(Id)PENALTY_DAYS*86400;
 snprintf(sql,sizeof sql,"SELECT u.id,u.username,u.role,u.enabled,(SELECT count(*) FROM reservations r WHERE r.user_id=u.id AND r.status='CONFIRMED') AS reservations,(SELECT count(*) FROM waitlist w WHERE w.user_id=u.id AND w.status='WAITING') AS waitlisted,(SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=u.id AND r.cancel_reason='NO_SHOW' AND s2.start_at>=?) AS no_show_count FROM users u %sORDER BY u.id LIMIT ? OFFSET ?",
  q?"WHERE substr(u.username,1,length(?))=? ":"");
 cJSON *rows=q?db_rows(d,sql,"issii",window_start,q,q,limit,offset):db_rows(d,sql,"iii",window_start,limit,offset);
 if(!rows)return db_failure(d);
 int more=cJSON_GetArraySize(rows)>size;if(more)cJSON_DeleteItemFromArray(rows,size);
 Id total=q?db_num(d,"SELECT count(*) FROM users WHERE substr(username,1,length(?))=?","ss",q,q):db_num(d,"SELECT count(*) FROM users","");
 if(d->error){cJSON_Delete(rows);return db_failure(d);}
 cJSON_AddItemToObject(j,"users",rows);cJSON_AddNumberToObject(j,"total",(double)total);
 cJSON_AddNumberToObject(j,"page",(double)page);cJSON_AddNumberToObject(j,"page_size",(double)size);cJSON_AddBoolToObject(j,"has_more",more);
 return result(200,"OK","查询成功",j);
}
/* 管理员用户操作：disable（立即下线+停用）/enable/reset-password（随机口令，明文仅响应一次）。 */
Result user_admin(DB *d,const User *actor,Id target,const char *op){
 cJSON *row=db_first(d,"SELECT id,role FROM users WHERE id=?","i",target);
 if(d->error)return db_failure(d);
 if(!row)return result(404,"NOT_FOUND","用户不存在",NULL);
 int is_admin=!strcmp(jstr(row,"role"),"ADMIN");cJSON_Delete(row);
 if(!strcmp(op,"disable")){
  if(target==actor->id&&is_admin)return result(409,"STATE_CONFLICT","不能停用当前登录的管理员",NULL);
  if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
  db_run(d,"UPDATE users SET enabled=0 WHERE id=?","i",target);
  db_run(d,"DELETE FROM sessions WHERE user_id=?","i",target);
  event(d,actor->id,"USER_DISABLE",target,NULL);
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  if(!db_run(d,"COMMIT",""))return db_failure(d);
  log_write(1,"ADMIN user %lld disabled by %lld",(long long)target,(long long)actor->id);
  cJSON *j=cJSON_CreateObject();jid(j,"user_id",target);cJSON_AddBoolToObject(j,"enabled",0);
  return result(200,"OK","账号已停用，该用户全部会话已下线",j);
 }
 if(!strcmp(op,"enable")){
  if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
  db_run(d,"UPDATE users SET enabled=1 WHERE id=?","i",target);
  event(d,actor->id,"USER_ENABLE",target,NULL);
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  if(!db_run(d,"COMMIT",""))return db_failure(d);
  cJSON *j=cJSON_CreateObject();jid(j,"user_id",target);cJSON_AddBoolToObject(j,"enabled",1);
  return result(200,"OK","账号已启用",j);
 }
 if(!strcmp(op,"reset-password")){
  char rnd[65],pw[13],hash[crypto_pwhash_STRBYTES];
  random_hex(rnd);memcpy(pw,rnd,12);pw[12]=0;sodium_memzero(rnd,sizeof rnd);
  if(crypto_pwhash_str(hash,pw,strlen(pw),crypto_pwhash_OPSLIMIT_INTERACTIVE,crypto_pwhash_MEMLIMIT_INTERACTIVE))return result(500,"INTERNAL_ERROR","密码处理失败",NULL);
  if(!db_run(d,"BEGIN IMMEDIATE","")){sodium_memzero(hash,sizeof hash);return db_failure(d);}
  db_run(d,"UPDATE users SET password_hash=? WHERE id=?","si",hash,target);
  sodium_memzero(hash,sizeof hash);
  db_run(d,"DELETE FROM sessions WHERE user_id=?","i",target);
  event(d,actor->id,"RESET_PW",target,NULL);
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  if(!db_run(d,"COMMIT",""))return db_failure(d);
  log_write(1,"ADMIN password reset for user %lld by %lld",(long long)target,(long long)actor->id);
  cJSON *j=cJSON_CreateObject();jid(j,"user_id",target);cJSON_AddStringToObject(j,"password",pw);
  sodium_memzero(pw,sizeof pw);
  return result(200,"OK","密码已重置，新口令仅显示这一次",j);
 }
 return result(404,"NOT_FOUND","接口不存在",NULL);
}
/* 通知发送历史：全站通知按时间倒序，含接收人与已读状态。 */
Result notifications_sent(DB *d,int page,int size){
 Id limit=(Id)size+1,offset=(Id)(page-1)*size;
 cJSON *rows=db_rows(d,"SELECT n.id,n.kind,n.title,n.body,n.read_at,n.created_at,u.username FROM notifications n JOIN users u ON u.id=n.user_id ORDER BY n.id DESC LIMIT ? OFFSET ?","ii",limit,offset);
 if(!rows)return db_failure(d);
 int more=cJSON_GetArraySize(rows)>size;if(more)cJSON_DeleteItemFromArray(rows,size);
 Id total=db_num(d,"SELECT count(*) FROM notifications","");
 if(d->error){cJSON_Delete(rows);return db_failure(d);}
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"sent",rows);cJSON_AddNumberToObject(j,"total",(double)total);
 cJSON_AddNumberToObject(j,"page",(double)page);cJSON_AddNumberToObject(j,"page_size",(double)size);cJSON_AddBoolToObject(j,"has_more",more);
 return result(200,"OK","查询成功",j);
}
/* r13 实验室资源（设备）清单：用户端只读展示（不含 DISABLED），管理端增改。 */
Result assets_list(DB *d,Id lab){
 cJSON *rows=db_rows(d,"SELECT id,name,spec,total,status FROM assets WHERE lab_id=? AND status<>'DISABLED' ORDER BY id","i",lab);
 if(d->error)return db_failure(d);
 if(!rows)rows=cJSON_CreateArray();
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"assets",rows);
 return result(200,"OK","查询成功",j);
}
Result asset_admin(DB *d,const User *u,Id lab,Id asset,const cJSON *body){
 const char *name=jstr(body,"name"),*spec=jstr(body,"spec"),*ttotal=jstr(body,"total"),*status=jstr(body,"status");
 if(!name||!strlen(name)||strlen(name)>180||(spec&&strlen(spec)>300))return result(400,"INVALID_INPUT","资源名称或规格不合法",NULL);
 Id total=1;
 if(ttotal&&(!parse_id(ttotal,&total)||total<1||total>999))return result(400,"INVALID_INPUT","总数需为 1..999 的整数",NULL);
 static const char *statuses[]={"AVAILABLE","MAINTENANCE","DISABLED"};
 int st=0,status_given=status&&status[0];
 if(status_given){
  while(st<3&&strcmp(status,statuses[st]))st++;
  if(st>=3)return result(400,"INVALID_INPUT","status 需为 AVAILABLE/MAINTENANCE/DISABLED",NULL);
 }
 if(lab){ /* 新增 */
  if(!db_num(d,"SELECT count(*) FROM labs WHERE id=?","i",lab))return result(404,"NOT_FOUND","实验室不存在",NULL);
  if(!db_run(d,"INSERT INTO assets(lab_id,name,spec,total,status,created_at) VALUES(?,?,?,?,?,?)","issisi",lab,name,(spec&&spec[0])?spec:"",total,status_given?statuses[st]:"AVAILABLE",now_sec())){
   if((d->error&255)==SQLITE_CONSTRAINT){d->error=0;return result(409,"STATE_CONFLICT","该实验室已有同名资源",NULL);}
   return db_failure(d);
  }
  Id aid=sqlite3_last_insert_rowid(d->sql);
  event(d,u->id,"ASSET_CREATE",aid,NULL);
  cJSON *j=cJSON_CreateObject();jid(j,"asset_id",aid);return result(200,"OK","资源已添加",j);
 }
 /* 更新 */
 if(!db_run(d,"UPDATE assets SET name=?,spec=?,total=?,status=? WHERE id=?","ssisi",name,(spec&&spec[0])?spec:"",total,status_given?statuses[st]:"AVAILABLE",asset))return db_failure(d);
 if(!sqlite3_changes(d->sql))return result(404,"NOT_FOUND","资源不存在",NULL);
 event(d,u->id,"ASSET_UPDATE",asset,NULL);
 cJSON *j=cJSON_CreateObject();jid(j,"asset_id",asset);return result(200,"OK","资源已更新",j);
}
/* 资源利用率：按实验室聚合区间内的开放场次/席位与预约、签到、爽约，利用率=有效预约/总席位。 */
Result lab_utilization(DB *d,Id start,Id end){
 cJSON *rows=db_rows(d,
  "SELECT l.id AS lab_id,l.name AS lab_name,count(s.id) AS slots,CAST(total(s.capacity) AS INTEGER) AS seats,"
  "(SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE s2.lab_id=l.id AND r.status='CONFIRMED' AND s2.start_at>=? AND s2.start_at<?) AS confirmed,"
  "(SELECT count(*) FROM reservations r JOIN slots s3 ON s3.id=r.slot_id WHERE s3.lab_id=l.id AND r.checked_in_at IS NOT NULL AND s3.start_at>=? AND s3.start_at<?) AS checked_in,"
  "(SELECT count(*) FROM reservations r JOIN slots s4 ON s4.id=r.slot_id WHERE s4.lab_id=l.id AND r.cancel_reason='NO_SHOW' AND s4.start_at>=? AND s4.start_at<?) AS no_show,"
  "(SELECT CAST(total(r.checked_out_at-r.checked_in_at)/60 AS INTEGER) FROM reservations r JOIN slots s5 ON s5.id=r.slot_id WHERE s5.lab_id=l.id AND r.checked_out_at IS NOT NULL AND r.checked_in_at IS NOT NULL AND s5.start_at>=? AND s5.start_at<?) AS actual_minutes "
  "FROM labs l LEFT JOIN slots s ON s.lab_id=l.id AND s.start_at>=? AND s.start_at<? GROUP BY l.id ORDER BY l.id",
  "iiiiiiiiii",start,end+86400,start,end+86400,start,end+86400,start,end+86400,start,end+86400);
 if(!rows)return db_failure(d);
 cJSON *out=cJSON_CreateArray();cJSON *it;
 cJSON_ArrayForEach(it,rows){
  cJSON *row=cJSON_CreateObject();if(!row)break;
  double seats=cJSON_GetObjectItemCaseSensitive(it,"seats")->valuedouble;
  double confirmed=cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble;
  double util=seats>0?confirmed/seats*100.0:0.0;
  cJSON *lid=cJSON_GetObjectItemCaseSensitive(it,"lab_id");
  if(lid&&lid->valuestring)cJSON_AddStringToObject(row,"lab_id",lid->valuestring);
  else if(lid)cJSON_AddNumberToObject(row,"lab_id",lid->valuedouble);
  cJSON_AddStringToObject(row,"lab_name",jstr(it,"lab_name"));
  cJSON_AddNumberToObject(row,"slots",cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble);
  cJSON_AddNumberToObject(row,"seats",seats);
  cJSON_AddNumberToObject(row,"confirmed",confirmed);
  cJSON_AddNumberToObject(row,"checked_in",cJSON_GetObjectItemCaseSensitive(it,"checked_in")->valuedouble);
  cJSON_AddNumberToObject(row,"no_show",cJSON_GetObjectItemCaseSensitive(it,"no_show")->valuedouble);
  cJSON_AddNumberToObject(row,"utilization",((int)(util*10+0.5))/10.0);
  {double actual=cJSON_GetObjectItemCaseSensitive(it,"actual_minutes")->valuedouble;
   double seat_min=seats*60.0;
   cJSON_AddNumberToObject(row,"actual_minutes",(int)actual);
   cJSON_AddNumberToObject(row,"seat_minutes",(int)seat_min);
   cJSON_AddNumberToObject(row,"utilization_actual",seat_min>0?((int)(actual/seat_min*1000.0+0.5))/10.0:0.0);}
  cJSON_AddItemToArray(out,row);
 }
 cJSON_Delete(rows);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"utilization",out);
 return result(200,"OK","查询成功",j);
}
/* r16 签退：本人+已签到+场次未结束；重复签退返回首次时间（对齐签到幂等语义） */
Result reservation_checkout(DB *d,const Config *cfg,const User *u,Id target,const char *key){
 (void)cfg;
 Result r={500,NULL};cJSON *row=NULL;
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 row=db_first(d,"SELECT r.user_id,r.status,r.checked_in_at,r.checked_out_at,s.end_at FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE r.id=?","i",target);
 if(d->error)goto failed;
 if(!row){r=result(404,"NOT_FOUND","记录不存在",NULL);goto save;}
 {Id owner=0;parse_id(jstr(row,"user_id"),&owner);
  if(owner!=u->id){r=result(403,"FORBIDDEN","只能操作自己的记录",NULL);goto save;}}
 if(strcmp(jstr(row,"status"),"CONFIRMED")){r=result(409,"STATE_CONFLICT","该预约当前状态不能签退",NULL);goto save;}
 {cJSON *ci=cJSON_GetObjectItemCaseSensitive(row,"checked_in_at");
  if(!(ci&&cJSON_IsNumber(ci)&&ci->valuedouble>0)){r=result(409,"STATE_CONFLICT","尚未签到，请先签到",NULL);goto save;}}
 {cJSON *co=cJSON_GetObjectItemCaseSensitive(row,"checked_out_at");
  if(co&&cJSON_IsNumber(co)&&co->valuedouble>0){
   cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);cJSON_AddNumberToObject(j,"checked_out_at",co->valuedouble);
   r=result(200,"OK","该预约已签退",j);goto save;}}
 {Id end_at=(Id)cJSON_GetObjectItemCaseSensitive(row,"end_at")->valuedouble;
  if(now_sec()>=end_at){r=result(409,"STATE_CONFLICT","场次已结束，无需签退",NULL);goto save;}}
 {Id when=now_sec();
  if(!db_run(d,"UPDATE reservations SET checked_out_at=? WHERE id=?","ii",when,target))goto failed;
  event(d,u->id,"CHECKOUT",target,key);
  cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);cJSON_AddNumberToObject(j,"checked_out_at",(double)when);
  r=result(200,"OK","签退成功",j);}
save:
 if(d->error)goto failed;
 if(!r.body){d->error=SQLITE_NOMEM;goto failed;}
 {char *serialized=cJSON_PrintUnformatted(r.body);if(!serialized){d->error=SQLITE_NOMEM;goto failed;}
  db_run(d,"INSERT INTO request_receipts(user_id,request_id,action,payload_digest,http_status,result_json,created_at) VALUES(?,?,?,?,?,?,?)","isssisi",u->id,key,"checkout","checkout",0,serialized,now_sec());cJSON_free(serialized);}
 if(d->error)goto failed;
 if(!db_run(d,"COMMIT",""))goto failed;
 cJSON_Delete(row);
 return r;
failed:
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);cJSON_Delete(row);cJSON_Delete(r.body);return db_failure(d);
}
/* r16 日历导出：全部有效预约导出 iCalendar VEVENT（UTC 时间，含实验室与签到状态）。 */
Result calendar_export(DB *d,const User *u){
 cJSON *rows=db_rows(d,"SELECT r.id,r.checked_in_at,s.start_at,s.end_at,l.name AS lab FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id WHERE r.user_id=? AND r.status='CONFIRMED' ORDER BY s.start_at","i",u->id);
 if(!rows)return db_failure(d);
 size_t cap=1024+(size_t)(rows?cJSON_GetArraySize(rows):0)*400;char *cal=malloc(cap);
 if(!cal){cJSON_Delete(rows);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(cal,cap,"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//LabBooking//CN\r\n");
 cJSON *it;cJSON_ArrayForEach(it,rows){
  Id st=(Id)cJSON_GetObjectItemCaseSensitive(it,"start_at")->valuedouble;
  Id en=(Id)cJSON_GetObjectItemCaseSensitive(it,"end_at")->valuedouble;
  Id rid=(Id)cJSON_GetObjectItemCaseSensitive(it,"id")->valuedouble;
  cJSON *ci=cJSON_GetObjectItemCaseSensitive(it,"checked_in_at");
  const char *lab=jstr(it,"lab");
  char ds[24],de[24],tstat[32];
  time_t tu=(time_t)st;strftime(ds,sizeof ds,"%Y%m%dT%H%M%SZ",gmtime(&tu));
  tu=(time_t)en;strftime(de,sizeof de,"%Y%m%dT%H%M%SZ",gmtime(&tu));
  snprintf(tstat,sizeof tstat," · %s",(ci&&cJSON_IsNumber(ci)&&ci->valuedouble>0)?"已签到":"未签到");
  char sum[160];snprintf(sum,sizeof sum,"实验室预约：%s%s",lab?lab:"",tstat);
  if((size_t)n+460>=cap)break;
  n+=snprintf(cal+n,cap-(size_t)n,"BEGIN:VEVENT\r\nUID:lab-booking-%lld@lab-booking\r\nDTSTART:%s\r\nDTEND:%s\r\nSUMMARY:%s\r\nEND:VEVENT\r\n",(long long)rid,ds,de,sum);
 }
 cJSON_Delete(rows);
 n+=snprintf(cal+n,cap-(size_t)n,"END:VCALENDAR\r\n");
 char name[40];snprintf(name,sizeof name,"lab-schedule.ics");
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"filename",name);cJSON_AddStringToObject(j,"content",cal);free(cal);
 return result(200,"OK","导出完成",j);
}
/* r14 资源使用统计：区间内某资源的声明次数（按有效预约、按日聚合），用于资源维度闭环分析。 */
Result asset_usage(DB *d,Id aid,Id start,Id end){
 cJSON *a=db_first(d,"SELECT id,name,spec,total,status FROM assets WHERE id=?","i",aid);
 if(d->error)return db_failure(d);
 if(!a)return result(404,"NOT_FOUND","资源不存在",NULL);
 cJSON *days=db_rows(d,"SELECT (s.start_at+28800)/86400 AS day,count(*) AS claims FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id AND r.status='CONFIRMED' JOIN slots s ON s.id=r.slot_id WHERE c.asset_id=? AND s.start_at>=? AND s.start_at<? GROUP BY day ORDER BY day","iii",aid,start,end+86400);
 if(!days){cJSON_Delete(a);return db_failure(d);}
 cJSON *out=cJSON_CreateArray();cJSON *it;
 cJSON_ArrayForEach(it,days){
  cJSON *row=cJSON_CreateObject();if(!row)break;
  char date[11];date_text((Id)cJSON_GetObjectItemCaseSensitive(it,"day")->valuedouble,date);
  cJSON_AddStringToObject(row,"date",date);
  cJSON_AddNumberToObject(row,"claims",cJSON_GetObjectItemCaseSensitive(it,"claims")->valuedouble);
  cJSON_AddItemToArray(out,row);
 }
 cJSON_Delete(days);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"asset",a);cJSON_AddItemToObject(j,"usage",out);
 return result(200,"OK","查询成功",j);
}
Result utilization_export(DB *d,Id start,Id end){ Result r=lab_utilization(d,start,end);if(r.status!=200)return r;
 cJSON *rows=cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(r.body,"data"),"utilization");
 char a[11],b[11];date_text((start+28800)/86400,a);date_text((end+28800)/86400,b);
 size_t cap=2048+(size_t)(rows?cJSON_GetArraySize(rows):0)*160;char *csv=malloc(cap);
 if(!csv){cJSON_Delete(r.body);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(csv,cap,"\xEF\xBB\xBF" "实验室,开放场次,总席位,有效预约,已签到,已爽约,利用率%\n");
 if(n<0||(size_t)n>=cap)n=0;
 cJSON *it;cJSON_ArrayForEach(it,rows){
  if((size_t)n+200>=cap)break;
  n+=snprintf(csv+n,cap-(size_t)n,"%s,%g,%g,%g,%g,%g,%g\n",jstr(it,"lab_name"),
   cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"seats")->valuedouble,
   cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"checked_in")->valuedouble,
   cJSON_GetObjectItemCaseSensitive(it,"no_show")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"utilization")->valuedouble);
 }
 cJSON_Delete(r.body);
 char name[64];snprintf(name,sizeof name,"lab-utilization-%s_%s.csv",a,b);
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"filename",name);cJSON_AddStringToObject(j,"content",csv);free(csv);
 return result(200,"OK","导出完成",j);
}
/* 管理员查看服务日志尾部：按级别过滤（日志行以 [level] 前缀），最多返回 lines 行。 */
 Result logs_tail(int lines,int level){
 if(lines<1)lines=1;
 if(lines>500)lines=500;
 if(level<1||level>3)level=0; /* 0=不过滤 */
 const char *path="data/logs/app.log";
 FILE *f=fopen(path,"rb");
 cJSON *j=cJSON_CreateObject();cJSON *arr=cJSON_AddArrayToObject(j,"lines");
 if(!f){cJSON_AddBoolToObject(j,"truncated",0);cJSON_AddNumberToObject(j,"file_size",0);return result(200,"OK","日志为空",j);}
 fseek(f,0,SEEK_END);long size=ftell(f);
 /* 只读尾部最多 512KB，足够 500 行 */
 long window=size>512*1024?512*1024:size;
 fseek(f,size-window,SEEK_SET);
 char *buf=malloc((size_t)window+1);if(!buf){fclose(f);cJSON_Delete(j);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 size_t got=fread(buf,1,(size_t)window,f);fclose(f);buf[got]=0;
 int truncated=(window<size);
 /* 滑动窗口收集匹配级别的最后 lines 行 */
 char **sel=calloc((size_t)lines,sizeof *sel);if(!sel){free(buf);cJSON_Delete(j);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int found=0;
 char *ctx=NULL;
 for(char *p=buf;;p=NULL){char *tok=strtok_r(p,"\n",&ctx);if(!tok)break;
  if(level){const char *name=level>=3?"ERROR":level==2?"WARN":"INFO";char prefix[12];snprintf(prefix,sizeof prefix,"[%s] ",name);size_t pl=strlen(prefix);if(strncmp(tok,prefix,pl))continue;}
  if(found<lines)sel[found++]=tok;else{for(int i=1;i<lines;i++)sel[i-1]=sel[i];sel[lines-1]=tok;}
 }
 for(int i=0;i<found;i++){cJSON *it=cJSON_CreateString(sel[i]);cJSON_AddItemToArray(arr,it);}
 free(sel);free(buf);
 cJSON_AddBoolToObject(j,"truncated",truncated?1:0);cJSON_AddNumberToObject(j,"file_size",(double)size);
 return result(200,"OK","查询成功",j);
}
/* 场次开始提醒：向即将开始（remind_sec 窗口内）且未提醒过场次的全部有效预约用户发送站内通知，reminded_at 防重。 */
int remind_once(const Config *config){
 if(config->remind_sec<=0)return 0;
 DB d={0};int count=0;
 if(!db_open(&d,config->db_path)){db_close(&d);return 0;}
 Id now=now_sec();
 cJSON *due=db_rows(&d,"SELECT DISTINCT s.id FROM slots s JOIN reservations r ON r.slot_id=s.id AND r.status='CONFIRMED' WHERE s.reminded_at IS NULL AND s.start_at>? AND s.start_at<=?","ii",now,now+(Id)config->remind_sec);
 if(!due||!cJSON_GetArraySize(due)){cJSON_Delete(due);db_close(&d);return 0;}
 if(!db_run(&d,"BEGIN IMMEDIATE","")){cJSON_Delete(due);db_close(&d);return 0;}
 cJSON *it;cJSON_ArrayForEach(it,due){
  Id sid=0;if(!parse_id(jstr(it,"id"),&sid))continue;
  cJSON *rs=db_rows(&d,"SELECT id,user_id FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",sid);
  if(!rs)break;
  char when[40];slot_when(&d,sid,when);
  cJSON *rit;cJSON_ArrayForEach(rit,rs){
   Id rid=0,uid=0;
   if(!parse_id(jstr(rit,"id"),&rid)||!parse_id(jstr(rit,"user_id"),&uid))continue;
   char body[160];snprintf(body,sizeof body,"你预约的场次 %s 即将开始，请按时到场并在签到时间内完成签到。",when);
   notify(&d,uid,"REMIND","场次即将开始",body,sid,rid);
  }
  cJSON_Delete(rs);
  db_run(&d,"UPDATE slots SET reminded_at=1 WHERE id=?","i",sid);
  if(d.error)break;
  count++;
 }
 cJSON_Delete(due);
 if(d.error||count==0){if(d.error)sqlite3_exec(d.sql,"ROLLBACK",NULL,NULL,NULL);db_close(&d);return count;}
 if(!db_run(&d,"COMMIT","")){db_close(&d);return count;}
 log_write(1,"REMIND slots=%d",count);db_close(&d);return count;
}
