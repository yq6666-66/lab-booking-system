/* service.c —— 平台域：通知渠道/outbox、会话与凭据、用户管理、扫描与提醒、操作审计助手。
   （r24 规划 P2-5 自单文件拆分：booking/asset/stats/token 各域独立成文件；函数体未改，仅移动） */
#include "service.h"
#include <sodium.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
void event(DB *d,Id actor,const char *action,Id entity,const char *request){db_run(d,"INSERT INTO operation_events(actor_id,action,entity_id,request_id,created_at) VALUES(?,?,?,?,?)","isisi",actor,action,entity,request,now_sec());}
/* -- r24 P2-6 通知渠道插件化 + outbox：inbox 为默认且必需渠道（站内信，行为与拆分前一致）；
      出站渠道经渠道表（函数指针）派发，默认关闭、零行为变化。--notify-file 开启 file 渠道后：
      notify() 在业务事务内同写 outbox（payload 快照），业务提交后由扫描周期派发到文件（JSONL），
      失败记 attempts/last_error 等待下轮重试；派发在自动提交模式运行，绝不影响业务事务。 -- */
static const char *notify_file=NULL;
void notify_configure(const Config *cfg){notify_file=cfg?cfg->notify_file:NULL;}
typedef struct{const char *name;void (*send)(const char *target,const char *payload,char *err,size_t errsz);}OutboxChannel;
static void outbox_file_send(const char *target,const char *payload,char *err,size_t errsz){
 FILE *f=fopen(target,"a");
 if(!f){snprintf(err,errsz,"open failed");return;}
 fputs(payload,f);fputc('\n',f);
 if(ferror(f)){snprintf(err,errsz,"write failed");fclose(f);return;}
 if(fclose(f)!=0){snprintf(err,errsz,"close failed");return;}
}
static const OutboxChannel OUTBOX_CHANNELS[]={{"file",outbox_file_send}};
int notify(DB *d,Id user,const char *kind,const char *title,const char *body,Id slot,Id reservation){
 if(!db_run(d,"INSERT INTO notifications(user_id,kind,title,body,slot_id,reservation_id,created_at) VALUES(?,?,?,?,?,?,?)","isssiii",user,kind,title,body,slot,reservation,now_sec()))return 0;
 if(!notify_file)return 1;
 Id nid=sqlite3_last_insert_rowid(d->sql);
 cJSON *p=cJSON_CreateObject();
 if(p){
  cJSON_AddStringToObject(p,"channel","file");
  cJSON_AddNumberToObject(p,"user_id",(double)user);
  cJSON_AddStringToObject(p,"kind",kind?kind:"");
  cJSON_AddStringToObject(p,"title",title?title:"");
  cJSON_AddStringToObject(p,"body",body?body:"");
  if(slot)cJSON_AddNumberToObject(p,"slot_id",(double)slot);
  if(reservation)cJSON_AddNumberToObject(p,"reservation_id",(double)reservation);
  char *s=cJSON_PrintUnformatted(p);
  if(s){db_run(d,"INSERT INTO outbox(notification_id,channel,payload,created_at) VALUES(?,?,?,?)","issi",nid,"file",s,now_sec());cJSON_free(s);}
  cJSON_Delete(p);
 }
 return 1;
}
/* 提交后派发：仅扫描线程在自动提交模式调用；单行失败记 last_error/attempts 后继续下一行，
   任何派发错误都不外泄（d->error 复位），保证业务路径不受出站渠道健康度影响。 */
