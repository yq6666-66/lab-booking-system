/* r5/security：登录防爆破与写操作限流。判定逻辑为纯函数（可单测），存储为进程内存态哈希表，重启清零。 */
#include "app.h"
#include <windows.h>
#include <stdio.h>
#include <string.h>

/* 令牌桶：tokens 以毫令为单位，每 refill_sec 秒补充 1000 毫令，上限 burst*1000。 */
int bucket_allow(RateBucket *b,Id now_ms,int burst,int refill_sec){
 if(!b||burst<1||refill_sec<1)return 1;
 if(!b->started){b->started=1;b->tokens=(double)burst*1000.0;b->last_ms=now_ms;}
 else if(now_ms>b->last_ms){
  b->tokens+=(double)(now_ms-b->last_ms)/(double)refill_sec;
  if(b->tokens>(double)burst*1000.0)b->tokens=(double)burst*1000.0;
  b->last_ms=now_ms;
 }
 if(b->tokens>=1000.0){b->tokens-=1000.0;return 1;}
 return 0;
}
int login_allow(const LoginGuard *g,Id now_ms){return !g||g->locked_until<=now_ms;}
void login_record_fail(LoginGuard *g,Id now_ms,int max_fails,int lockout_sec){
 if(!g||max_fails<1)return;
 g->fails++;
 if(g->fails>=max_fails){g->locked_until=now_ms+(Id)lockout_sec*1000;g->fails=0;}
}
void login_record_ok(LoginGuard *g){if(g){g->fails=0;g->locked_until=0;}}

typedef struct { char key[80]; RateBucket bucket; LoginGuard guard; } Entry;
static Entry table[1024];
static SRWLOCK rl_lock=SRWLOCK_INIT;
static int cfg_burst=30,cfg_refill=1,cfg_maxfails=5,cfg_lockout=900;
void rl_configure(const Config *c){
 if(!c)return;
 if(c->rate_burst>0)cfg_burst=c->rate_burst;
 if(c->rate_refill_sec>0)cfg_refill=c->rate_refill_sec;
 if(c->login_max_fails>0)cfg_maxfails=c->login_max_fails;
 if(c->login_lockout>0)cfg_lockout=c->login_lockout;
}
static Entry *entry_get(const char *key){ /* 调用方持锁；表满时返回 NULL（放行，不因限流器故障拒绝服务） */
 unsigned idx=0;
 for(const char *p=key;*p;p++)idx=idx*131u+(unsigned char)*p;
 idx&=1023u;
 for(int i=0;i<1024;i++){
  Entry *e=&table[(idx+(unsigned)i)&1023u];
  if(!e->key[0]){snprintf(e->key,sizeof e->key,"%s",key);return e;}
  if(!strcmp(e->key,key))return e;
 }
 return NULL;
}
int rl_login_gate(const char *username){
 char key[80];snprintf(key,sizeof key,"L:%s",username?username:"");
 Id now=now_sec()*1000;int ok=1;
 AcquireSRWLockExclusive(&rl_lock);
 Entry *e=entry_get(key);
 if(e)ok=login_allow(&e->guard,now);
 ReleaseSRWLockExclusive(&rl_lock);
 return ok;
}
void rl_login_fail(const char *username){
 char key[80];snprintf(key,sizeof key,"L:%s",username?username:"");
 Id now=now_sec()*1000;
 AcquireSRWLockExclusive(&rl_lock);
 Entry *e=entry_get(key);
 if(e)login_record_fail(&e->guard,now,cfg_maxfails,cfg_lockout);
 ReleaseSRWLockExclusive(&rl_lock);
}
void rl_login_ok(const char *username){
 char key[80];snprintf(key,sizeof key,"L:%s",username?username:"");
 AcquireSRWLockExclusive(&rl_lock);
 Entry *e=entry_get(key);
 if(e)login_record_ok(&e->guard);
 ReleaseSRWLockExclusive(&rl_lock);
}
int rl_consume(Id user_id){
 char key[80];snprintf(key,sizeof key,"U:%lld",(long long)user_id);
 Id now=now_sec()*1000;int ok=1;
 AcquireSRWLockExclusive(&rl_lock);
 Entry *e=entry_get(key);
 if(e)ok=bucket_allow(&e->bucket,now,cfg_burst,cfg_refill);
 ReleaseSRWLockExclusive(&rl_lock);
 return ok;
}
/* 注册接口闸门：全局桶（回环部署无来源 IP 可区分），防脚本刷注册 */
int rl_register_gate(void){
 Id now=now_sec()*1000;int ok=1;
 AcquireSRWLockExclusive(&rl_lock);
 Entry *e=entry_get("R:register");
 if(e)ok=bucket_allow(&e->bucket,now,20,30);
 ReleaseSRWLockExclusive(&rl_lock);
 return ok;
}
