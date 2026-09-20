/* booking.c —— 预约/候补/审批域（r24 规划 P2-5 自 service.c 拆分；函数体未改，仅移动） */
#include "service.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
/* r23 服务端按角色校准优先级：管理员 10、普通用户 0。不接受客户端自报，避免越权插队。 */
static Id role_priority(const User *u){return u->admin?10:0;}
/* r26 知情候补（前向声明，定义在 suggestion_list 附近）：排位与历史转正概率 */
static Id queue_rank(DB *d,Id slot,Id user);
/* r32 审批核心（前向声明，供单条与批量审批共用；假定调用方已开启事务） */
static Result approval_core(DB *d,const User *u,Id target,int approve,const char *key);
static double promote_probability(DB *d,Id slot,Id rank);
/* r24 候补递补（可执行 FIFO）：按 priority DESC,id 顺序扫描候补，账号停用者统一置 SKIPPED；
   临时时间冲突者"暂跳并保留原序号"，只递补第一个当前可执行的候选——队首暂时不可执行时不再阻塞整条队列。
   --waitlist-strategy=strict 时退化为旧语义（队首不可执行则阻塞），供对照实验使用。
   --hold-window>0 时补位先落 HELD（限时保留），需用户确认才生效；默认 0 保持直接确认的既有行为。
   r26 新增 --waitlist-strategy=weighted（老化加权）：候选按 score 降序扫描——
   score = 1000*priority + 200*(credit-5) + 60*log2(1+等待秒/3600)。
   量纲：等待时长每翻倍 +60 分；同优先级下 1 点信用差（200）约等于 8 小时等待可追平；
   管理员(10000)不被普通用户的 aging 越级——老化只解决"同档饿死"，不破坏角色优先级语义。 */