static void outbox_dispatch(DB *d){
 if(!notify_file)return;
 cJSON *rows=db_rows(d,"SELECT id,channel,payload FROM outbox WHERE sent_at IS NULL ORDER BY id LIMIT 100","");
 if(!rows||d->error){cJSON_Delete(rows);d->error=0;return;}
 cJSON *it;cJSON_ArrayForEach(it,rows){
  Id id=0;if(!parse_id(jstr(it,"id"),&id))continue;
  const char *chn=jstr(it,"channel");
  const OutboxChannel *ch=NULL;
  for(size_t i=0;i<sizeof OUTBOX_CHANNELS/sizeof *OUTBOX_CHANNELS;i++)if(chn&&!strcmp(OUTBOX_CHANNELS[i].name,chn)){ch=&OUTBOX_CHANNELS[i];break;}
  char err[96]="";
  if(ch)ch->send(notify_file,jstr(it,"payload"),err,sizeof err);
  else snprintf(err,sizeof err,"unknown channel");
  if(!err[0])db_run(d,"UPDATE outbox SET sent_at=?,attempts=attempts+1 WHERE id=?","ii",now_sec(),id);
  else db_run(d,"UPDATE outbox SET attempts=attempts+1,last_error=? WHERE id=?","si",err,id);
 }
 cJSON_Delete(rows);
 d->error=0;
}
void slot_when(DB *d,Id slot,char out[40]){
 Id st=db_num(d,"SELECT start_at FROM slots WHERE id=?","i",slot);
 if(!st){snprintf(out,40,"场次 %lld",(long long)slot);return;}
 Id bj=st+28800;char day[11];date_text(bj/86400,day);
 snprintf(out,40,"%s %02d:%02d",day,(int)((bj%86400)/3600),(int)((bj%3600)/60));
}
void fault(const Config *c,const char *stage,const char *key){
#ifdef TEST_FAULTS
 if(c->fault&&!strcmp(c->fault,stage)){
  if(!strcmp(stage,"sweep-mid"))_Exit(88); /* 扫描事务中途崩溃：验证回收+补位的原子性 */
  if(c->fault_request&&key&&!strcmp(c->fault_request,key))_Exit(!strcmp(stage,"cancel-before-promote")?86:87);
 }
#else
 (void)c;(void)stage;(void)key;
#endif
}
Result ok_id(const char *field,Id value){cJSON *j=cJSON_CreateObject();jid(j,field,value);return result(200,"OK","操作成功",j);}
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
 db_run(d,"DELETE FROM api_tokens WHERE user_id=?","i",u->id); /* 凭据生命周期：改密后 API 令牌一并吊销 */
 Id revoked_tokens=(Id)sqlite3_changes(d->sql);
 event(d,u->id,"PASSWORD_CHANGE",u->id,key);
 sodium_memzero(hash,sizeof hash);
 if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 if(!db_run(d,"COMMIT",""))return db_failure(d);
 cJSON *j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"revoked_sessions",(double)revoked);cJSON_AddNumberToObject(j,"revoked_tokens",(double)revoked_tokens);
 return result(200,"OK","密码已更新，其他设备的登录与 API 令牌已失效",j);
}
/* 签到窗口结束仍未签到的预约：标记爽约并释放名额；场次尚未结束时按 FIFO 补位。 */
int sweep_once(const Config *config){
 DB d={0};int count=0;
 if(!db_open(&d,config->db_path)){db_close(&d);return 0;}
 Id now=now_sec();
 outbox_dispatch(&d); /* r24 P2-6：先派发此前业务已提交的 outbox 行（自动提交，非业务事务内） */
 /* r24 HELD 超时回收：保留截止已过（或时段已开始）的待确认预约转 EXPIRED，
    释放后立即对同一场次重新递补，形成"超时即让位"的自然重试环。默认 hold_window=0 时无 HELD，此块空转。 */
 {cJSON *held=db_rows(&d,"SELECT r.id,r.user_id,r.slot_id FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE r.status='HELD' AND ((r.hold_deadline IS NOT NULL AND r.hold_deadline<=?) OR s.start_at<=?)","ii",now,now);
  if(held){cJSON *hit;cJSON_ArrayForEach(hit,held){
   Id hrid=0,huid=0,hsid=0;
   parse_id(jstr(hit,"id"),&hrid);parse_id(jstr(hit,"user_id"),&huid);parse_id(jstr(hit,"slot_id"),&hsid);
   if(!db_run(&d,"UPDATE reservations SET status='EXPIRED',cancelled_at=?,hold_deadline=NULL WHERE id=? AND status='HELD'","ii",now,hrid))break;
   if(!sqlite3_changes(d.sql))continue;
   event(&d,huid,"HOLD_EXPIRE",hrid,NULL);
   notify(&d,huid,"NOTICE","名额保留已超时","你保留的候补名额已超时释放，可重新申请或再次加入候补。",hsid,hrid);
   promote_fill(&d,config,hsid,huid,NULL);
   if(d.error)break;
   count++;
  }
  cJSON_Delete(held);}
 }
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
  credit_apply(&d,uid,-1,"NO_SHOW",rid); /* r23 爽约额外扣 1 点（预约时已扣 1 点且不返还，合计净 -2） */
  if(live)promote_fill(&d,config,sid,uid,NULL);
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
 outbox_dispatch(&d); /* r24 P2-6：扫描自身事务提交后立即派发，缩短出站延迟 */
 db_close(&d);return count;
}
/* -- r11 用户管理与运营增强 -- */
/* 管理员用户列表：含有效预约数与候补数；q 为用户名前缀过滤（substr 比较，避免 LIKE 通配符转义问题）。 */
Result users_list(DB *d,int page,int size,const char *q){
 cJSON *j=cJSON_CreateObject();
 char sql[640];Id limit=(Id)size+1,offset=(Id)(page-1)*size;
 Id window_start=now_sec()-(Id)PENALTY_DAYS*86400;
 snprintf(sql,sizeof sql,"SELECT u.id,u.username,u.role,u.enabled,(SELECT count(*) FROM reservations r WHERE r.user_id=u.id AND r.status IN('CONFIRMED','HELD')) AS reservations,(SELECT count(*) FROM waitlist w WHERE w.user_id=u.id AND w.status='WAITING') AS waitlisted,(SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=u.id AND r.cancel_reason='NO_SHOW' AND s2.start_at>=?) AS no_show_count FROM users u %sORDER BY u.id LIMIT ? OFFSET ?",
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
  db_run(d,"DELETE FROM api_tokens WHERE user_id=?","i",target); /* 凭据生命周期：停用即吊销 API 令牌（令牌校验虽查 enabled=1，仍应清除） */
  event(d,actor->id,"USER_DISABLE",target,NULL);
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  if(!db_run(d,"COMMIT",""))return db_failure(d);
  log_write(1,"ADMIN user %lld disabled by %lld",(long long)target,(long long)actor->id);
  cJSON *j=cJSON_CreateObject();jid(j,"user_id",target);cJSON_AddBoolToObject(j,"enabled",0);
  return result(200,"OK","账号已停用，该用户全部会话与 API 令牌已失效",j);
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
  db_run(d,"DELETE FROM api_tokens WHERE user_id=?","i",target); /* 凭据生命周期：重置密码后 API 令牌一并吊销 */
  event(d,actor->id,"RESET_PW",target,NULL);
  if(d->error){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  if(!db_run(d,"COMMIT",""))return db_failure(d);
  log_write(1,"ADMIN password reset for user %lld by %lld",(long long)target,(long long)actor->id);
  cJSON *j=cJSON_CreateObject();jid(j,"user_id",target);cJSON_AddStringToObject(j,"password",pw);
  sodium_memzero(pw,sizeof pw);
  return result(200,"OK","密码已重置，新口令仅显示这一次，该用户 API 令牌已吊销",j);
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
 cJSON *due=db_rows(&d,"SELECT DISTINCT s.id FROM slots s JOIN reservations r ON r.slot_id=s.id AND r.status IN('CONFIRMED','HELD') WHERE s.reminded_at IS NULL AND s.start_at>? AND s.start_at<=?","ii",now,now+(Id)config->remind_sec);
 if(!due||!cJSON_GetArraySize(due)){cJSON_Delete(due);db_close(&d);return 0;}
 if(!db_run(&d,"BEGIN IMMEDIATE","")){cJSON_Delete(due);db_close(&d);return 0;}
 cJSON *it;cJSON_ArrayForEach(it,due){
  Id sid=0;if(!parse_id(jstr(it,"id"),&sid))continue;
  cJSON *rs=db_rows(&d,"SELECT id,user_id FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",sid);
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
