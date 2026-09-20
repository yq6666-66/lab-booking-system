/* asset.c —— 资源/时段/维护/资格域（r24 规划 P2-5 自 service.c 拆分；函数体未改，仅移动） */
#include "service.h"
#include <string.h>
#include <stdio.h>
/* ===================== r23 新增能力 ===================== */
/* -- 资源可用时段（对标 working plans）：与场次时段正交，表达"该资源在这些时段可用"。
      未配置任何时段时视为全天可用（向后兼容）；配置后场次起点须落在某个匹配窗口内。 -- */
int asset_window_ok(DB *d,Id asset,Id start_at){
 Id n=db_num(d,"SELECT count(*) FROM asset_windows WHERE asset_id=?","i",asset);
 if(d->error)return 0;
 if(!n)return 1;
 int wd=(int)((((start_at+28800)/86400)+3)%7);
 Id smin=(start_at+28800)%86400/60;
 Id hit=db_num(d,"SELECT count(*) FROM asset_windows WHERE asset_id=? AND ((weekday_mask>>?)&1)=1 AND start_minute<=? AND end_minute>=?","iiii",asset,(Id)wd,smin,smin+60);
 if(d->error)return 0;
 return hit>0;
}
Result asset_windows_list(DB *d,Id asset){
 cJSON *rows=db_rows(d,"SELECT id,asset_id,weekday_mask,start_minute,end_minute,reason FROM asset_windows WHERE asset_id=? ORDER BY id","i",asset);
 if(d->error)return db_failure(d);
 if(!rows)rows=cJSON_CreateArray();
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"windows",rows);
 return result(200,"OK","查询成功",j);
}
/* 时段维护：不带 id 为新增，带 id 为删除（时段属低频配置，增/删已足够）。 */
Result asset_window_admin(DB *d,const User *u,Id asset,const cJSON *body){
 if(!db_num(d,"SELECT count(*) FROM assets WHERE id=?","i",asset))return result(404,"NOT_FOUND","资源不存在",NULL);
 if(d->error)return db_failure(d);
 Id wid=0;
 if(parse_id(jstr(body,"id"),&wid)&&wid>0){
  if(!db_run(d,"DELETE FROM asset_windows WHERE id=? AND asset_id=?","ii",wid,asset))return db_failure(d);
  if(!sqlite3_changes(d->sql))return result(404,"NOT_FOUND","时段不存在",NULL);
  event(d,u->id,"WINDOW_DEL",asset,NULL);return ok_id("removed",1);
 }
 Id mask=127,smin=0,emin=1440;
 cJSON *m=cJSON_GetObjectItemCaseSensitive(body,"weekday_mask");
 if(m&&cJSON_IsNumber(m))mask=(Id)m->valuedouble;
 cJSON *a=cJSON_GetObjectItemCaseSensitive(body,"start_minute"),*b=cJSON_GetObjectItemCaseSensitive(body,"end_minute");
 if(a&&cJSON_IsNumber(a))smin=(Id)a->valuedouble;
 if(b&&cJSON_IsNumber(b))emin=(Id)b->valuedouble;
 if(mask<0||mask>127||smin<0||emin>1440||emin<=smin)return result(400,"INVALID_INPUT","时段参数不合法",NULL);
 if(!db_run(d,"INSERT INTO asset_windows(asset_id,weekday_mask,start_minute,end_minute,reason,created_at) VALUES(?,?,?,?,?,?)","iiiisi",asset,mask,smin,emin,jstr(body,"reason")?jstr(body,"reason"):"",now_sec()))return db_failure(d);
 Id id=sqlite3_last_insert_rowid(d->sql);
 event(d,u->id,"WINDOW_ADD",asset,NULL);
 cJSON *j=cJSON_CreateObject();jid(j,"window_id",id);return result(200,"OK","时段已保存",j);
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
 if(has_cap){Id taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",target);
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
/* -- 资源维护工单：开启时把资源置为维修中，关闭时恢复可用，形成设备生命周期闭环。 -- */
Result asset_maintenance_list(DB *d,Id asset){
 cJSON *rows=db_rows(d,"SELECT m.id,m.asset_id,m.started_at,m.ended_at,m.reason,u.username AS operator FROM asset_maintenance m LEFT JOIN users u ON u.id=m.operator_id WHERE m.asset_id=? ORDER BY m.id DESC LIMIT 50","i",asset);
 if(d->error)return db_failure(d);
 if(!rows)rows=cJSON_CreateArray();
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"maintenance",rows);
 return result(200,"OK","查询成功",j);
}
Result asset_maintenance_admin(DB *d,const User *u,Id asset,const cJSON *body){
 if(!db_num(d,"SELECT count(*) FROM assets WHERE id=?","i",asset))return result(404,"NOT_FOUND","资源不存在",NULL);
 if(d->error)return db_failure(d);
 const char *op=jstr(body,"op");
 if(!op||(strcmp(op,"open")&&strcmp(op,"close")))return result(400,"INVALID_INPUT","op 需为 open 或 close",NULL);
 if(!strcmp(op,"open")){
  Id opened=db_num(d,"SELECT COALESCE(MAX(id),0) FROM asset_maintenance WHERE asset_id=? AND ended_at IS NULL","i",asset);
  if(d->error)return db_failure(d);
  if(opened)return result(409,"STATE_CONFLICT","该资源已有未关闭的维护工单",NULL);
  if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
  if(!db_run(d,"INSERT INTO asset_maintenance(asset_id,started_at,reason,operator_id) VALUES(?,?,?,?)","iisi",asset,now_sec(),jstr(body,"reason")?jstr(body,"reason"):"",u->id))goto maint_fail;
  Id id=sqlite3_last_insert_rowid(d->sql);
  db_run(d,"UPDATE assets SET status='MAINTENANCE' WHERE id=?","i",asset);
  event(d,u->id,"MAINT_OPEN",asset,NULL);
  /* r32 影响通知：该资源全部未来有效声明的持有者逐场告知（既有预约不受影响，但用户应知情以便改期） */
  {cJSON *aff=db_rows(d,"SELECT r.user_id,r.id AS reservation_id,r.slot_id,a.name AS asset_name FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id JOIN slots s ON s.id=r.slot_id JOIN assets a ON a.id=c.asset_id WHERE c.asset_id=? AND r.status IN('CONFIRMED','HELD') AND s.start_at>?","ii",asset,now_sec());
   if(!aff)goto maint_fail;
   cJSON *ait;Id affected=0;
   cJSON_ArrayForEach(ait,aff){
    Id uid2=0,rid2=0,slot2=0;
    parse_id(jstr(ait,"user_id"),&uid2);parse_id(jstr(ait,"reservation_id"),&rid2);parse_id(jstr(ait,"slot_id"),&slot2);
    char nb[256];snprintf(nb,sizeof nb,"你一场即将开始场次所声明的设备「%s」已转入维护（既有预约仍有效），如需调整请及时改期。",jstr(ait,"asset_name"));
    notify(d,uid2,"NOTICE","设备维护提醒",nb,slot2,rid2);
    affected++;
   }
   cJSON_Delete(aff);
   if(d->error)goto maint_fail;
   if(!db_run(d,"COMMIT",""))goto maint_fail;
   cJSON *j=cJSON_CreateObject();jid(j,"maintenance_id",id);cJSON_AddNumberToObject(j,"affected",(double)affected);
   return result(200,"OK","维护工单已开启，资源已置为维修中，受影响声明人已通知",j);
  }
 }
 Id opened=db_num(d,"SELECT COALESCE(MAX(id),0) FROM asset_maintenance WHERE asset_id=? AND ended_at IS NULL","i",asset);
 if(d->error)return db_failure(d);
 if(!opened)return result(404,"NOT_FOUND","没有未关闭的维护工单",NULL);
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 if(!db_run(d,"UPDATE asset_maintenance SET ended_at=? WHERE id=?","ii",now_sec(),opened))goto maint_fail;
 db_run(d,"UPDATE assets SET status='AVAILABLE' WHERE id=? AND status='MAINTENANCE'","i",asset);
 event(d,u->id,"MAINT_CLOSE",asset,NULL);
 {cJSON *aff=db_rows(d,"SELECT r.user_id,r.id AS reservation_id,r.slot_id,a.name AS asset_name FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id JOIN slots s ON s.id=r.slot_id JOIN assets a ON a.id=c.asset_id WHERE c.asset_id=? AND r.status IN('CONFIRMED','HELD') AND s.start_at>?","ii",asset,now_sec());
  if(!aff)goto maint_fail;
  cJSON *ait;Id affected=0;
  cJSON_ArrayForEach(ait,aff){
   Id uid2=0,rid2=0,slot2=0;
   parse_id(jstr(ait,"user_id"),&uid2);parse_id(jstr(ait,"reservation_id"),&rid2);parse_id(jstr(ait,"slot_id"),&slot2);
   char nb[256];snprintf(nb,sizeof nb,"设备「%s」维护已完成、恢复可用，你的预约不受影响。",jstr(ait,"asset_name"));
   notify(d,uid2,"NOTICE","设备恢复可用",nb,slot2,rid2);
   affected++;
  }
  cJSON_Delete(aff);
  if(d->error)goto maint_fail;
  if(!db_run(d,"COMMIT",""))goto maint_fail;
  cJSON *j=cJSON_CreateObject();jid(j,"closed",opened);cJSON_AddNumberToObject(j,"affected",(double)affected);
  return result(200,"OK","维护工单已关闭，资源恢复可用，受影响声明人已通知",j);
 }
maint_fail:
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);
}
/* -- r23 跨时段连续预约：同一事务内校验全部场次，全成或全败，避免"订到一半"。
      本路径只做容量/连续性/信用校验；资源声明与审批仍走单场次路径（见 CONTRACT 说明）。 -- */