typedef struct{Id wid;Id uid;double score;} WeightedCand;
static Id promote(DB *d,const Config *cfg,Id slot,Id actor,const char *key){
 db_run(d,"UPDATE waitlist SET status='SKIPPED' WHERE slot_id=? AND status='WAITING' AND user_id IN(SELECT id FROM users WHERE enabled=0)","i",slot);
 int weighted=cfg&&cfg->waitlist_strategy==2;
 cJSON *cands=db_rows(d,weighted
  ?"SELECT w.id,w.user_id,w.priority,w.created_at,u.credit FROM waitlist w JOIN users u ON u.id=w.user_id WHERE w.slot_id=? AND w.status='WAITING' ORDER BY w.priority DESC,w.id"
  :"SELECT id,user_id FROM waitlist WHERE slot_id=? AND status='WAITING' ORDER BY priority DESC,id","i",slot);
 if(!cands)return 0;
 int strict=cfg&&cfg->waitlist_strict;
 Id wid=0,uid=0;cJSON *it;
 if(weighted){
  /* 取回候选后按 score 降序（稳定：同分按原 priority DESC,id 序），再走同一套可执行性扫描 */
  int n=cJSON_GetArraySize(cands);
  if(n<=0){cJSON_Delete(cands);return 0;}
  WeightedCand *arr=(WeightedCand*)malloc(sizeof(WeightedCand)*(size_t)n);
  if(!arr){cJSON_Delete(cands);return 0;}
  int m=0;Id now=clock_now();
  cJSON_ArrayForEach(it,cands){
   Id w=0,u=0;
   parse_id(jstr(it,"id"),&w);parse_id(jstr(it,"user_id"),&u); /* 仅 _id 列是 jid 字符串；数字列必须走 valuedouble（r18 教训） */
   cJSON *jp=cJSON_GetObjectItemCaseSensitive(it,"priority"),*jc=cJSON_GetObjectItemCaseSensitive(it,"created_at"),*jr=cJSON_GetObjectItemCaseSensitive(it,"credit");
   Id pr=(jp&&cJSON_IsNumber(jp))?(Id)jp->valuedouble:0;
   Id ca=(jc&&cJSON_IsNumber(jc))?(Id)jc->valuedouble:0;
   Id cr=(jr&&cJSON_IsNumber(jr))?(Id)jr->valuedouble:5;
   if(ca<=0||now<ca)ca=now;
   double wait=(double)(now-ca)/3600.0;
   double lg=log2(1.0+wait);if(lg<0)lg=0;
   arr[m].wid=w;arr[m].uid=u;
   arr[m].score=1000.0*(double)pr+200.0*((double)cr-5.0)+60.0*lg;
   m++;
  }
  /* 插入排序（n 为候补队列规模，通常 < 百级；稳定：仅严格大于才交换） */
  for(int i=1;i<m;i++){WeightedCand t=arr[i];int j=i-1;while(j>=0&&arr[j].score<t.score){arr[j+1]=arr[j];j--;}arr[j+1]=t;}
  for(int i=0;i<m&&!wid;i++){
   Id u=arr[i].uid;
   if(!db_num(d,"SELECT count(*) FROM users WHERE id=? AND enabled=1","i",u)){if(d->error)break;continue;}
   Id clash=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id JOIN slots s ON s.id=? WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND s.start_at<s2.end_at AND s2.start_at<s.end_at","ii",slot,u);
   if(d->error)break;
   if(clash)continue; /* weighted 沿用可执行语义：冲突暂跳保留序号 */
   wid=arr[i].wid;uid=u;
  }
  free(arr);
 }
 else{
 cJSON_ArrayForEach(it,cands){
  Id w=0,u=0;parse_id(jstr(it,"id"),&w);parse_id(jstr(it,"user_id"),&u);
  if(!db_num(d,"SELECT count(*) FROM users WHERE id=? AND enabled=1","i",u)){if(d->error)break;continue;}
  Id clash=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id JOIN slots s ON s.id=? WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND s.start_at<s2.end_at AND s2.start_at<s.end_at","ii",slot,u);
  if(d->error)break;
  if(clash){if(strict)break;continue;}
  wid=w;uid=u;break;
 }
 }
 cJSON_Delete(cands);
 if(d->error||!wid)return 0;
 Id start=db_num(d,"SELECT start_at FROM slots WHERE id=?","i",slot);
 if(d->error)return 0;
 int hold=cfg&&cfg->hold_window>0;
 Id now=clock_now(),deadline=0;
 if(hold){deadline=now+(Id)cfg->hold_window;if(deadline>start)deadline=start;}
 if(!db_run(d,"INSERT INTO reservations(user_id,slot_id,status,source,created_at,hold_deadline) VALUES(?,?,?,'WAITLIST',?,?)","iisii",uid,slot,hold?"HELD":"CONFIRMED",now,hold?deadline:0))return 0;
 Id rid=sqlite3_last_insert_rowid(d->sql);
 db_run(d,"UPDATE waitlist SET status='PROMOTED',promoted_reservation_id=? WHERE id=?","ii",rid,wid);
 event(d,actor,"PROMOTE",rid,key);
 char when[40],body[240];slot_when(d,slot,when);
 if(hold)snprintf(body,sizeof body,"你候补的场次 %s 已为你保留名额（预约编号 %lld），请在保留截止前确认，超时将自动释放给下一位。",when,(long long)rid);
 else snprintf(body,sizeof body,"你候补的场次 %s 已补位成功，预约编号 %lld，请按时到场并在签到时间内签到。",when,(long long)rid);
 notify(d,uid,"PROMOTED",hold?"候补名额待确认":"候补补位成功",body,slot,rid);
 return rid;
}
/* 按容量反复补位直至满员或队列空；返回首个新增预约编号（无则 0）。 */
Id promote_fill(DB *d,const Config *cfg,Id slot,Id actor,const char *key){
 Id first=0;
 for(;;){
  Id capacity=db_num(d,"SELECT capacity FROM slots WHERE id=?","i",slot);
  if(d->error)return first;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",slot)>=capacity)break;
  Id rid=promote(d,cfg,slot,actor,key);
  if(d->error)return first;
  if(!rid)break;
  if(!first)first=rid;
 }
 return first;
}
static Result slot_full(DB *d,Id slot,const char *message){
 cJSON *list=db_rows(d,"SELECT s.id,s.start_at,s.end_at FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.lab_id=(SELECT lab_id FROM slots WHERE id=?) AND s.start_at>(SELECT start_at FROM slots WHERE id=?) AND s.start_at<=(SELECT start_at FROM slots WHERE id=?)+604800 AND s.enabled=1 AND l.enabled=1 AND (SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND r.status IN('CONFIRMED','HELD'))<s.capacity ORDER BY s.start_at LIMIT 3","iii",slot,slot,slot);
 if(d->error)return db_failure(d);
 cJSON *data=cJSON_CreateObject();if(!data){cJSON_Delete(list);d->error=SQLITE_NOMEM;return db_failure(d);}
 cJSON_AddItemToObject(data,"alternatives",list);return result(409,"SLOT_FULL",message,data);
}
Result booking(DB *d,const Config *cfg,const User *u,const char *action,Id target,const cJSON *body,const char *key){
 Result r={500,NULL};cJSON *old=NULL,*row=NULL,*slot=NULL;char canonical[224],digest[65];Id sid=target;
 snprintf(canonical,sizeof canonical,"%s:%lld",action,(long long)target);
 /* r14：预约可声明所需资源，资源列表纳入请求摘要（同编号异参数仍为冲突） */
 cJSON *claim_arr=NULL;const char *bnote=NULL;
 if(!strcmp(action,"reserve")&&body){
  bnote=jstr(body,"note");
  if(bnote&&strlen(bnote)>200)return result(400,"INVALID_INPUT","备注最多 200 字符",NULL);
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
 slot=db_first(d,"SELECT s.id,s.start_at,s.enabled,s.capacity,l.enabled AS lab_enabled,l.require_approval FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.id=?","i",sid);
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
 /* r17 BR14 预约提前量：开始前不足 lead_time 秒的场次停止受理预约与候补 */
 if(cfg&&cfg->lead_time>0&&start_at-now_sec()<(Id)cfg->lead_time){
  char msg[96];snprintf(msg,sizeof msg,"距场次开始不足 %d 分钟，停止受理预约",(int)(cfg->lead_time/60));
  r=result(409,"LEAD_TIME",msg,NULL);goto save;
 }
 if(!strcmp(action,"reserve")){
  Id capacity=(Id)cJSON_GetObjectItemCaseSensitive(slot,"capacity")->valuedouble;
  Id taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",sid);
  if(d->error)goto failed;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=? AND status IN('CONFIRMED','HELD')","ii",sid,u->id)){r=result(409,"ALREADY_RESERVED","你已预约该场次",NULL);goto save;}
  if(d->error)goto failed;
  /* r23 信用账户：先做每周回补，再校验余额——余额为 0 时禁止新预约（取代原先独立的每周配额计数） */
  credit_weekly_topup(d,u->id);if(d->error)goto failed;
  if(!db_num(d,"SELECT credit FROM users WHERE id=?","i",u->id)){r=result(409,"CREDIT_EXHAUSTED","信用额度已用尽，按时签到或等待每周回补后可继续预约",NULL);goto save;}
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
   Id overlap=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id JOIN slots s ON s.id=? WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND s.start_at<s2.end_at AND s2.start_at<s.end_at","ii",sid,u->id);
   if(d->error)goto failed;
   if(overlap){r=result(409,"TIME_CONFLICT","与您已预约的场次时间重叠，请先取消原场次",NULL);goto save;}
  }
  /* r16 BR13 每周配额：本周（北京周一 0 点起）有效预约数达上限则拒绝（候补不入配额，补位为 FIFO 公平结果） */
  if(cfg&&cfg->quota_weekly>0){
   Id day0=(now_sec()+28800)/86400*86400-28800;
   int wd=(int)(((day0+28800)/86400+3)%7); /* 0=周一 */
   Id week_start=day0-(Id)wd*86400;
   Id used=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND s2.start_at>=?","ii",u->id,week_start);
   if(d->error)goto failed;
   if(used>=(Id)cfg->quota_weekly){
    char msg[96];snprintf(msg,sizeof msg,"本周预约已达上限 %d 场，下周一 0 点后重试",(int)cfg->quota_weekly);
    r=result(409,"WEEKLY_QUOTA",msg,NULL);goto save;
   }
  }
  if(taken>=capacity){
   /* r23 抢占：请求者优先级严格高于场次内某个"未签到"的确认预约时可抢占该名额；
      被抢占者记 cancel_reason='PREEMPTED'（不计爽约）、获 1 点信用补偿并收到通知。 */
   Id victim=0;
   if(role_priority(u)>0){
    victim=db_num(d,"SELECT id FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD') AND checked_in_at IS NULL AND priority<? ORDER BY priority ASC,id ASC LIMIT 1","ii",sid,role_priority(u));
    if(d->error)goto failed;
   }
   if(victim){
    Id vuser=db_num(d,"SELECT user_id FROM reservations WHERE id=?","i",victim);
    if(d->error)goto failed;
    if(!db_run(d,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='PREEMPTED' WHERE id=?","ii",now_sec(),victim))goto failed;
    credit_apply(d,vuser,1,"PREEMPTED",victim);
    char when[40];slot_when(d,sid,when);
    char pmsg[192];snprintf(pmsg,sizeof pmsg,"%s 的预约因更高优先级需求被占用，已补偿 1 点信用，请另选时段。",when[0]?when:"您");
    notify(d,vuser,"NOTICE","预约被优先占用",pmsg,sid,victim);
    event(d,u->id,"PREEMPT",victim,key);
    taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",sid);
    if(d->error)goto failed;
   }
  }
  if(taken>=capacity){r=slot_full(d,sid,"该场次已约满，可加入候补或选择替代时段");goto save;}
  promote_fill(d,cfg,sid,u->id,key);if(d->error)goto failed;
  taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",sid);
  if(d->error)goto failed;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=? AND status IN('CONFIRMED','HELD')","ii",sid,u->id)){r=slot_full(d,sid,"你已通过候补补位获得该场次，可在我的记录查看");goto save;}
  if(taken>=capacity){r=slot_full(d,sid,"名额已按顺序分配给候补用户，可选择替代时段");goto save;}
  /* r14 资源声明：先在事务内校验归属/可用性与时段配额（BR12），通过后再创建预约并落 claims */
  if(claim_arr){
   Id slot_end=start_at+3600;
   cJSON *it;int n=0;
   cJSON_ArrayForEach(it,claim_arr){
    if(n>=5)break;
    n++;
    Id aid=0;const char *sv=cJSON_IsString(it)?cJSON_GetStringValue(it):NULL;
    if(!sv||!parse_id(sv,&aid)||aid<1){r=result(400,"INVALID_INPUT","资源编号不合法",NULL);goto save;}
    if(!db_num(d,"SELECT count(*) FROM assets WHERE id=? AND lab_id=(SELECT lab_id FROM slots WHERE id=?) AND status='AVAILABLE'","ii",aid,sid)){
     r=result(409,"STATE_CONFLICT","所声明的资源不存在、已停用或不属于该实验室",NULL);goto save;}
    /* r24 资格授权：仅对显式开启 requires_qualification 的资源校验（默认关闭，向后兼容） */
    if(db_num(d,"SELECT requires_qualification FROM assets WHERE id=?","i",aid)){
     if(d->error)goto failed;
     if(!db_num(d,"SELECT count(*) FROM qualifications WHERE user_id=? AND asset_id=? AND (expires_at IS NULL OR expires_at=0 OR expires_at>?)","iii",u->id,aid,clock_now())){
      if(d->error)goto failed;
      r=result(409,"QUALIFICATION_REQUIRED","你尚未获得该资源的使用资格，请联系管理员授权",NULL);goto save;}
    }
    /* r23 资源自身可用时段：与场次时段正交，未配置时段的资源视为全天可用 */
    if(!asset_window_ok(d,aid,start_at)){
     if(d->error)goto failed;
     r=result(409,"WINDOW_CONFLICT","所声明的资源在该场次时段不可用（不在其开放时段内）",NULL);goto save;}
    Id total=db_num(d,"SELECT total FROM assets WHERE id=?","i",aid);
    Id used=db_num(d,"SELECT count(*) FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id JOIN slots s2 ON s2.id=r.slot_id WHERE c.asset_id=? AND r.status IN('CONFIRMED','HELD') AND s2.start_at<? AND s2.end_at>?",
                      "iii",aid,slot_end,start_at);
    if(d->error)goto failed;
    if(used>=total){r=result(409,"ASSET_QUOTA","该时段所声明的资源已被约满，可减少资源或改约其他时段",NULL);goto save;}
   }
  }
  /* r19 审批模式：所在实验室开启 require_approval 时预约先落 PENDING，由管理员批准后生效 */
  cJSON *ap=cJSON_GetObjectItemCaseSensitive(slot,"require_approval");
  int need_appr=ap&&((ap->type==cJSON_Number)?(int)ap->valuedouble==1:cJSON_IsTrue(ap));
  if(!db_run(d,"INSERT INTO reservations(user_id,slot_id,status,source,created_at,note,priority) VALUES(?,?,'CONFIRMED','DIRECT',?,?,?)","iiisi",u->id,sid,now_sec(),(bnote&&bnote[0])?bnote:NULL,role_priority(u)))goto failed;
  Id rid=sqlite3_last_insert_rowid(d->sql);
  if(need_appr){
   if(!db_run(d,"UPDATE reservations SET status='PENDING' WHERE id=?","i",rid))goto failed;
   notify(d,1,"NOTICE","新的待审批预约","有待审批预约，请在管理控制台处理。",sid,rid);
  }
  if(claim_arr){
   cJSON *it2;cJSON_ArrayForEach(it2,claim_arr){
    const char *sv2=cJSON_IsString(it2)?cJSON_GetStringValue(it2):NULL;Id aid2=0;
    if(!sv2||!parse_id(sv2,&aid2))continue;
    if(!db_run(d,"INSERT OR IGNORE INTO asset_claims(reservation_id,asset_id,created_at) VALUES(?,?,?)","iii",rid,aid2,now_sec()))goto failed;
   }
  }
  event(d,u->id,"RESERVE",rid,key);
  { /* r21 审批回执：带落库 status，页面据此区分已确认与待审批（前端门禁 check_approval_flag_is_surfaced） */
   cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",rid);
   cJSON_AddStringToObject(j,"status",need_appr?"PENDING":"CONFIRMED");
   r=result(200,"OK","操作成功",j);
  }
 }else if(!strcmp(action,"wait")){
  Id capacity=(Id)cJSON_GetObjectItemCaseSensitive(slot,"capacity")->valuedouble;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=? AND status IN('CONFIRMED','HELD')","ii",sid,u->id)){r=result(409,"ALREADY_RESERVED","你已预约该场次",NULL);goto save;}
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",sid)<capacity){r=result(409,"SLOT_AVAILABLE","场次尚有余位，请直接预约",NULL);goto save;}
  if(d->error)goto failed;
  /* 候补同样受爽约信用与时间重叠约束（补位成功即占用该时段） */
  {
   Id window_start=now_sec()-(Id)PENALTY_DAYS*86400;
   Id strikes=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=? AND r.cancel_reason='NO_SHOW' AND s2.start_at>=?","ii",u->id,window_start);
   if(d->error)goto failed;
   if(strikes>=NO_SHOW_GRACE){r=result(409,"PENALTY_ACTIVE","爽约次数过多，预约受限，如有疑问请联系管理员",NULL);goto save;}
   Id overlap=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id JOIN slots s ON s.id=? WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND s.start_at<s2.end_at AND s2.start_at<s.end_at","ii",sid,u->id);
   if(d->error)goto failed;
   if(overlap){r=result(409,"TIME_CONFLICT","与您已预约的场次时间重叠，不能加入候补",NULL);goto save;}
  }
  credit_weekly_topup(d,u->id);if(d->error)goto failed;
  if(!db_num(d,"SELECT credit FROM users WHERE id=?","i",u->id)){r=result(409,"CREDIT_EXHAUSTED","信用额度已用尽，按时签到或等待每周回补后可继续候补",NULL);goto save;}
  if(d->error)goto failed;
  if(cfg&&cfg->waitlist_daily_limit>0){ /* r26 防刷：每用户每日候补入队上限（北京日界） */
   Id d0=(now_sec()+28800)/86400*86400-28800;
   if(db_num(d,"SELECT count(*) FROM waitlist WHERE user_id=? AND created_at>=?","ii",u->id,d0)>=cfg->waitlist_daily_limit){r=result(409,"WAITLIST_LIMIT","今日候补次数已达上限，请明日再试",NULL);goto save;}
   if(d->error)goto failed;
  }
  Id wid=db_num(d,"SELECT id FROM waitlist WHERE user_id=? AND slot_id=? AND status='WAITING'","ii",u->id,sid);
  if(!wid){db_run(d,"INSERT INTO waitlist(user_id,slot_id,status,created_at,priority) VALUES(?,?,'WAITING',?,?)","iiii",u->id,sid,now_sec(),role_priority(u));wid=sqlite3_last_insert_rowid(d->sql);event(d,u->id,"WAIT",wid,key);}
  { /* r26 知情候补：附排位与历史转正概率 */
   Id rank=queue_rank(d,sid,u->id);if(d->error)goto failed;
   double prob=promote_probability(d,sid,rank>0?rank:1);if(d->error)goto failed;
   cJSON *j=cJSON_CreateObject();jid(j,"waitlist_id",wid);cJSON_AddNumberToObject(j,"queue_ahead",(double)(rank>0?rank-1:0));
   if(prob<0)cJSON_AddNullToObject(j,"promote_probability");else cJSON_AddNumberToObject(j,"promote_probability",prob);
   r=result(200,"OK","已加入候补",j);
  }
 }else if(!strcmp(action,"cancel")){
  Id promoted=0;
  /* r24：HELD（待确认保留）与 CONFIRMED 一样可被本人取消 */
  if(!strcmp(jstr(row,"status"),"CONFIRMED")||!strcmp(jstr(row,"status"),"HELD")){
   if(!db_run(d,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='USER',hold_deadline=NULL WHERE id=?","ii",clock_now(),target))goto failed;
   fault(cfg,"cancel-before-promote",key);event(d,u->id,"CANCEL",target,key);promoted=promote_fill(d,cfg,sid,u->id,key);
  }else if(!strcmp(jstr(row,"status"),"PENDING")){ /* r19 待审批取消：不占容量、无补位 */
   if(!db_run(d,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='USER' WHERE id=?","ii",now_sec(),target))goto failed;
   event(d,u->id,"CANCEL",target,key);
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
  if(!done){if(!db_run(d,"UPDATE reservations SET checked_in_at=? WHERE id=?","ii",when,target))goto failed;credit_apply(d,u->id,1,"CHECKIN",target);event(d,u->id,"CHECKIN",target,key);}
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
 char sql[1900],sfilter[48]={0};
 /* status 由调用方按白名单枚举校验后传入，直接拼接无注入面；
    原实现写成字面量 '?' 使条件恒不成立（r22 修正）。 */
 if(status)snprintf(sfilter,sizeof sfilter," AND r.status='%s'",status);
 Id limit=(Id)size+1,offset=(Id)(page-1)*size;int more=0;
 snprintf(sql,sizeof sql,"SELECT r.id,r.slot_id,l.id AS lab_id,l.name AS lab_name,u.username,s.start_at,s.end_at,r.status,r.source,r.cancel_reason,r.checked_in_at,r.hold_deadline,r.note FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=r.user_id WHERE %s%s ORDER BY r.id DESC LIMIT ? OFFSET ?",
  where,sfilter);
 cJSON *a=all?db_rows(d,sql,"iiiii",date,date,date+86400,limit,offset):db_rows(d,sql,"iii",u->id,limit,offset);
 if(a&&cJSON_GetArraySize(a)>size){cJSON_DeleteItemFromArray(a,(int)size);more=1;}
 snprintf(sql,sizeof sql,"SELECT r.id,r.slot_id,l.name AS lab_name,u.username,s.start_at,s.end_at,r.status,CASE WHEN r.status='WAITING' THEN (SELECT count(*) FROM waitlist w JOIN users wu ON wu.id=w.user_id WHERE w.slot_id=r.slot_id AND w.status='WAITING' AND (w.priority>r.priority OR (w.priority=r.priority AND w.id<=r.id)) AND wu.enabled=1) ELSE 0 END AS position FROM waitlist r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=r.user_id WHERE %s ORDER BY r.id DESC LIMIT ? OFFSET ?",where);
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
/* r18 改期：同实验室、CONFIRMED、原/新场次均未开始；事务内重校新槽容量/重叠(排除自身)/提前量/资源声明配额，
   原子更新后旧槽触发 FIFO 补位；资源声明保留（按新时段计入配额）。对标 Cal.com 预约生命周期三件套。 */
Result reservation_reschedule(DB *d,const Config *cfg,const User *u,Id target,Id new_slot,const char *key){
 Result r={500,NULL};cJSON *row=NULL;
 char canonical[128],digest[65];snprintf(canonical,sizeof canonical,"reschedule:%lld:%lld",(long long)target,(long long)new_slot);hash_text(canonical,digest);
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 row=db_first(d,"SELECT r.user_id,r.status,r.slot_id,s.start_at,s.lab_id FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE r.id=?","i",target);
 if(d->error)goto failed;
 if(!row){r=result(404,"NOT_FOUND","记录不存在",NULL);goto save;}
 {Id owner=0;parse_id(jstr(row,"user_id"),&owner);
  if(owner!=u->id){r=result(403,"FORBIDDEN","只能操作自己的记录",NULL);goto save;}}
 if(strcmp(jstr(row,"status"),"CONFIRMED")){r=result(409,"STATE_CONFLICT","该预约当前状态不能改期",NULL);goto save;}
 {Id old_slot=0;parse_id(jstr(row,"slot_id"),&old_slot);
  Id old_start=(Id)cJSON_GetObjectItemCaseSensitive(row,"start_at")->valuedouble;
  Id lab=0;parse_id(jstr(row,"lab_id"),&lab);
  if(old_start<=now_sec()){r=result(409,"STATE_CONFLICT","场次已开始，不能再改期",NULL);goto save;}
  if(new_slot==old_slot){r=result(409,"STATE_CONFLICT","新场次与原场次相同",NULL);goto save;}
  cJSON *ns=db_first(d,"SELECT s.id,s.start_at,s.enabled,s.capacity,s.lab_id,l.enabled AS lab_enabled FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.id=?","i",new_slot);
  if(d->error)goto failed;
  if(!ns){cJSON_Delete(ns);r=result(404,"NOT_FOUND","目标场次不存在",NULL);goto save;}
  if(!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(ns,"enabled"))||!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(ns,"lab_enabled"))){
   cJSON_Delete(ns);r=result(409,"STATE_CONFLICT","目标场次未开放",NULL);goto save;}
  Id ns_start=(Id)cJSON_GetObjectItemCaseSensitive(ns,"start_at")->valuedouble;
  Id ns_lab=0;parse_id(jstr(ns,"lab_id"),&ns_lab);
  if(ns_lab!=lab){cJSON_Delete(ns);r=result(409,"STATE_CONFLICT","改期仅限同一实验室（资源声明随场次生效）",NULL);goto save;}
  if(ns_start<=now_sec()){cJSON_Delete(ns);r=result(409,"STATE_CONFLICT","目标场次已开始",NULL);goto save;}
  if(cfg&&cfg->lead_time>0&&ns_start-now_sec()<(Id)cfg->lead_time){
   cJSON_Delete(ns);r=result(409,"LEAD_TIME","距目标场次开始不足提前量",NULL);goto save;}
  Id ns_cap=(Id)cJSON_GetObjectItemCaseSensitive(ns,"capacity")->valuedouble;
  Id ns_taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD') AND id<>?","ii",new_slot,target);
  if(d->error){cJSON_Delete(ns);goto failed;}
  if(ns_taken>=ns_cap){cJSON_Delete(ns);r=slot_full(d,new_slot,"目标场次已约满，可先取消原预约再排队");goto save;}
  {Id overlap=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id JOIN slots s ON s.id=? WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND r.id<>? AND s.start_at<s2.end_at AND s2.start_at<s.end_at","iii",new_slot,u->id,target);
   if(d->error){cJSON_Delete(ns);goto failed;}
   if(overlap){cJSON_Delete(ns);r=result(409,"TIME_CONFLICT","与您已预约的场次时间重叠",NULL);goto save;}}
  /* 资源声明按新时段重校配额（声明保留，同实验室已保证资源归属成立） */
  {cJSON *cl=db_rows(d,"SELECT c.asset_id,a.total FROM asset_claims c JOIN assets a ON a.id=c.asset_id WHERE c.reservation_id=?","i",target);
   if(!cl){cJSON_Delete(ns);goto failed;}
   cJSON *cit;cJSON_ArrayForEach(cit,cl){
    Id ca=0,total=0;parse_id(jstr(cit,"asset_id"),&ca);total=(Id)cJSON_GetObjectItemCaseSensitive(cit,"total")->valuedouble;
    Id used=db_num(d,"SELECT count(*) FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id JOIN slots s2 ON s2.id=r.slot_id WHERE c.asset_id=? AND r.status IN('CONFIRMED','HELD') AND r.id<>? AND s2.start_at<? AND s2.end_at>?","iii",ca,target,ns_start+3600,ns_start);
    if(d->error){cJSON_Delete(cl);cJSON_Delete(ns);goto failed;}
    if(used>=total){cJSON_Delete(cl);cJSON_Delete(ns);r=result(409,"ASSET_QUOTA","目标时段所声明的资源已被约满",NULL);goto save;}}
   cJSON_Delete(cl);}
  cJSON_Delete(ns);
  if(!db_run(d,"UPDATE reservations SET slot_id=? WHERE id=?","ii",new_slot,target))goto failed;
  event(d,u->id,"RESCHEDULE",target,key);
  Id promoted=promote_fill(d,cfg,old_slot,u->id,key); /* 旧槽释放 → FIFO 补位 */
  cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);jid(j,"new_slot_id",new_slot);
  jid(j,"old_slot_id",old_slot);if(promoted)jid(j,"promoted_reservation_id",promoted);else cJSON_AddNullToObject(j,"promoted_reservation_id");
  r=result(200,"OK","改期成功",j);goto save;
 }
save:
 if(d->error)goto failed;
 if(!r.body){d->error=SQLITE_NOMEM;goto failed;}
 {char *serialized=cJSON_PrintUnformatted(r.body);if(!serialized){d->error=SQLITE_NOMEM;goto failed;}
  db_run(d,"INSERT INTO request_receipts(user_id,request_id,action,payload_digest,http_status,result_json,created_at) VALUES(?,?,?,?,?,?,?)","isssisi",u->id,key,"reschedule",digest,0,serialized,now_sec());cJSON_free(serialized);}
 if(d->error)goto failed;
 if(!db_run(d,"COMMIT",""))goto failed;
 cJSON_Delete(row);
 return r;
failed:
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);cJSON_Delete(row);cJSON_Delete(r.body);return db_failure(d);
}
/* r19 审批：PENDING → CONFIRMED（批准，重校容量与资源配额，通知用户）/ → CANCELLED+REJECTED（拒绝，通知用户）。 */
Result reservation_approval(DB *d,const User *u,Id target,int approve,const char *key){
 Result r;
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 r=approval_core(d,u,target,approve,key);
 if(d->error)goto afailed;
 if(!r.body){d->error=SQLITE_NOMEM;goto afailed;}
 {char *serialized=cJSON_PrintUnformatted(r.body);if(!serialized){d->error=SQLITE_NOMEM;goto afailed;}
  db_run(d,"INSERT INTO request_receipts(user_id,request_id,action,payload_digest,http_status,result_json,created_at) VALUES(?,?,?,?,?,?,?)","isssisi",u->id,key,approve?"approve":"reject","digest",0,serialized,now_sec());cJSON_free(serialized);}
 if(d->error)goto afailed;
 if(!db_run(d,"COMMIT",""))goto afailed;
 return r;
afailed:
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);cJSON_Delete(r.body);return db_failure(d);
}
/* r32 审批核心（假定调用方已开启 BEGIN IMMEDIATE 事务）：单条与批量共用同一套校验与通知。
   返回 Result；404/409 类业务失败不置 d->error（批量场景继续下一条），仅真实 DB 错误置错。 */
static Result approval_core(DB *d,const User *u,Id target,int approve,const char *key){
 cJSON *row=db_first(d,"SELECT r.user_id,r.status,r.slot_id,s.capacity,l.name AS lab FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id WHERE r.id=?","i",target);
 if(d->error)goto acore_fail;
 if(!row)return result(404,"NOT_FOUND","记录不存在",NULL);
 if(strcmp(jstr(row,"status"),"PENDING")){cJSON_Delete(row);return result(409,"STATE_CONFLICT","该预约不在待审批状态",NULL);}
 if(approve){
  Id cap=(Id)cJSON_GetObjectItemCaseSensitive(row,"capacity")->valuedouble;
  Id sid=0;parse_id(jstr(row,"slot_id"),&sid);
  Id taken=db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",sid);
  if(d->error)goto acore_fail2;
  if(taken>=cap){cJSON_Delete(row);return result(409,"APPROVAL_CAPACITY","批准时容量已满，请拒绝该预约或调整容量",NULL);}
  {cJSON *cl=db_rows(d,"SELECT c.asset_id,a.total FROM asset_claims c JOIN assets a ON a.id=c.asset_id WHERE c.reservation_id=?","i",target);
   if(!cl)goto acore_fail2;
   cJSON *cit;cJSON_ArrayForEach(cit,cl){
    Id ca=0,total=0;parse_id(jstr(cit,"asset_id"),&ca);total=(Id)cJSON_GetObjectItemCaseSensitive(cit,"total")->valuedouble;
    Id used=db_num(d,"SELECT count(*) FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id JOIN slots s2 ON s2.id=r.slot_id WHERE c.asset_id=? AND r.status IN('CONFIRMED','HELD') AND r.id<>? AND s2.start_at<(SELECT end_at FROM slots WHERE id=?) AND s2.end_at>(SELECT start_at FROM slots WHERE id=?)","iiii",ca,target,sid,sid);
    if(d->error){cJSON_Delete(cl);goto acore_fail2;}
    if(used>=total){cJSON_Delete(cl);cJSON_Delete(row);return result(409,"ASSET_QUOTA","批准时资源声明已超配额，请拒绝或协调改期",NULL);}}
   cJSON_Delete(cl);}
  if(!db_run(d,"UPDATE reservations SET status='CONFIRMED' WHERE id=?","i",target))goto acore_fail2;
  {Id uid2=0;parse_id(jstr(row,"user_id"),&uid2);
   event(d,u->id,"APPROVE",target,key);
   notify(d,uid2,"NOTICE","预约已批准","你的预约已获批准，请按时到场签到。",sid,target);}
  cJSON_Delete(row);
  cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);return result(200,"OK","已批准",j);
 }
 if(!db_run(d,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='REJECTED' WHERE id=?","ii",now_sec(),target))goto acore_fail2;
 event(d,u->id,"REJECT",target,key);
 {Id uid2=0,slot_id2=0;parse_id(jstr(row,"user_id"),&uid2);parse_id(jstr(row,"slot_id"),&slot_id2);
  notify(d,uid2,"NOTICE","预约被拒绝","很抱歉，你的预约未获批准，可改约其他场次。",slot_id2,target);}
 cJSON_Delete(row);
 {cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);return result(200,"OK","已拒绝",j);}
acore_fail2:
 cJSON_Delete(row);
acore_fail:
 d->error=d->error?d->error:SQLITE_ERROR;
 return result(500,"INTERNAL_ERROR","服务处理失败，请稍后重试",NULL);
}
/* r32 批量审批：一个事务内逐条执行审批核心；单条业务失败（404/409）记入 failed[] 继续下一条（部分成功语义，
   适用于清理积压待审批列表），任一 DB 错误整体回滚。 */
Result reservation_approval_batch(DB *d,const User *u,const cJSON *body,const char *key){
 cJSON *ids=cJSON_GetObjectItemCaseSensitive(body,"ids");
 const char *action=jstr(body,"action");
 if(!ids||!cJSON_IsArray(ids)||cJSON_GetArraySize(ids)<1||cJSON_GetArraySize(ids)>50)
  return result(400,"INVALID_INPUT","ids 需为 1..50 个预约编号的数组",NULL);
 if(!action||(strcmp(action,"approve")&&strcmp(action,"reject")))
  return result(400,"INVALID_INPUT","action 需为 approve 或 reject",NULL);
 int approve=!strcmp(action,"approve");
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 cJSON *failed=cJSON_CreateArray();int succeeded=0,processed=0,db_bad=0;
 cJSON *it;cJSON_ArrayForEach(it,ids){
  if(!cJSON_IsNumber(it))continue;
  Id rid=(Id)it->valuedouble;if(rid<1)continue;
  processed++;
  Result r=approval_core(d,u,rid,approve,key);
  if(d->error){db_bad=1;break;} /* 真实 DB 错误：整体回滚 */
  if(r.status==200)succeeded++;
  else{
   cJSON *f=cJSON_CreateObject();jid(f,"reservation_id",rid);
   const char *code="ERROR";cJSON *jc=cJSON_GetObjectItemCaseSensitive(r.body,"code");
   if(jc&&cJSON_IsString(jc))code=jc->valuestring;
   cJSON_AddStringToObject(f,"code",code);cJSON_AddItemToArray(failed,f);
  }
  cJSON_Delete(r.body);
 }
 if(db_bad||d->error){cJSON_Delete(failed);sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 db_run(d,"INSERT INTO request_receipts(user_id,request_id,action,payload_digest,http_status,result_json,created_at) VALUES(?,?,?,?,?,?,?)","isssisi",u->id,key,"approve-batch","digest",200,"{}",now_sec());
 if(d->error||!db_run(d,"COMMIT","")){cJSON_Delete(failed);sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
 cJSON *j=cJSON_CreateObject();
 cJSON_AddNumberToObject(j,"processed",(double)processed);
 cJSON_AddNumberToObject(j,"succeeded",(double)succeeded);
 cJSON_AddItemToObject(j,"failed",failed);
 return result(200,"OK","批量审批完成（部分成功语义，失败项见 failed 列表）",j);
}
Result reservation_batch(DB *d,const Config *cfg,const User *u,const cJSON *body,const char *key){
 (void)cfg;
 cJSON *ids=cJSON_GetObjectItemCaseSensitive(body,"slot_ids");
 if(!ids||!cJSON_IsArray(ids))return result(400,"INVALID_INPUT","slot_ids 需为非空数组",NULL);
 int n=cJSON_GetArraySize(ids);
 if(n<1||n>8)return result(400,"INVALID_INPUT","一次可预约 1..8 个连续场次",NULL);
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 Result r={500,NULL};Id first=0,last_end=0,lab=0;int i=0;cJSON *it;
 credit_weekly_topup(d,u->id);
 if(d->error)goto failed;
 if(db_num(d,"SELECT credit FROM users WHERE id=?","i",u->id)<(Id)n){r=result(409,"CREDIT_EXHAUSTED","信用额度不足以完成本次连续预约",NULL);goto save;}
 if(d->error)goto failed;
 cJSON_ArrayForEach(it,ids){
  Id sid=0;
  if(!cJSON_IsString(it)||!parse_id(it->valuestring,&sid)){r=result(400,"INVALID_INPUT","场次编号不合法",NULL);goto save;}
  cJSON *s=db_first(d,"SELECT s.lab_id,s.start_at,s.end_at,s.enabled,s.capacity,l.enabled AS lab_enabled FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.id=?","i",sid);
  if(d->error)goto failed;
  if(!s){r=result(404,"NOT_FOUND","所选场次不存在",NULL);goto save;}
  Id cap=(Id)cJSON_GetObjectItemCaseSensitive(s,"capacity")->valuedouble;
  Id t0=(Id)cJSON_GetObjectItemCaseSensitive(s,"start_at")->valuedouble;
  Id t1=(Id)cJSON_GetObjectItemCaseSensitive(s,"end_at")->valuedouble;
  Id sl=0;parse_id(jstr(s,"lab_id"),&sl); /* _id 后缀列是 JSON 字符串，不能用 valuedouble */
  int en=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(s,"enabled"));      /* 布尔列用 cJSON_IsTrue */
  int le=cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(s,"lab_enabled"));
  cJSON_Delete(s);
  if(!en||!le){r=result(409,"STATE_CONFLICT","所选场次或实验室已停用",NULL);goto save;}
  if(i==0){lab=sl;first=t0;}
  else if(sl!=lab||t0!=last_end){r=result(409,"TIME_CONFLICT","所选场次须属于同一实验室且时间连续",NULL);goto save;}
  last_end=t1;i++;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND status IN('CONFIRMED','HELD')","i",sid)>=cap){r=result(409,"SLOT_FULL","所选场次中有已约满的时段",NULL);goto save;}
  if(d->error)goto failed;
  if(db_num(d,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=? AND status IN('CONFIRMED','HELD')","ii",sid,u->id)){r=result(409,"ALREADY_RESERVED","你已预约所选场次中的时段",NULL);goto save;}
  if(d->error)goto failed;
  if(!db_run(d,"INSERT INTO reservations(user_id,slot_id,status,source,created_at,priority) VALUES(?,?,'CONFIRMED','DIRECT',?,?)","iiii",u->id,sid,now_sec(),role_priority(u)))goto failed;
 }
 /* 与既有确认预约的时间重叠（连续时段以首尾代表） */
 if(db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND NOT (s2.start_at>=? AND s2.end_at<=?) AND s2.start_at<? AND s2.end_at>?","iiiii",u->id,first,last_end,last_end,first)){
  r=result(409,"TIME_CONFLICT","与您已预约的其他场次时间重叠",NULL);goto save;
 }
 if(d->error)goto failed;
 event(d,u->id,"RESERVE_BATCH",first,key);
 {cJSON *j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"created",n);r=result(200,"OK","连续预约成功",j);}
save:
 if(r.status==200){
  if(!db_run(d,"COMMIT","")){cJSON_Delete(r.body);return db_failure(d);}
  return r;
 }
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);
 return r;
failed:
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);
 cJSON_Delete(r.body);
 return db_failure(d);
}
/* r24 HELD 限时保留的确认：仅本人、仅 HELD 态、且当前时刻严格早于 hold_deadline 与时段开始时刻。
   截止判断在写事务内用统一时间源进行——恰好等于截止时刻视为超时（与设计文档 5.2 节一致）。 */
Result reservation_confirm(DB *d,const User *u,Id target,const char *key){
 (void)key;
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 cJSON *row=db_first(d,"SELECT user_id,slot_id,status,hold_deadline FROM reservations WHERE id=?","i",target);
 Result r={500,NULL};
 if(d->error)goto failed;
 if(!row){r=result(404,"NOT_FOUND","记录不存在",NULL);goto out;}
 if(strcmp(jstr(row,"status"),"HELD")){r=result(409,"STATE_CONFLICT","该预约不在待确认状态",NULL);goto out;}
 {Id owner=0;parse_id(jstr(row,"user_id"),&owner);
  if(owner!=u->id){r=result(403,"FORBIDDEN","只能确认本人的预约",NULL);goto out;}}
 {Id sid=0;parse_id(jstr(row,"slot_id"),&sid);
  Id start=db_num(d,"SELECT start_at FROM slots WHERE id=?","i",sid);
  if(d->error)goto failed;
  cJSON *hd=cJSON_GetObjectItemCaseSensitive(row,"hold_deadline");
  Id dl=(hd&&cJSON_IsNumber(hd))?(Id)hd->valuedouble:0;
  Id now=clock_now();
  if((dl&&now>=dl)||now>=start){r=result(409,"HOLD_EXPIRED","保留时限已过，名额已释放给下一位",NULL);goto out;}}
 if(!db_run(d,"UPDATE reservations SET status='CONFIRMED',hold_deadline=NULL WHERE id=? AND status='HELD'","i",target))goto failed;
 event(d,u->id,"CONFIRM",target,NULL);
 {cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);r=result(200,"OK","已确认预约",j);}
out:
 cJSON_Delete(row);
 if(r.status==200){if(!db_run(d,"COMMIT","")){cJSON_Delete(r.body);return db_failure(d);}return r;}
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);
 return r;
failed:
 cJSON_Delete(row);
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);
 cJSON_Delete(r.body);
 return db_failure(d);
}
/* -- r25 创新方向 1（最小版）：约束感知替代建议——只读地逐场次评估当前用户可预约/可候补与否，并给出原因解释。
   评估与真实预约之间存在竞态，属建议性质；提交时仍由 booking() 事务内全量校验兜底。 -- */
