/* token.c —— API 令牌/信用账户域（r24 规划 P2-5 自 service.c 拆分；函数体未改，仅移动） */
#include "service.h"
#include <string.h>
#include <stdio.h>
/* r23 信用账户：余额与流水在调用方事务内同增同减，每次变动均可追溯。
   reason ∈ RESERVE/CANCEL/CHECKIN/NO_SHOW/PREEMPTED/WEEKLY/GRANT。 */
int credit_apply(DB *d,Id user,Id delta,const char *reason,Id reservation_id){
 if(!delta)return 1;
 /* 钳制在 0..CREDIT_BASE：信用是"守约激励 + 爽约惩罚"的余额，不因预约而消耗，
    因此不会改变既有预约行为；余额为 0 时才作为惩罚生效（禁止新预约/候补）。 */
 if(!db_run(d,"UPDATE users SET credit=MIN(?,MAX(0,credit+?)) WHERE id=?","iii",(Id)CREDIT_BASE,delta,user))return 0;
 return db_run(d,"INSERT INTO credit_ledger(user_id,delta,reason,reservation_id,created_at) VALUES(?,?,?,?,?)","iisii",user,delta,reason,reservation_id,now_sec());
}
/* r23 每周回补：跨过北京周一 0 点后把余额补至基准额度（只补不扣，不产生零变动流水）。 */
void credit_weekly_topup(DB *d,Id user){
 Id day0=(now_sec()+28800)/86400*86400-28800;
 int wd=(int)(((day0+28800)/86400+3)%7);
 Id week_start=day0-(Id)wd*86400;
 Id last=db_num(d,"SELECT COALESCE(MAX(created_at),0) FROM credit_ledger WHERE user_id=? AND reason='WEEKLY'","i",user);
 if(d->error||last>=week_start)return;
 Id bal=db_num(d,"SELECT credit FROM users WHERE id=?","i",user);
 if(d->error)return;
 if(bal<CREDIT_BASE)if(!credit_apply(d,user,CREDIT_BASE-bal,"WEEKLY",0))d->error=1;
}
/* r20 API 令牌：只读程序化访问（Cal.com API keys 模式）。明文仅创建时返回一次，库中存哈希。 */
Result token_create(DB *d,const User *u,const cJSON *body){
 const char *name=jstr(body,"name");
 if(!name||!strlen(name)||strlen(name)>80)return result(400,"INVALID_INPUT","令牌名称不合法",NULL);
 char raw[65];random_hex(raw);
 raw[32]=0;
 char th[65];hash_text(raw,th);
 if(!db_run(d,"INSERT INTO api_tokens(token_hash,user_id,name,created_at) VALUES(?,?,?,?)","sisi",th,u->id,name,now_sec())){return db_failure(d);}
 event(d,u->id,"TOKEN_CREATE",u->id,NULL);
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"token",raw);cJSON_AddStringToObject(j,"name",name);
 return result(200,"OK","令牌已创建，明文仅此一次显示",j);
}
Result token_revoke(DB *d,const User *u,Id target,const char *request_id){
 (void)request_id;
 int ok=db_run(d,"DELETE FROM api_tokens WHERE id=? AND user_id=?","ii",target,u->id);
 if(!ok)return db_failure(d);
 if(!sqlite3_changes(d->sql))return result(404,"NOT_FOUND","令牌不存在",NULL);
 event(d,u->id,"TOKEN_REVOKE",u->id,NULL);
 return ok_id("revoked",1);
}
Result token_list(DB *d,const User *u){
 cJSON *rows=db_rows(d,"SELECT id,name,created_at,last_used_at FROM api_tokens WHERE user_id=? ORDER BY id DESC","i",u->id);
 if(d->error)return db_failure(d);
 if(!rows)rows=cJSON_CreateArray();
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"tokens",rows);
 return result(200,"OK","查询成功",j);
}
/* r22 X-API-Token 只读访问：以令牌明文换取用户身份，供 GET 路径免 Cookie 认证。
   明文仅创建时返回一次，库内只存 SHA-256 十六进制（与 token_create 一致，长度 32）。
   命中即刷新 last_used_at 以便管理端观察使用情况；不写入操作事件，避免读操作刷屏。 */
