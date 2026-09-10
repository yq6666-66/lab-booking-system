#include "app.h"
#include "civetweb.h"
#include <sodium.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <windows.h>
static volatile sig_atomic_t stopping=0;
static void on_stop(int sig){(void)sig;stopping=1;}
static Result invalid(void){return result(400,"INVALID_INPUT","请求参数不正确",NULL);}
static int origin_ok(struct mg_connection *c,const Config *cfg){
 char one[80],two[80];snprintf(one,sizeof one,"http://127.0.0.1:%d",cfg->port);snprintf(two,sizeof two,"http://localhost:%d",cfg->port);
 const char *o=mg_get_header(c,"Origin");return !o||!strcmp(o,one)||!strcmp(o,two);
}
static int host_ok(struct mg_connection *c,const Config *cfg){
 char one[80],two[80];snprintf(one,sizeof one,"127.0.0.1:%d",cfg->port);snprintf(two,sizeof two,"localhost:%d",cfg->port);
 const char *h=mg_get_header(c,"Host");return h&&(!strcmp(h,one)||!strcmp(h,two));
}
static cJSON *user_data(const User *u){cJSON *j=cJSON_CreateObject(),*v=cJSON_CreateObject();jid(v,"id",u->id);cJSON_AddStringToObject(v,"username",u->username);cJSON_AddStringToObject(v,"role",u->admin?"ADMIN":"USER");cJSON_AddItemToObject(j,"user",v);cJSON_AddStringToObject(j,"csrf_token",u->csrf);return j;}
static int authenticated(DB *d,struct mg_connection *c,User *u,char hash[65]){
 char token[65];const char *cookie=mg_get_header(c,"Cookie");if(!cookie||mg_get_cookie(cookie,"lab_session",token,sizeof token)!=64)return 0;hash_text(token,hash);
 cJSON *r=db_first(d,"SELECT u.id,u.username,u.role,s.csrf_token FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1","si",hash,now_sec());
 if(!r)return 0;
 memset(u,0,sizeof *u);parse_id(jstr(r,"id"),&u->id);u->admin=!strcmp(jstr(r,"role"),"ADMIN");snprintf(u->username,sizeof u->username,"%s",jstr(r,"username"));snprintf(u->csrf,sizeof u->csrf,"%s",jstr(r,"csrf_token"));cJSON_Delete(r);return 1;
}
static int text_ok(const char *s,size_t max,int empty){return s&&strlen(s)<=max&&(empty||strlen(s)>0);}
static Result login(DB *d,const cJSON *body,char cookie[256]){
 const char *name=jstr(body,"username"),*pw=jstr(body,"password");if(!text_ok(name,64,0)||!text_ok(pw,128,0))return invalid();
 if(!rl_login_gate(name))return result(429,"LOGIN_LOCKED","登录尝试过于频繁，请稍后再试",NULL);
 cJSON *r=db_first(d,"SELECT id,username,role,password_hash FROM users WHERE username=? AND enabled=1","s",name);
 if(d->error)return db_failure(d);
 if(!r||crypto_pwhash_str_verify(jstr(r,"password_hash"),pw,strlen(pw))){
  rl_login_fail(name);
  Id uid=0;
  if(r&&parse_id(jstr(r,"id"),&uid))db_run(d,"INSERT INTO operation_events(actor_id,action,entity_id,created_at) VALUES(?,?,0,?)","isi",uid,"LOGIN_FAILED",now_sec());
  cJSON_Delete(r);return result(401,"UNAUTHORIZED","用户名或密码不正确",NULL);
 }
 rl_login_ok(name);
 User u={0};parse_id(jstr(r,"id"),&u.id);u.admin=!strcmp(jstr(r,"role"),"ADMIN");snprintf(u.username,sizeof u.username,"%s",name);cJSON_Delete(r);
 char token[65],hash[65];random_hex(token);hash_text(token,hash);random_hex(u.csrf);
 db_run(d,"DELETE FROM sessions WHERE expires_at<?","i",now_sec());
 if(!db_run(d,"INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at) VALUES(?,?,?,?)","sisi",hash,u.id,u.csrf,now_sec()+7200))return db_failure(d);
 snprintf(cookie,256,"Set-Cookie: lab_session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=7200\r\n",token);sodium_memzero(token,sizeof token);
 metrics_inc_login();
 return result(200,"OK","登录成功",user_data(&u));
}
static int path_id(const char *path,const char *prefix,const char *suffix,Id *id){
 size_t a=strlen(prefix),b=strlen(suffix),n=strlen(path);if(n<=a+b||strncmp(path,prefix,a)||strcmp(path+n-b,suffix))return 0;
 size_t len=n-a-b;if(len>18)return 0;char tmp[32];memcpy(tmp,path+a,len);tmp[len]=0;return parse_id(tmp,id);
}
static int path_hex(const char *path,const char *prefix,const char *suffix,char *out,size_t n){
 size_t a=strlen(prefix),b=strlen(suffix),len=strlen(path);if(len<=a+b||strncmp(path,prefix,a)||strcmp(path+len-b,suffix))return 0;
 size_t need=len-a-b;if(need!=64||need+1>n)return 0;
 memcpy(out,path+a,need);out[need]=0;return 1;
}
static int query(const struct mg_request_info *ri,const char *key,char *out,size_t n){if(!ri->query_string){out[0]=0;return 0;}return mg_get_var(ri->query_string,strlen(ri->query_string),key,out,n)>0;}
static int pager(const struct mg_request_info *ri,int *page,int *size){
 char a[16]={0},b[16]={0};query(ri,"page",a,sizeof a);query(ri,"page_size",b,sizeof b);
 int p=page_arg(a,1,1,1000000),s=page_arg(b,20,1,200);if(p<0||s<0)return 0;*page=p;*size=s;return 1;
}
static Result admin(DB *d,const char *path,const cJSON *body){
 Id target=0;
 if(!strcmp(path,"/api/admin/slots/publish")){
  Id lab=0,start=date_start(jstr(body,"start_date")),end=date_start(jstr(body,"end_date")),capacity=1;
  cJSON *cap=cJSON_GetObjectItemCaseSensitive(body,"capacity");
  if(cap&&cJSON_IsString(cap)&&!parse_id(cap->valuestring,&capacity))return invalid();
  if(cap&&cJSON_IsNumber(cap))capacity=(Id)cap->valuedouble;
  if(capacity<1||capacity>200)return invalid();
  if(!parse_id(jstr(body,"lab_id"),&lab)||start<0||end<start||end-start>13*86400)return invalid();
  if(!db_run(d,"BEGIN IMMEDIATE",""))return db_failure(d);
  if(!db_num(d,"SELECT count(*) FROM labs WHERE id=? AND enabled=1","i",lab)){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return d->error?db_failure(d):result(409,"STATE_CONFLICT","实验室不存在或已停用",NULL);}
  int n=publish_slots(d,lab,start,end,capacity);
  if(d->error||!db_run(d,"COMMIT","")){sqlite3_exec(d->sql,"ROLLBACK",NULL,NULL,NULL);return db_failure(d);}
  cJSON *j=cJSON_CreateObject();cJSON_AddNumberToObject(j,"created",n);return result(200,"OK","场次发布完成",j);
 }
 if(!strcmp(path,"/api/admin/labs")||path_id(path,"/api/admin/labs/","/update",&target)){
  const char *name=jstr(body,"name"),*loc=jstr(body,"location"),*desc=jstr(body,"description");cJSON *enabled=cJSON_GetObjectItemCaseSensitive(body,"enabled");
  if(!text_ok(name,180,0)||!text_ok(loc,180,0)||!text_ok(desc,1000,1)||(target&&!cJSON_IsBool(enabled)))return invalid();
  int ok;if(target)ok=db_run(d,"UPDATE labs SET name=?,location=?,description=?,enabled=? WHERE id=?","sssii",name,loc,desc,(Id)cJSON_IsTrue(enabled),target);
  else ok=db_run(d,"INSERT INTO labs(name,location,description) VALUES(?,?,?)","sss",name,loc,desc);
  if(!ok){if((d->error&255)==SQLITE_CONSTRAINT){d->error=0;return result(409,"STATE_CONFLICT","实验室名称已存在",NULL);}return db_failure(d);}
  if(target&&!sqlite3_changes(d->sql))return result(404,"NOT_FOUND","实验室不存在",NULL);
  cJSON *j=cJSON_CreateObject();jid(j,"lab_id",target?target:sqlite3_last_insert_rowid(d->sql));return result(200,"OK","实验室已保存",j);
 }
 return result(404,"NOT_FOUND","接口不存在",NULL);
}
static Result dispatch(DB *d,struct mg_connection *c,const Config *cfg,const cJSON *body,char cookie[256]){
 const struct mg_request_info *ri=mg_get_request_info(c);const char *path=ri->local_uri;int post=!strcmp(ri->request_method,"POST");
 if(!strcmp(path,"/api/login"))return post?login(d,body,cookie):result(405,"METHOD_NOT_ALLOWED","请求方法不支持",NULL);
 User u={0};char tokenhash[65];if(!authenticated(d,c,&u,tokenhash))return d->error?db_failure(d):result(401,"UNAUTHORIZED","请先登录",NULL);
 if(post){const char *csrf=mg_get_header(c,"X-CSRF-Token");if(!csrf||strlen(csrf)!=64||sodium_memcmp(csrf,u.csrf,64))return result(403,"CSRF","请求校验失败，请刷新后重试",NULL);
  if(!rl_consume(u.id))return result(429,"RATE_LIMITED","操作过于频繁，请稍后再试",NULL);}
 if(!post){
  if(!strcmp(path,"/api/me"))return result(200,"OK","查询成功",user_data(&u));
  if(!strcmp(path,"/api/labs")){cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"labs",db_rows(d,"SELECT id,name,location,description,enabled FROM labs ORDER BY id",""));return result(200,"OK","查询成功",j);}
  if(!strcmp(path,"/api/slots")){
   char a[40],dt[32];Id lab=0;query(ri,"lab_id",a,sizeof a);query(ri,"date",dt,sizeof dt);Id start=date_start(dt);if(!parse_id(a,&lab)||start<0)return invalid();
   cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"slots",db_rows(d,
    "SELECT s.id,s.lab_id,s.start_at,s.end_at,s.enabled,s.capacity,l.enabled AS lab_enabled,(SELECT count(*) FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED') AS confirmed_count,(SELECT count(*) FROM waitlist w JOIN users wu ON wu.id=w.user_id WHERE w.slot_id=s.id AND w.status='WAITING' AND wu.enabled=1) AS waiting_count,(SELECT id FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED' AND r.user_id=?) AS my_reservation_id,(SELECT checked_in_at FROM reservations r WHERE r.slot_id=s.id AND r.status='CONFIRMED' AND r.user_id=?) AS my_checked_in_at,(SELECT id FROM waitlist w WHERE w.slot_id=s.id AND w.status='WAITING' AND w.user_id=?) AS my_waitlist_id FROM slots s JOIN labs l ON l.id=s.lab_id WHERE s.lab_id=? AND s.start_at>=? AND s.start_at<? ORDER BY s.start_at",
    "iiiiii",u.id,u.id,u.id,lab,start,start+86400));
   cJSON_AddNumberToObject(j,"checkin_window",(double)cfg->checkin_window);return result(200,"OK","查询成功",j);
  }
  if(!strcmp(path,"/api/me/records")){int pg=1,ps=20;if(!pager(ri,&pg,&ps))return invalid();return records(d,&u,0,0,pg,ps);}
  if(!strcmp(path,"/api/me/notifications")){int pg=1,ps=20;if(!pager(ri,&pg,&ps))return invalid();char uf[8]={0};query(ri,"unread",uf,sizeof uf);return notifications(d,&u,!strcmp(uf,"1")||!strcmp(uf,"true"),pg,ps);}
  if(!strcmp(path,"/api/me/sessions"))return sessions_list(d,&u,tokenhash);
  if(!strcmp(path,"/api/admin/records")){if(!u.admin)return result(403,"FORBIDDEN","需要管理员权限",NULL);char dt[32];Id date=0;if(query(ri,"date",dt,sizeof dt)){date=date_start(dt);if(date<0)return invalid();}int pg=1,ps=20;if(!pager(ri,&pg,&ps))return invalid();return records(d,&u,1,date,pg,ps);}
  if(!strcmp(path,"/api/admin/stats/export")){
   if(!u.admin)return result(403,"FORBIDDEN","需要管理员权限",NULL);
   char s1[32],s2[32];if(!query(ri,"start_date",s1,sizeof s1)||!query(ri,"end_date",s2,sizeof s2))return invalid();
   Id a=date_start(s1),b=date_start(s2);if(a<0||b<a||b-a>30*86400)return invalid();
   return stats_export(d,a,b);
  }
  if(!strcmp(path,"/api/admin/stats")){
   if(!u.admin)return result(403,"FORBIDDEN","需要管理员权限",NULL);
   char s1[32],s2[32];if(!query(ri,"start_date",s1,sizeof s1)||!query(ri,"end_date",s2,sizeof s2))return invalid();
   Id a=date_start(s1),b=date_start(s2);if(a<0||b<a||b-a>30*86400)return invalid();
   return stats(d,a,b);
  }
  if(!strcmp(path,"/api/admin/metrics")){if(!u.admin)return result(403,"FORBIDDEN","需要管理员权限",NULL);return result(200,"OK","查询成功",metrics_snapshot());}
  return result(404,"NOT_FOUND","接口不存在",NULL);
 }
 if(!strcmp(path,"/api/logout")){db_run(d,"DELETE FROM sessions WHERE token_hash=?","s",tokenhash);strcpy(cookie,"Set-Cookie: lab_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0\r\n");return result(200,"OK","已退出",NULL);}
 {char sh[65];const char *k=jstr(body,"request_id");
  if(!strcmp(path,"/api/me/password")){if(!uuid_valid(k))return invalid();return password_change(d,&u,jstr(body,"old_password"),jstr(body,"new_password"),tokenhash,k);}
  if(!strcmp(path,"/api/me/notifications/read")){if(!uuid_valid(k))return invalid();return notifications_read(d,&u,body,k);}
  if(path_hex(path,"/api/me/sessions/","/revoke",sh,sizeof sh)){if(!uuid_valid(k))return invalid();return session_revoke(d,&u,sh,k);}
 }
 if(!strncmp(path,"/api/admin/",11)){if(!u.admin)return result(403,"FORBIDDEN","需要管理员权限",NULL);return admin(d,path,body);}
 const char *action=NULL;Id target=0;
 if(!strcmp(path,"/api/reservations")){action="reserve";parse_id(jstr(body,"slot_id"),&target);}
 else if(!strcmp(path,"/api/waitlist")){action="wait";parse_id(jstr(body,"slot_id"),&target);}
 else if(path_id(path,"/api/reservations/","/cancel",&target))action="cancel";
 else if(path_id(path,"/api/reservations/","/checkin",&target))action="checkin";
 else if(path_id(path,"/api/waitlist/","/withdraw",&target))action="withdraw";
 else return result(404,"NOT_FOUND","接口不存在",NULL);
 const char *key=jstr(body,"request_id");if(!target||!uuid_valid(key))return invalid();
 return booking(d,cfg,&u,action,target,key);
}
static int api(struct mg_connection *c,void *userdata){
 LARGE_INTEGER mfreq,mt0;QueryPerformanceFrequency(&mfreq);QueryPerformanceCounter(&mt0);
 const Config *cfg=userdata;const struct mg_request_info *ri=mg_get_request_info(c);Result r={0,NULL};cJSON *body=NULL;char cookie[256]={0};DB d={0};char *raw=NULL,*serialized=NULL;
 if(!host_ok(c,cfg)){r=result(403,"FORBIDDEN","Host 不被允许",NULL);goto send;}
 int post=!strcmp(ri->request_method,"POST");
 if(!post&&strcmp(ri->request_method,"GET")){r=result(405,"METHOD_NOT_ALLOWED","请求方法不支持",NULL);goto send;}
 if(!post&&!strcmp(ri->local_uri,"/api/health")){cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"status","ok");r=result(200,"OK","服务运行中",j);goto send;}
 if(post){
  if(!origin_ok(c,cfg)){r=result(403,"FORBIDDEN","来源不被允许",NULL);goto send;}
  if(ri->content_length>16384){r=result(413,"TOO_LARGE","请求体过大",NULL);goto send;}
  const char *ct=mg_get_header(c,"Content-Type");
  if(ri->content_length<=0||!ct||strncmp(ct,"application/json",16)||(ct[16]&&ct[16]!=';'&&ct[16]!=' ')){r=invalid();goto send;}
  size_t n=(size_t)ri->content_length;raw=malloc(n+1);if(!raw){r=result(500,"INTERNAL_ERROR","内存不足",NULL);goto send;}
  size_t got=0;while(got<n){int k=mg_read(c,raw+got,n-got);if(k<=0)break;got+=(size_t)k;}raw[got]=0;
  const char *end=NULL;body=cJSON_ParseWithLengthOpts(raw,got+1,&end,1);
  if(got!=n||!cJSON_IsObject(body)){r=invalid();goto send;}
  for(cJSON *a=body->child;a;a=a->next)for(cJSON *b=a->next;b;b=b->next)if(!strcmp(a->string,b->string)){r=invalid();goto send;}
 }
 if(!db_open(&d,cfg->db_path)){r=db_failure(&d);goto send;}
 r=dispatch(&d,c,cfg,body,cookie);
 if(d.error){cJSON_Delete(r.body);r=db_failure(&d);cookie[0]=0;}