/* r26 转正概率：同 (实验室, 星期几) 过去 35 天（5 周）已开场场次中"释放名额数 ≥ rank"的场次占比（经验分布）。
   释放 = CANCELLED(USER/NO_SHOW/REJECTED) + EXPIRED；样本 <5 场返回 -1（不下结论）。rank 从 1 计。 */
static double promote_probability(DB *d,Id slot,Id rank){
 if(rank<1)return -1.0;
 Id start_at=db_num(d,"SELECT start_at FROM slots WHERE id=?","i",slot);
 if(d->error||!start_at)return -1.0;
 int wd=(int)(((start_at+28800)/86400+4)%7); /* 北京时区星期：1970-01-01 是周四 → +4 mod 7 得 0=周日…与 strftime('%w') 对齐 */
 Id now=now_sec();
 cJSON *rows=db_rows(d,
  "SELECT s.id AS sid,"
  "(SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND ((r.status='CANCELLED' AND r.cancel_reason IN('USER','NO_SHOW','REJECTED')) OR r.status='EXPIRED')) AS released "
  "FROM slots s WHERE s.lab_id=(SELECT lab_id FROM slots WHERE id=?) AND s.start_at<? AND s.start_at>? "
  "AND ((s.start_at+28800)/86400+4)%7=? GROUP BY s.id",
  "iiii",slot,now-3600,now-35*86400,(Id)wd);
 if(d->error)return -1.0;
 int total=0,hit=0;
 for(cJSON *r=rows?rows->child:NULL;r;r=r->next){
  cJSON *rel=cJSON_GetObjectItemCaseSensitive(r,"released");
  Id n=(rel&&cJSON_IsNumber(rel))?(Id)rel->valuedouble:0;
  total++;if(n>=rank)hit++;
 }
 cJSON_Delete(rows);
 if(total<5)return -1.0;
 return (double)hit/(double)total;
}
/* r26 候补排位：按（priority DESC,id ASC）计本人前方有效候补数 +1；等待中的本人算第 1。 */
static Id queue_rank(DB *d,Id slot,Id user){
 Id prio=db_num(d,"SELECT priority FROM waitlist WHERE slot_id=? AND user_id=? AND status='WAITING'","ii",slot,user);
 if(d->error)return 0;
 if(!prio&&prio==0){ /* 无 WAITING 行（未入队）：按本人角色优先级估算 */
  Id role=db_num(d,"SELECT role='ADMIN' FROM users WHERE id=?","i",user);
  if(d->error)return 0;
  prio=role?10:0;
 }
 Id ahead=db_num(d,"SELECT count(*) FROM waitlist w JOIN users u ON u.id=w.user_id WHERE w.slot_id=? AND w.status='WAITING' AND w.user_id<>? AND u.enabled=1 AND (w.priority>? OR (w.priority=? AND w.id<(SELECT COALESCE(MIN(id),9223372036854775807) FROM waitlist WHERE slot_id=? AND user_id=? AND status='WAITING')))",
  "iiiiii",slot,user,prio,prio,slot,user);
 if(d->error)return 0;
 return ahead+1;
}
/* r30 预取辅助：一次查询取齐建议列表所需的排位与概率数据，替代逐场次的 N+1 查询。
   rank：未入队用户在某场的排位 = Σ(priority≥本人优先级的 WAITING 计数)+1（与 queue_rank 对未入队者等价：
   未入队时 id 上界子查询为 NULL→COALESCE 取 INT64_MAX，同优先级全部计入前方）；本人已 WAITING 该场时仍走精确 queue_rank。
   概率：按星期分桶的历史释放经验分布（与 promote_probability 同口径，样本<5 为不下结论）。 */