Result token_auth(DB *d,const char *raw,User *u){ if(!raw||strlen(raw)!=32)return result(401,"UNAUTHORIZED","令牌无效",NULL);
 char th[65];hash_text(raw,th);
 cJSON *r=db_first(d,"SELECT t.id,u.id AS user_id,u.username,u.role FROM api_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND u.enabled=1","s",th);
 if(d->error)return db_failure(d);
 if(!r)return result(401,"UNAUTHORIZED","令牌无效",NULL);
 memset(u,0,sizeof *u);
 parse_id(jstr(r,"user_id"),&u->id);
 u->admin=!strcmp(jstr(r,"role"),"ADMIN");
 snprintf(u->username,sizeof u->username,"%s",jstr(r,"username"));
 Id tid=0;parse_id(jstr(r,"id"),&tid);cJSON_Delete(r);
 db_run(d,"UPDATE api_tokens SET last_used_at=? WHERE id=?","ii",now_sec(),tid);
 return result(200,"OK","令牌有效",NULL);
}
/* -- r23 信用流水：用户看自己的账本，每次增减都有原因与关联预约，规则对用户透明。 -- */
Result credit_history(DB *d,const User *u,int page,int size){
 Id limit=(Id)size+1,offset=(Id)(page-1)*size;
 cJSON *rows=db_rows(d,"SELECT id,delta,reason,reservation_id,created_at FROM credit_ledger WHERE user_id=? ORDER BY id DESC LIMIT ? OFFSET ?","iii",u->id,limit,offset);
 if(d->error)return db_failure(d);
 int more=0;
 if(rows&&cJSON_GetArraySize(rows)>size){cJSON_DeleteItemFromArray(rows,(int)size);more=1;}
 Id bal=db_num(d,"SELECT credit FROM users WHERE id=?","i",u->id);
 if(d->error){cJSON_Delete(rows);return db_failure(d);}
 cJSON *j=cJSON_CreateObject();
 cJSON_AddItemToObject(j,"ledger",rows);
 cJSON_AddNumberToObject(j,"balance",(double)bal);
 cJSON_AddNumberToObject(j,"base",(double)CREDIT_BASE);
 cJSON_AddNumberToObject(j,"page",(double)page);
 cJSON_AddNumberToObject(j,"page_size",(double)size);
 cJSON_AddBoolToObject(j,"has_more",more);
 return result(200,"OK","查询成功",j);
}
/* -- r23 管理员发放信用（补偿/激励）：单次 1..基准额度，写入流水并可追溯。 -- */
Result admin_credit_grant(DB *d,const User *actor,Id target,const cJSON *body){
 if(!db_num(d,"SELECT count(*) FROM users WHERE id=?","i",target))return result(404,"NOT_FOUND","用户不存在",NULL);
 if(d->error)return db_failure(d);
 Id delta=0;cJSON *v=cJSON_GetObjectItemCaseSensitive(body,"delta");
 if(v&&cJSON_IsNumber(v))delta=(Id)v->valuedouble;
 else if(v&&cJSON_IsString(v)&&!parse_id(v->valuestring,&delta))return result(400,"INVALID_INPUT","delta 不合法",NULL);
 if(delta<1||delta>CREDIT_BASE)return result(400,"INVALID_INPUT","单次发放额度需为 1..5",NULL);
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 if(!credit_apply(d,target,delta,"GRANT",0)){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 event(d,actor->id,"CREDIT_GRANT",target,NULL);
 if(!db_run(d,"COMMIT","")){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 {cJSON *j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"granted",(double)delta);return result(200,"OK","信用已发放",j);}
}
