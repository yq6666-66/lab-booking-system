#include "app.h"
#include <sodium.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
static void event(DB *d,Id actor,const char *action,Id entity,const char *request){db_run(d,"INSERT INTO operation_events(actor_id,action,entity_id,request_id,created_at) VALUES(?,?,?,?,?)","isisi",actor,action,entity,request,now_sec());}
void notify(DB *d,Id user,const char *kind,const char *title,const char *body,Id slot,Id reservation){db_run(d,"INSERT INTO notifications(user_id,kind,title,body,slot_id,reservation_id,created_at) VALUES(?,?,?,?,?,?,?)","isssiii",user,kind,title,body,slot,reservation,now_sec());}
static void slot_when(DB *d,Id slot,char out[40]){
 Id st=db_num(d,"SELECT start_at FROM slots WHERE id=?","i",slot);
 if(!st){snprintf(out,40,"场次 %lld",(long long)slot);return;}
 Id bj=st+28800;char day[11];date_text(bj/86400,day);
 snprintf(out,40,"%s %02d:%02d",day,(int)((bj%86400)/3600),(int)((bj%3600)/60));
}
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
 db_run(d,"UPDATE waitlist SET status='PROMOTED',promoted_reservation_id=? WHERE id=?","ii",rid,wid);event(d,actor,"PROMOTE",rid,key);
 char when[40],body[192];slot_when(d,slot,when);snprintf(body,sizeof body,"你候补的场次 %s 已补位成功，预约编号 %lld，请按时到场并在签到时间内签到。",when,(long long)rid);
 notify(d,uid,"PROMOTED","候补补位成功",body,slot,rid);
 return rid;
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
 if(!strcmp(action,"cancel")||!strcmp(action,"withdraw")||!strcmp(action,"checkin")){
  const char *sel=!strcmp(action,"withdraw")?"SELECT user_id,slot_id,status FROM waitlist WHERE id=?":!strcmp(action,"checkin")?"SELECT user_id,slot_id,status,checked_in_at,source,created_at FROM reservations WHERE id=?":"SELECT user_id,slot_id,status FROM reservations WHERE id=?";
  row=db_first(d,sel,"i",target);
  if(d->error)goto failed;
  if(!row){r=result(404,"NOT_FOUND","记录不存在",NULL);goto save;}
  Id owner=0;parse_id(jstr(row,"user_id"),&owner);parse_id(jstr(row,"slot_id"),&sid);
  if(owner!=u->id){r=result(403,"FORBIDDEN","只能操作自己的记录",NULL);goto save;}
 }
 slot=db_first(d,"SELECT s.id,s.start_at,s.enabled,l.enabled AS lab_enabled FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.id=?","i",sid);
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
   if(!db_run(d,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='USER' WHERE id=?","ii",now_sec(),target))goto failed;
   fault(cfg,"cancel-before-promote",key);event(d,u->id,"CANCEL",target,key);promoted=promote(d,sid,u->id,key);
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
Result records(DB *d,const User *u,int all,Id date,int page,int size){
 const char *where=all?"(?=0 OR (s.start_at>=? AND s.start_at<?))":"r.user_id=?";
 char sql[1800];
 Id limit=(Id)size+1,offset=(Id)(page-1)*size;int more=0;
 snprintf(sql,sizeof sql,"SELECT r.id,r.slot_id,l.name AS lab_name,u.username,s.start_at,s.end_at,r.status,r.source,r.cancel_reason,r.checked_in_at FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=r.user_id WHERE %s ORDER BY r.id DESC LIMIT ? OFFSET ?",where);
 cJSON *a=all?db_rows(d,sql,"iiiii",date,date,date+86400,limit,offset):db_rows(d,sql,"iii",u->id,limit,offset);
 if(a&&cJSON_GetArraySize(a)>size){cJSON_DeleteItemFromArray(a,(int)size);more=1;}
 snprintf(sql,sizeof sql,"SELECT r.id,r.slot_id,l.name AS lab_name,u.username,s.start_at,s.end_at,r.status,CASE WHEN r.status='WAITING' THEN (SELECT count(*) FROM waitlist w JOIN users wu ON wu.id=w.user_id WHERE w.slot_id=r.slot_id AND w.status='WAITING' AND w.id<=r.id AND wu.enabled=1) ELSE 0 END AS position FROM waitlist r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=r.user_id WHERE %s ORDER BY r.id DESC LIMIT ? OFFSET ?",where);
 cJSON *b=all?db_rows(d,sql,"iiiii",date,date,date+86400,limit,offset):db_rows(d,sql,"iii",u->id,limit,offset);
 if(b&&cJSON_GetArraySize(b)>size){cJSON_DeleteItemFromArray(b,(int)size);more=1;}
 cJSON *e=all?db_rows(d,"SELECT e.id,u.username AS actor,e.action,e.entity_id,e.request_id,e.created_at FROM operation_events e JOIN users u ON u.id=e.actor_id ORDER BY e.id DESC LIMIT ? OFFSET ?","ii",limit,offset):cJSON_CreateArray();
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
  if(live)promote(&d,sid,uid,NULL);
  count++;
 }
 cJSON_Delete(due);
 if(d.error){sqlite3_exec(d.sql,"ROLLBACK",NULL,NULL,NULL);db_close(&d);return count;}
 if(!db_run(&d,"COMMIT","")){db_close(&d);return count;}
 db_close(&d);return count;
}