typedef struct{Id slot;Id cnt[11];} SuggRankRow;
typedef struct{Id rel[8];int n;} SuggProbBucket; /* 每 weekday 一桶，35 天窗口同 weekday 已开场场次 ≤5 */
static int suggestion_prefetch(DB *d,Id lab,Id near_bound,Id far_bound,
                                  SuggRankRow **ranks,int *nrank,SuggProbBucket *prob /*[7]*/){
 *ranks=NULL;*nrank=0;memset(prob,0,7*sizeof *prob);
 cJSON *wr=db_rows(d,
  "SELECT w.slot_id,w.priority,count(*) AS c FROM waitlist w JOIN users u ON u.id=w.user_id "
  "WHERE w.status='WAITING' AND u.enabled=1 GROUP BY w.slot_id,w.priority","");
 if(d->error)return -1;
 int cap=16;*ranks=malloc(sizeof(SuggRankRow)*(size_t)cap);
 if(!*ranks){cJSON_Delete(wr);return -1;}
 for(cJSON *it=wr?wr->child:NULL;it;it=it->next){
  Id sid=0,pr=0,c=0;
  parse_id(jstr(it,"slot_id"),&sid);
  cJSON *jp=cJSON_GetObjectItemCaseSensitive(it,"priority"),*jc=cJSON_GetObjectItemCaseSensitive(it,"c");
  pr=(jp&&cJSON_IsNumber(jp))?(Id)jp->valuedouble:0;
  c=(jc&&cJSON_IsNumber(jc))?(Id)jc->valuedouble:0;
  if(pr<0)pr=0;
  if(pr>10)pr=10;
  SuggRankRow *row=NULL;
  for(int k=0;k<*nrank;k++)if((*ranks)[k].slot==sid){row=&(*ranks)[k];break;}
  if(!row){
   if(*nrank>=cap){cap*=2;SuggRankRow *nr=realloc(*ranks,sizeof(SuggRankRow)*(size_t)cap);if(!nr){free(*ranks);cJSON_Delete(wr);*ranks=NULL;return -1;}*ranks=nr;}
   row=&(*ranks)[(*nrank)++];row->slot=sid;memset(row->cnt,0,sizeof row->cnt);
  }
  row->cnt[pr]+=c;
 }
 cJSON_Delete(wr);
 cJSON *pr_rows=db_rows(d,
  "SELECT ((s.start_at+28800)/86400+4)%7 AS wd,"
  "(SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND ((r.status='CANCELLED' AND r.cancel_reason IN('USER','NO_SHOW','REJECTED')) OR r.status='EXPIRED')) AS rel "
  "FROM slots s WHERE s.lab_id=? AND s.start_at<? AND s.start_at>? GROUP BY s.id",
  "iii",lab,near_bound,far_bound);
 if(d->error){free(*ranks);*ranks=NULL;return -1;}
 for(cJSON *it=pr_rows?pr_rows->child:NULL;it;it=it->next){
  cJSON *jw=cJSON_GetObjectItemCaseSensitive(it,"wd"),*jr=cJSON_GetObjectItemCaseSensitive(it,"rel");
  int wd=(jw&&cJSON_IsNumber(jw))?(int)jw->valuedouble:0;
  Id rel=(jr&&cJSON_IsNumber(jr))?(Id)jr->valuedouble:0;
  if(wd<0||wd>6)continue;
  SuggProbBucket *b=&prob[wd];
  if(b->n<8)b->rel[b->n++]=rel;
 }
 cJSON_Delete(pr_rows);
 return 0;
}
Result suggestion_list(DB *d,const User *u,const Config *cfg,Id lab,Id start){
 Id now=now_sec();
 cJSON *rows=db_rows(d,
  "SELECT s.id,s.start_at,s.end_at,s.capacity,"
  "(SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND r.status IN('CONFIRMED','HELD')) AS taken,"
  "(SELECT count(*) FROM waitlist w JOIN users wu ON wu.id=w.user_id WHERE w.slot_id=s.id AND w.status='WAITING' AND wu.enabled=1) AS waiting_count,"
  "(SELECT id FROM reservations r WHERE r.slot_id=s.id AND r.status IN('CONFIRMED','HELD','PENDING') AND r.user_id=?) AS my_rid "
  "FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.lab_id=? AND s.enabled=1 AND l.enabled=1 AND s.start_at>? AND s.start_at<? ORDER BY s.start_at",
  "iiii",u->id,lab,start,start+7*86400);
 if(d->error)return db_failure(d);
 /* 用户侧上下文一次取齐：信用余额、本周已约数、本人有效预约区间（用于时间重叠判定） */
 Id credit=db_num(d,"SELECT credit FROM users WHERE id=?","i",u->id);
 if(d->error)return db_failure(d);
 Id day0=(now+28800)/86400*86400-28800;int wd=(int)(((day0+28800)/86400+3)%7);
 Id week_start=day0-(Id)wd*86400;
 Id used=db_num(d,"SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') AND s2.start_at>=?","ii",u->id,week_start);
 if(d->error)return db_failure(d);
 cJSON *mine=db_rows(d,"SELECT s.start_at,s.end_at FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD')","i",u->id);
 if(d->error)return db_failure(d);
 /* r30 预取：排位计数与星期分桶历史释放一次取齐（替代逐场次 N+1），本人已 WAITING 的场次仍走精确 queue_rank */
 SuggRankRow *ranks;int nrank;SuggProbBucket probs[7];
 if(suggestion_prefetch(d,lab,now-3600,now-35*86400,&ranks,&nrank,probs)){cJSON_Delete(rows);cJSON_Delete(mine);return db_failure(d);}
 int my_prio=u->admin?10:0;
 cJSON *mywait=db_rows(d,"SELECT slot_id FROM waitlist WHERE user_id=? AND status='WAITING'","i",u->id);
 if(d->error){free(ranks);cJSON_Delete(rows);cJSON_Delete(mine);return db_failure(d);}
 cJSON *out=cJSON_CreateArray();
 for(cJSON *sl=rows?rows->child:NULL;sl;sl=sl->next){
  cJSON *jstart=cJSON_GetObjectItemCaseSensitive(sl,"start_at"),*jend=cJSON_GetObjectItemCaseSensitive(sl,"end_at");
  Id st=(jstart&&cJSON_IsNumber(jstart))?(Id)jstart->valuedouble:0;
  Id en=(jend&&cJSON_IsNumber(jend))?(Id)jend->valuedouble:0;
  cJSON *taken_j=cJSON_GetObjectItemCaseSensitive(sl,"taken"),*cap_j=cJSON_GetObjectItemCaseSensitive(sl,"capacity");
  Id taken=(taken_j&&cJSON_IsNumber(taken_j))?(Id)taken_j->valuedouble:0;
  Id cap=(cap_j&&cJSON_IsNumber(cap_j))?(Id)cap_j->valuedouble:0;
  cJSON *myr=cJSON_GetObjectItemCaseSensitive(sl,"my_rid");
  int bookable=1,joinable=1;
  cJSON *reasons=cJSON_CreateArray();
  #define SUG_ADD(code,msg) do{cJSON *rr=cJSON_CreateObject();cJSON_AddStringToObject(rr,"code",code);cJSON_AddStringToObject(rr,"message",msg);cJSON_AddItemToArray(reasons,rr);}while(0)
  if(st<=now){SUG_ADD("STARTED","场次已开始，不再受理");bookable=0;joinable=0;}
  else{
   if(myr&&cJSON_IsNumber(myr)&&(Id)myr->valuedouble>0){SUG_ADD("ALREADY_RESERVED","您已持有该场次的预约或候补");bookable=0;joinable=0;}
   if(credit<=0){SUG_ADD("CREDIT_EXHAUSTED","信用余额为零，暂不能预约或候补");bookable=0;joinable=0;}
   if(cfg->quota_weekly>0&&used>=cfg->quota_weekly){SUG_ADD("WEEKLY_QUOTA","本周预约配额已用完");bookable=0;} /* 候补不入配额（BR13），不阻塞 joinable */
   if(cfg->lead_time>0&&st-now<cfg->lead_time){SUG_ADD("LEAD_TIME","距场次开始不足预约提前量");bookable=0;joinable=0;}
   for(cJSON *m=mine;m;m=m->next){
    cJSON *ms=cJSON_GetObjectItemCaseSensitive(m,"start_at"),*me=cJSON_GetObjectItemCaseSensitive(m,"end_at");
    if(ms&&me&&cJSON_IsNumber(ms)&&cJSON_IsNumber(me)&&st<(Id)me->valuedouble&&(Id)ms->valuedouble<en){
     SUG_ADD("TIME_CONFLICT","与您已有的有效预约时间重叠");bookable=0;joinable=0;break;}
   }
   if(taken>=cap){SUG_ADD("SLOT_FULL","场次已满，可加入候补排队");bookable=0;}
  }
  cJSON *item=cJSON_CreateObject();
  Id sid=0;parse_id(jstr(sl,"id"),&sid);
  jid(item,"slot_id",sid);
  cJSON_AddNumberToObject(item,"start_at",(double)st);
  cJSON_AddNumberToObject(item,"end_at",(double)en);
  cJSON_AddNumberToObject(item,"capacity",(double)cap);
  cJSON_AddNumberToObject(item,"taken",(double)taken);
  cJSON *wc=cJSON_GetObjectItemCaseSensitive(sl,"waiting_count");
  cJSON_AddNumberToObject(item,"waiting_count",(wc&&cJSON_IsNumber(wc))?wc->valuedouble:0);
  cJSON_AddBoolToObject(item,"bookable",bookable);
  cJSON_AddBoolToObject(item,"joinable",joinable);
   /* r26 知情候补（r30 预取版）：排位与概率由预取结构计算；已 WAITING 该场时回退精确 queue_rank */
   int im_waiting=0;
   for(cJSON *mw=mywait?mywait->child:NULL;mw;mw=mw->next){Id wslot=0;parse_id(jstr(mw,"slot_id"),&wslot);if(wslot==sid){im_waiting=1;break;}} /* db_rows 返回数组：必须从 child 遍历（r31/T75 抓出） */
   Id rank=0;
   if(!joinable)rank=0;
   else if(im_waiting)rank=queue_rank(d,sid,u->id);
   else{
    Id ahead=0;const SuggRankRow *row=NULL;
    for(int k=0;k<nrank;k++)if(ranks[k].slot==sid){row=&ranks[k];break;}
    if(row)for(int p=my_prio;p<=10;p++)ahead+=row->cnt[p];
    rank=ahead+1;
   }
   if(d->error)goto sug_fail_row;
   cJSON_AddNumberToObject(item,"queue_ahead",(double)(rank>0?rank-1:0));
   double prob=-1.0;
   if(rank>0){
    int wd2=(int)(((st+28800)/86400+4)%7);
    const SuggProbBucket *b=&probs[wd2];
    if(b->n>=5){Id hit=0;for(int k=0;k<b->n;k++)if(b->rel[k]>=rank)hit++;prob=(double)hit/(double)b->n;}
   }
   if(prob<0)cJSON_AddNullToObject(item,"promote_probability");
   else cJSON_AddNumberToObject(item,"promote_probability",prob);
  cJSON_AddItemToObject(item,"reasons",reasons);
  cJSON_AddItemToArray(out,item);
  continue;
sug_fail_row:
  cJSON_Delete(reasons);cJSON_Delete(out);cJSON_Delete(rows);cJSON_Delete(mine);cJSON_Delete(mywait);free(ranks);return db_failure(d);
 }
 #undef SUG_ADD
 cJSON_Delete(rows);cJSON_Delete(mine);cJSON_Delete(mywait);free(ranks);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"suggestions",out);
 return result(200,"OK","查询成功",j);
}
/* -- r26 公平性审计：按用户聚合候补经历（入队/转正/退出/跳过/转正率/平均等待秒），
   并给出全站 Jain 公平指数 = (Σx)²/(n·Σx²)，x 为该用户近 N 天获得预约数（任何状态，代表资源获取机会）。 -- */
