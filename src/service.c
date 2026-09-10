#include "app.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
static void event(DB *d,Id actor,const char *action,Id entity,const char *request){db_run(d,"INSERT INTO operation_events(actor_id,action,entity_id,request_id,created_at) VALUES(?,?,?,?,?)","isisi",actor,action,entity,request,now_sec());}
static void fault(const Config *c,const char *stage,const char *key){
#ifdef TEST_FAULTS
 if(c->fault&&c->fault_request&&!strcmp(c->fault,stage)&&!strcmp(c->fault_request,key))_Exit(!strcmp(stage,"cancel-before-promote")?86:87);
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
 db_run(d,"UPDATE waitlist SET status='PROMOTED',promoted_reservation_id=? WHERE id=?","ii",rid,wid);event(d,actor,"PROMOTE",rid,key);return rid;
}
static Result ok_id(const char *field,Id value){cJSON *j=cJSON_CreateObject();jid(j,field,value);return result(200,"OK","操作成功",j);}
static Result slot_full(DB *d,Id slot,const char *message){
 cJSON *list=db_rows(d,"SELECT s.id,s.start_at,s.end_at FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.lab_id=(SELECT lab_id FROM slots WHERE id=?) AND s.start_at>(SELECT start_at FROM slots WHERE id=?) AND s.start_at<=(SELECT start_at FROM slots WHERE id=?)+604800 AND s.enabled=1 AND l.enabled=1 AND NOT EXISTS(SELECT 1 FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') ORDER BY s.start_at LIMIT 3","iii",slot,slot,slot);
 if(d->error)return db_failure(d);
 cJSON *data=cJSON_CreateObject();if(!data){cJSON_Delete(list);d->error=SQLITE_NOMEM;return db_failure(d);}
 cJSON_AddItemToObject(data,"alternatives",list);return result(409,"SLOT_FULL",message,data);
}
Result booking(DB *d,const Config *cfg,const User *u,const char *action,Id target,const char *key){
 Result r={500,NULL};cJSON *old=NULL,*row=NULL,*slot=NULL;char canonical[128],digest[65];Id sid=target;
 snprintf(canonical,sizeof canonical,"%s:%lld",action,(long long)target);hash_text(canonical,digest);
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 if(!db_num(d,"SELECT enabled FROM users WHERE id=?","i",u->id)){r=result(401,"UNAUTHORIZED","账号不可用",NULL);goto finish_nosave;}
 old=db_first(d,"SELECT action,payload_digest,http_status,result_json FROM request_receipts WHERE user_id=? AND request_id=?","is",u->id,key);
 if(d->error)goto failed;
 if(old){
  if(strcmp(jstr(old,"action"),action)||strcmp(jstr(old,"payload_digest"),digest))r=result(409,"REQUEST_ID_CONFLICT","请求编号已用于其他操作",NULL);
  else {r.status=cJSON_GetObjectItemCaseSensitive(old,"http_status")->valueint;r.body=cJSON_Parse(jstr(old,"result_json"));if(!r.body){d->error=SQLITE_CORRUPT;goto failed;}}
  goto finish_nosave;
 }
 if(!strcmp(action,"cancel")||!strcmp(action,"withdraw")){
  row=db_first(d,!strcmp(action,"cancel")?"SELECT user_id,slot_id,status FROM reservations WHERE id=?":"SELECT user_id,slot_id,status FROM waitlist WHERE id=?","i",target);
  if(d->error)goto failed;
  if(!row){r=result(404,"NOT_FOUND","记录不存在",NULL);goto save;}
  Id owner=0;parse_id(jstr(row,"user_id"),&owner);parse_id(jstr(row,"slot_id"),&sid);
  if(owner!=u->id){r=result(403,"FORBIDDEN","只能操作自己的记录",NULL);goto save;}
 }
 slot=db_first(d,"SELECT s.id,s.start_at,s.enabled,l.enabled AS lab_enabled FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.id=?","i",sid);
 if(d->error)goto failed;
 if(!slot){r=result(404,"NOT_FOUND","场次不存在",NULL);goto save;}
 if((Id)cJSON_GetObjectItemCaseSensitive(slot,"start_at")->valuedouble<=now_sec()) {r=result(409,"STATE_CONFLICT","场次已开始，不能再修改",NULL);goto save;}
 if((!strcmp(action,"reserve")||!strcmp(action,"wait"))&&(!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(slot,"enabled"))||!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(slot,"lab_enabled")))){
  r=result(409,"STATE_CONFLICT","场次未开放",NULL);goto save;
 }
 if(!strcmp(action,"reserve")){
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",sid)) {r=slot_full(d,sid,"该场次已被预约，可加入候补或选择替代时段");goto save;}
  Id promoted=promote(d,sid,u->id,key);if(d->error)goto failed;
  if(promoted){r=slot_full(d,sid,"名额已按顺序分配给候补用户，可选择替代时段");goto save;}
  if(!db_run(d,"INSERT INTO reservations(user_id,slot_id,status,source,created_at) VALUES(?,?,'CONFIRMED','DIRECT',?)","iii",u->id,sid,now_sec()))goto failed;
  Id rid=sqlite3_last_insert_rowid(d->sql);event(d,u->id,"RESERVE",rid,key);r=ok_id("reservation_id",rid);
 }else if(!strcmp(action,"wait")){
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=? AND status='CONFIRMED'","ii",sid,u->id)){r=result(409,"ALREADY_RESERVED","你已预约该场次",NULL);goto save;}
  if(!db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",sid)){r=result(409,"SLOT_AVAILABLE","场次当前空闲，请直接预约",NULL);goto save;}
  Id wid=db_num(d,"SELECT id FROM waitlist WHERE user_id=? AND slot_id=? AND status='WAITING'","ii",u->id,sid);
  if(!wid){db_run(d,"INSERT INTO waitlist(user_id,slot_id,status,created_at) VALUES(?,?,'WAITING',?)","iii",u->id,sid,now_sec());wid=sqlite3_last_insert_rowid(d->sql);event(d,u->id,"WAIT",wid,key);}
  r=ok_id("waitlist_id",wid);
 }else if(!strcmp(action,"cancel")){
  Id promoted=0;
  if(!strcmp(jstr(row,"status"),"CONFIRMED")){
   if(!db_run(d,"UPDATE reservations SET status='CANCELLED',cancelled_at=? WHERE id=?","ii",now_sec(),target))goto failed;
   fault(cfg,"cancel-before-promote",key);event(d,u->id,"CANCEL",target,key);promoted=promote(d,sid,u->id,key);
  }
  cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);if(promoted)jid(j,"promoted_reservation_id",promoted);else cJSON_AddNullToObject(j,"promoted_reservation_id");r=result(200,"OK","预约已取消",j);
 }else if(!strcmp(action,"withdraw")){
  if(!strcmp(jstr(row,"status"),"WAITING")){db_run(d,"UPDATE waitlist SET status='WITHDRAWN' WHERE id=?","i",target);event(d,u->id,"WITHDRAW",target,key);}
  else if(strcmp(jstr(row,"status"),"WITHDRAWN")){r=result(409,"STATE_CONFLICT","候补已结束，若已补位请取消对应预约",NULL);goto save;}
  r=ok_id("waitlist_id",target);
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
Result records(DB *d,const User *u,int all,Id date){
 const char *where=all?"(?=0 OR (s.start_at>=? AND s.start_at<?))":"r.user_id=?";
 char sql[1800];
 snprintf(sql,sizeof sql,"SELECT r.id,r.slot_id,l.name AS lab_name,u.username,s.start_at,s.end_at,r.status,r.source FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=r.user_id WHERE %s ORDER BY r.id DESC LIMIT 1000",where);
 cJSON *a=all?db_rows(d,sql,"iii",date,date,date+86400):db_rows(d,sql,"i",u->id);
 snprintf(sql,sizeof sql,"SELECT r.id,r.slot_id,l.name AS lab_name,u.username,s.start_at,s.end_at,r.status,CASE WHEN r.status='WAITING' THEN (SELECT count(*) FROM waitlist w JOIN users wu ON wu.id=w.user_id WHERE w.slot_id=r.slot_id AND w.status='WAITING' AND w.id<=r.id AND wu.enabled=1) ELSE 0 END AS position FROM waitlist r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=r.user_id WHERE %s ORDER BY r.id DESC LIMIT 1000",where);
 cJSON *b=all?db_rows(d,sql,"iii",date,date,date+86400):db_rows(d,sql,"i",u->id);
 cJSON *e=all?db_rows(d,"SELECT e.id,u.username AS actor,e.action,e.entity_id,e.request_id,e.created_at FROM operation_events e JOIN users u ON u.id=e.actor_id ORDER BY e.id DESC LIMIT 200",""):cJSON_CreateArray();
 if(d->error){cJSON_Delete(a);cJSON_Delete(b);cJSON_Delete(e);return db_failure(d);}
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"reservations",a);cJSON_AddItemToObject(j,"waitlist",b);cJSON_AddItemToObject(j,"events",e);return result(200,"OK","查询成功",j);
}
Result stats(DB *d,Id start,Id end){
 cJSON *days=db_rows(d,"SELECT (s.start_at+28800)/86400 AS day,count(DISTINCT s.id) AS slots,count(DISTINCT CASE WHEN r.status='CONFIRMED' THEN r.id END) AS confirmed,count(DISTINCT CASE WHEN r.status='CANCELLED' THEN r.id END) AS cancelled FROM slots s LEFT JOIN reservations r ON r.slot_id=s.id WHERE s.start_at>=? AND s.start_at<? GROUP BY day ORDER BY day","ii",start,end+86400);
 if(d->error)return db_failure(d);
 cJSON *waiting=db_rows(d,"SELECT (s.start_at+28800)/86400 AS day,count(*) AS waiting FROM waitlist w JOIN slots s ON s.id=w.slot_id JOIN users u ON u.id=w.user_id WHERE s.start_at>=? AND s.start_at<? AND w.status='WAITING' AND u.enabled=1 GROUP BY day ORDER BY day","ii",start,end+86400);
 if(d->error){cJSON_Delete(days);return db_failure(d);}
 cJSON *rows=cJSON_CreateArray(),*totals=cJSON_CreateObject();
 if(rows&&totals){
  double ts=0,tc=0,tx=0,tw=0;cJSON *it;
  cJSON_ArrayForEach(it,days){
   cJSON *row=cJSON_CreateObject();if(!row)break;
   char date[11];date_text((Id)cJSON_GetObjectItemCaseSensitive(it,"day")->valuedouble,date);
   double w=0;cJSON *jt;cJSON_ArrayForEach(jt,waiting)if((Id)cJSON_GetObjectItemCaseSensitive(jt,"day")->valuedouble==(Id)cJSON_GetObjectItemCaseSensitive(it,"day")->valuedouble){w=cJSON_GetObjectItemCaseSensitive(jt,"waiting")->valuedouble;break;}
   double s=cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble,c=cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble,x=cJSON_GetObjectItemCaseSensitive(it,"cancelled")->valuedouble;
   cJSON_AddStringToObject(row,"date",date);cJSON_AddNumberToObject(row,"slots",s);cJSON_AddNumberToObject(row,"confirmed",c);cJSON_AddNumberToObject(row,"cancelled",x);cJSON_AddNumberToObject(row,"waiting",w);
   cJSON_AddItemToArray(rows,row);ts+=s;tc+=c;tx+=x;tw+=w;
  }
  cJSON_AddNumberToObject(totals,"slots",ts);cJSON_AddNumberToObject(totals,"confirmed",tc);cJSON_AddNumberToObject(totals,"cancelled",tx);cJSON_AddNumberToObject(totals,"waiting",tw);
 }
 cJSON_Delete(days);cJSON_Delete(waiting);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"stats",rows);cJSON_AddItemToObject(j,"totals",totals);
 return result(200,"OK","查询成功",j);
}