send:
 db_close(&d);if(raw){sodium_memzero(raw,strlen(raw));free(raw);}cJSON_Delete(body);
 serialized=r.body?cJSON_PrintUnformatted(r.body):NULL;
 if(!serialized){r.status=500;cookie[0]=0;}
 const char *out=serialized?serialized:"{\"code\":\"INTERNAL_ERROR\",\"message\":\"Memory error\",\"data\":{}}";
 mg_printf(c,"HTTP/1.1 %d %s\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: %lu\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n%s\r\n",r.status,r.status==200?"OK":"Error",(unsigned long)strlen(out),cookie);mg_write(c,out,strlen(out));cJSON_free(serialized);cJSON_Delete(r.body);
 {LARGE_INTEGER mt1;QueryPerformanceCounter(&mt1);metrics_record_request(r.status,(double)(mt1.QuadPart-mt0.QuadPart)*1000.0/(double)mfreq.QuadPart);}
 return 1;
}
/* 后台扫描：定期释放超过签到时限仍未签到的预约，并按 FIFO 补位。 */
static void *sweeper(void *arg){
 const Config *cfg=arg;int waited=0;
 while(!stopping){
  Sleep(200);waited+=200;
  if(waited>=cfg->sweep_interval*1000){waited=0;sweep_once(cfg);}
 }
 return NULL;
}
void sweep_start(const Config *config){
 if(config->sweep_interval<=0)return;
 mg_start_thread(sweeper,(void*)config);
}
int serve(const Config *cfg){
 char port[48];snprintf(port,sizeof port,"127.0.0.1:%d",cfg->port);
 const char *opts[]={"listening_ports",port,"document_root",cfg->web_path,"num_threads","8","enable_directory_listing","no","request_timeout_ms","5000","enable_keep_alive","no",NULL};
 struct mg_callbacks callbacks;memset(&callbacks,0,sizeof callbacks);mg_init_library(0);
 rl_configure(cfg);
 struct mg_context *ctx=mg_start(&callbacks,NULL,opts);if(!ctx){fprintf(stderr,"HTTP server startup failed. Check port and web directory.\n");mg_exit_library();return 1;}
 mg_set_request_handler(ctx,"/api",api,(void*)cfg);signal(SIGINT,on_stop);signal(SIGTERM,on_stop);
 sweep_start(cfg);
 printf("Lab Booking ready: http://127.0.0.1:%d\n",cfg->port);fflush(stdout);
 while(!stopping)Sleep(100);
 mg_stop(ctx);mg_exit_library();return 0;
}