Result fairness_admin(DB *d,int days){
 Id now=now_sec();Id since=now-(Id)days*86400;
 /* r33 N+1 消除：7 个相关子查询×每用户 → 4 条 GROUP BY 预聚合并内存拼装（与 r30 建议端点同手法） */
 cJSON *wjoin=db_rows(d,"SELECT user_id,count(*) AS c FROM waitlist WHERE created_at>=? GROUP BY user_id","i",since);
 cJSON *wprom=db_rows(d,"SELECT user_id,count(*) AS c FROM waitlist WHERE status='PROMOTED' AND created_at>=? GROUP BY user_id","i",since);
 cJSON *wwd=db_rows(d,"SELECT user_id,count(*) AS c FROM waitlist WHERE status='WITHDRAWN' AND created_at>=? GROUP BY user_id","i",since);
 cJSON *wsk=db_rows(d,"SELECT user_id,count(*) AS c FROM waitlist WHERE status='SKIPPED' AND created_at>=? GROUP BY user_id","i",since);
 cJSON *wwait=db_rows(d,"SELECT w.user_id,avg(r.created_at-w.created_at) AS a FROM waitlist w JOIN reservations r ON r.id=w.promoted_reservation_id WHERE w.status='PROMOTED' AND w.created_at>=? GROUP BY w.user_id","i",since);
 cJSON *gcount=db_rows(d,"SELECT user_id,count(*) AS c FROM reservations WHERE created_at>=? GROUP BY user_id","i",since);
 if(d->error){cJSON_Delete(wjoin);cJSON_Delete(wprom);cJSON_Delete(wwd);cJSON_Delete(wsk);cJSON_Delete(wwait);cJSON_Delete(gcount);return db_failure(d);}
 cJSON *users=db_rows(d,"SELECT u.id,u.username,COALESCE(g.c,0) AS granted FROM users u LEFT JOIN (SELECT user_id,count(*) AS c FROM reservations WHERE created_at>=? GROUP BY user_id) g ON g.user_id=u.id WHERE u.enabled=1 ORDER BY granted DESC,u.id","i",since);
 if(d->error){cJSON_Delete(wjoin);cJSON_Delete(wprom);cJSON_Delete(wwd);cJSON_Delete(wsk);cJSON_Delete(wwait);cJSON_Delete(gcount);return db_failure(d);}
 cJSON *out=cJSON_CreateArray();
 double sum=0,sum2=0;long long n=0;
 for(cJSON *u=users?users->child:NULL;u;u=u->next){
  Id uid=0;parse_id(jstr(u,"id"),&uid);
  cJSON *row=cJSON_CreateObject();
  jid(row,"id",uid);
  cJSON_AddStringToObject(row,"username",jstr(u,"username"));
  Id joined=0,promoted=0,withdrawn=0,skipped=0,granted=0;double avgwait=0;int has_wait=0;
  cJSON *it;
  cJSON_ArrayForEach(it,wjoin){Id x=0;parse_id(jstr(it,"user_id"),&x);if(x==uid){joined=(Id)cJSON_GetObjectItemCaseSensitive(it,"c")->valuedouble;break;}}
  cJSON_ArrayForEach(it,wprom){Id x=0;parse_id(jstr(it,"user_id"),&x);if(x==uid){promoted=(Id)cJSON_GetObjectItemCaseSensitive(it,"c")->valuedouble;break;}}
  cJSON_ArrayForEach(it,wwd){Id x=0;parse_id(jstr(it,"user_id"),&x);if(x==uid){withdrawn=(Id)cJSON_GetObjectItemCaseSensitive(it,"c")->valuedouble;break;}}
  cJSON_ArrayForEach(it,wsk){Id x=0;parse_id(jstr(it,"user_id"),&x);if(x==uid){skipped=(Id)cJSON_GetObjectItemCaseSensitive(it,"c")->valuedouble;break;}}
  cJSON_ArrayForEach(it,wwait){Id x=0;parse_id(jstr(it,"user_id"),&x);if(x==uid){cJSON *a=cJSON_GetObjectItemCaseSensitive(it,"a");if(a&&cJSON_IsNumber(a)){avgwait=a->valuedouble;has_wait=1;}break;}}
  {cJSON *gc=cJSON_GetObjectItemCaseSensitive(u,"granted");granted=(gc&&cJSON_IsNumber(gc))?(Id)gc->valuedouble:0;}
  cJSON_AddNumberToObject(row,"joined",(double)joined);
  cJSON_AddNumberToObject(row,"promoted",(double)promoted);
  cJSON_AddNumberToObject(row,"withdrawn",(double)withdrawn);
  cJSON_AddNumberToObject(row,"skipped",(double)skipped);
  if(has_wait)cJSON_AddNumberToObject(row,"avg_wait_s",avgwait);else cJSON_AddNullToObject(row,"avg_wait_s");
  cJSON_AddNumberToObject(row,"granted",(double)granted);
  cJSON_AddItemToArray(out,row);
  sum+=(double)granted;sum2+=(double)granted*(double)granted;n++;
 }
 cJSON_Delete(wjoin);cJSON_Delete(wprom);cJSON_Delete(wwd);cJSON_Delete(wsk);cJSON_Delete(wwait);cJSON_Delete(gcount);cJSON_Delete(users);
 double jain=(n>0&&sum2>0)?(sum*sum)/((double)n*sum2):1.0;
 cJSON *j=cJSON_CreateObject();
 cJSON_AddItemToObject(j,"users",out);
 cJSON_AddNumberToObject(j,"jain_index",jain);
 cJSON_AddNumberToObject(j,"days",(double)days);
 cJSON_AddStringToObject(j,"jain_definition","(Σx)²/(n·Σx²)，x=该用户窗口内获得预约数；1 为完全公平");
 cJSON_AddStringToObject(j,"wait_definition","avg_wait_s=转正时刻(reservations.created_at)−入队时刻(waitlist.created_at)的均值");
 return result(200,"OK","查询成功",j);
}
/* -- r33 管理员强制操作（现场运营：设备损坏/用户失联/突发闭馆）：
   force-complete：CONFIRMED 且已签到未签退 → 代签退（checked_out_at=now，保实机时口径），用户不需在场；
   force-cancel：CONFIRMED/HELD/PENDING 任意 → CANCELLED（cancel_reason='ADMIN'，扩展原因枚举）+ 同事务 FIFO 补位 + 通知。
   不能对已签到者 force-cancel（先 force-complete 或让爽约回收处理，避免签到中记录被取消的矛盾状态）。 -- */