/* -- r24 资格授权：开启 requires_qualification 的资源，声明前须持有有效资格。
      默认不要求（既有资源 requires_qualification=0），故完全向后兼容。 -- */
Result asset_quals_list(DB *d,Id asset){
 cJSON *rows=db_rows(d,"SELECT q.id,q.user_id,u.username,q.granted_at,q.expires_at,q.note FROM qualifications q JOIN users u ON u.id=q.user_id WHERE q.asset_id=? ORDER BY q.id DESC","i",asset);
 if(d->error)return db_failure(d);
 if(!rows)rows=cJSON_CreateArray();
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"qualifications",rows);
 return result(200,"OK","查询成功",j);
}
Result asset_qual_admin(DB *d,const User *actor,Id asset,const cJSON *body){
 cJSON *ar=db_first(d,"SELECT requires_qualification FROM assets WHERE id=?","i",asset);
 if(d->error)return db_failure(d);
 if(!ar)return result(404,"NOT_FOUND","资源不存在",NULL);
 cJSON_Delete(ar);
 Id uid=0;
 if(!parse_id(jstr(body,"user_id"),&uid))return result(400,"INVALID_INPUT","user_id 不合法",NULL);
 const char *op=jstr(body,"op");if(!op)op="grant";
 if(!strcmp(op,"grant")){
  if(!db_num(d,"SELECT count(*) FROM users WHERE id=?","i",uid))return result(404,"NOT_FOUND","用户不存在",NULL);
  if(d->error)return db_failure(d);
  Id exp=0;cJSON *ex=cJSON_GetObjectItemCaseSensitive(body,"expires_at");
  if(ex&&cJSON_IsNumber(ex))exp=(Id)ex->valuedouble;
  if(!db_run(d,"INSERT INTO qualifications(user_id,asset_id,granted_at,expires_at,note) VALUES(?,?,?,?,?) ON CONFLICT(user_id,asset_id) DO UPDATE SET granted_at=excluded.granted_at,expires_at=excluded.expires_at,note=excluded.note","iiiis",uid,asset,clock_now(),exp,jstr(body,"note")?jstr(body,"note"):""))return db_failure(d);
  event(d,actor->id,"QUAL_GRANT",asset,NULL);
  cJSON *j=cJSON_CreateObject();jid(j,"user_id",uid);return result(200,"OK","资格已授予",j);
 }
 if(!strcmp(op,"require")){
  /* r24 开关：把该资源设为"需资格"或"不需资格" */
  cJSON *rv=cJSON_GetObjectItemCaseSensitive(body,"required");
  int on=(rv&&cJSON_IsBool(rv))?(cJSON_IsTrue(rv)?1:0):1;
  if(!db_run(d,"UPDATE assets SET requires_qualification=? WHERE id=?","ii",(Id)on,asset))return db_failure(d);
  event(d,actor->id,"QUAL_REQUIRE",asset,NULL);
  cJSON *j=cJSON_CreateObject();cJSON_AddBoolToObject(j,"requires_qualification",on);return result(200,"OK","资格要求已更新",j);
 }
 if(!strcmp(op,"revoke")){
  if(!db_run(d,"DELETE FROM qualifications WHERE user_id=? AND asset_id=?","ii",uid,asset))return db_failure(d);
  if(!sqlite3_changes(d->sql))return result(404,"NOT_FOUND","该用户没有此资源的资格",NULL);
  event(d,actor->id,"QUAL_REVOKE",asset,NULL);
  cJSON *j=cJSON_CreateObject();jid(j,"user_id",uid);return result(200,"OK","资格已撤销",j);
 }
 return result(400,"INVALID_INPUT","op 需为 grant 或 revoke",NULL);
}