Result reservation_force(DB *d,const Config *cfg,const User *u,Id target,const char *op,const char *key){
 Result r={500,NULL};cJSON *row=NULL;
 if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
 row=db_first(d,"SELECT r.user_id,r.status,r.checked_in_at,r.checked_out_at,r.slot_id,s.start_at,s.end_at FROM reservations r JOIN slots s ON s.id=r.slot_id WHERE r.id=?","i",target);
 if(d->error)goto force_fail;
 if(!row){r=result(404,"NOT_FOUND","记录不存在",NULL);goto force_save;}
 if(!strcmp(op,"force-complete")){
  if(strcmp(jstr(row,"status"),"CONFIRMED")){r=result(409,"STATE_CONFLICT","仅有效预约可强制完成",NULL);goto force_save;}
  {cJSON *ci=cJSON_GetObjectItemCaseSensitive(row,"checked_in_at");
   if(!(ci&&cJSON_IsNumber(ci)&&ci->valuedouble>0)){r=result(409,"STATE_CONFLICT","该预约尚未签到，请用强制取消",NULL);goto force_save;}}
  {cJSON *co=cJSON_GetObjectItemCaseSensitive(row,"checked_out_at");
   if(co&&cJSON_IsNumber(co)&&co->valuedouble>0){
    cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);cJSON_AddNumberToObject(j,"checked_out_at",co->valuedouble);
    r=result(200,"OK","该预约已签退",j);goto force_save;}}
  {Id when=now_sec();
   if(!db_run(d,"UPDATE reservations SET checked_out_at=? WHERE id=?","ii",when,target))goto force_fail;
   {Id owner=0;parse_id(jstr(row,"user_id"),&owner);
    event(d,u->id,"FORCE_COMPLETE",target,key);
    {Id fslot=0;parse_id(jstr(row,"slot_id"),&fslot); /* _id 列是 jid 字符串，必须 parse_id（第 13 例） */
     notify(d,owner,"NOTICE","预约已被管理员结束","管理员已结束你的本次使用（视为已签退），如有疑问请联系管理员。",fslot,target);}}
   cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);cJSON_AddNumberToObject(j,"checked_out_at",(double)when);
   r=result(200,"OK","已强制完成（代签退）",j);}
 }else{ /* force-cancel */
  const char *st=jstr(row,"status");
  if(strcmp(st,"CONFIRMED")&&strcmp(st,"HELD")&&strcmp(st,"PENDING")){r=result(409,"STATE_CONFLICT","该状态不可强制取消",NULL);goto force_save;}
  {cJSON *ci=cJSON_GetObjectItemCaseSensitive(row,"checked_in_at");
   if(ci&&cJSON_IsNumber(ci)&&ci->valuedouble>0){r=result(409,"STATE_CONFLICT","已签到记录不可强制取消（请先强制完成）",NULL);goto force_save;}}
  {Id owner=0,slot2=0;parse_id(jstr(row,"user_id"),&owner);parse_id(jstr(row,"slot_id"),&slot2); /* _id 列 parse_id（第 13 例） */
   if(!db_run(d,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='ADMIN',hold_deadline=NULL WHERE id=?","ii",now_sec(),target))goto force_fail;
   event(d,u->id,"FORCE_CANCEL",target,key);
   notify(d,owner,"NOTICE","预约已被管理员取消","你的预约被管理员取消（名额已按候补顺序释放），如有疑问请联系管理员。",slot2,target);
   Id promoted=promote_fill(d,cfg,slot2,u->id,key);
   if(d->error)goto force_fail;
   cJSON *j=cJSON_CreateObject();jid(j,"reservation_id",target);
   if(promoted)jid(j,"promoted_reservation_id",promoted);else cJSON_AddNullToObject(j,"promoted_reservation_id");
   r=result(200,"OK","已强制取消，候补已按规则递补",j);}
 }
force_save:
 if(d->error)goto force_fail;
 if(!r.body){d->error=SQLITE_NOMEM;goto force_fail;}
 if(!db_run(d,"COMMIT",""))goto force_fail;
 cJSON_Delete(row);
 return r;
force_fail:
 sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);cJSON_Delete(row);cJSON_Delete(r.body);return db_failure(d);
}
