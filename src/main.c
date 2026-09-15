#include "app.h"
#include <sodium.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <direct.h>
#include <windows.h>
/* -- r12 演示数据：为过去 demo_days 天生成可复现的预约/签到/爽约/候补历史（固定随机种子 42） -- */
static int gen_demo(DB *db,int days,const char *password){
 (void)password;
 unsigned long long rng=42;
 #define DEMO_NEXT() (rng=rng*6364136223846793005ULL+1442695040888963407ULL)
 for(int d=days;d>=1;d--){
  Id day_start=(now_sec()+28800)/86400*86400-28800-(Id)d*86400; /* 北京当天 0 点 */
  for(int hour=0;hour<8;hour++){
   static const int hours[8]={8,9,10,11,14,15,16,17};
   Id start=day_start+(Id)hours[hour]*3600;
   Id lab=1+(Id)(hour%3); /* 种子只建未来场次，历史场次需自行插入：三个实验室轮换 */
   db_run(db,"INSERT OR IGNORE INTO slots(lab_id,start_at,end_at,enabled,capacity,reminded_at) VALUES(?,?,?,1,2,NULL)","iii",lab,start,start+3600);
   Id slot=db_num(db,"SELECT id FROM slots WHERE start_at=?","i",start);
   if(!slot)continue;
   Id capacity=db_num(db,"SELECT capacity FROM slots WHERE id=?","i",slot);
   /* 2..capacity 人预约 */
   int n=(int)(2+DEMO_NEXT()%((unsigned long long)capacity<3?1:(unsigned long long)capacity-2+1));
   if(n>(int)capacity)n=(int)capacity;
   for(int k=0;k<n;k++){
    Id uid=2+(Id)(DEMO_NEXT()%8); /* user01..user08 */
    if(db_num(db,"SELECT count(*) FROM reservations WHERE slot_id=? AND user_id=?","ii",slot,uid))continue;
    if((int)(DEMO_NEXT()%10)<2)continue; /* 20% 空缺 */
    if(db_num(db,"SELECT count(*) FROM reservations WHERE slot_id=? AND status='CONFIRMED'","i",slot)>=(Id)capacity)break; /* 结构性容量保护：重复生成/序列漂移时也不超容 */
    if(!db_run(db,"INSERT INTO reservations(user_id,slot_id,status,source,created_at) VALUES(?,?, 'CONFIRMED','DIRECT',?)","iii",uid,slot,start-86400))return 0;
    Id rid=sqlite3_last_insert_rowid(db->sql);
    unsigned long long roll=DEMO_NEXT()%100;
    if(roll<15){ /* 15% 爽约 */
     db_run(db,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='NO_SHOW' WHERE id=?","ii",start+3600,rid);
    }else if(roll<75){ /* 60% 签到 */
     db_run(db,"UPDATE reservations SET checked_in_at=? WHERE id=?","ii",start+600+ (Id)(DEMO_NEXT()%1800),rid);
    }else{ /* 25% 已取消 */
     db_run(db,"UPDATE reservations SET status='CANCELLED',cancelled_at=?,cancel_reason='USER' WHERE id=?","ii",start-3600,rid);
    }
   }
  }
 }
 #undef DEMO_NEXT
 return 1;
}
 int app_main(int argc,char **argv){
 Config c={"data/lab.db","web",8080,900,30,30,1,5,900,500,NULL,NULL,NULL,NULL,1800,21600,0,0,0,0,0};int seed=0,init=0,check=0,demo_days=0;
 for(int i=1;i<argc;i++){
  if(!strcmp(argv[i],"--seed"))seed=1;else if(!strcmp(argv[i],"--init-only"))init=1;else if(!strcmp(argv[i],"--check"))check=1;
  else if(!strcmp(argv[i],"--db")&&i+1<argc)c.db_path=argv[++i];
  else if(!strcmp(argv[i],"--web")&&i+1<argc)c.web_path=argv[++i];
  else if(!strcmp(argv[i],"--port")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n<1024||n>65535){fprintf(stderr,"Port must be 1024..65535\n");return 2;}c.port=(int)n;}
  else if(!strcmp(argv[i],"--checkin-window")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n<1||n>86400){fprintf(stderr,"Check-in window must be 1..86400 seconds\n");return 2;}c.checkin_window=(int)n;}
  else if(!strcmp(argv[i],"--sweep-interval")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n<1||n>3600){fprintf(stderr,"Sweep interval must be 1..3600 seconds\n");return 2;}c.sweep_interval=(int)n;}
  else if(!strcmp(argv[i],"--rate-burst")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n<1||n>100000){fprintf(stderr,"Rate burst must be 1..100000\n");return 2;}c.rate_burst=(int)n;}
  else if(!strcmp(argv[i],"--rate-refill-sec")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n<1||n>3600){fprintf(stderr,"Rate refill must be 1..3600 seconds\n");return 2;}c.rate_refill_sec=(int)n;}
  else if(!strcmp(argv[i],"--login-max-fails")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n<1||n>100){fprintf(stderr,"Login max fails must be 1..100\n");return 2;}c.login_max_fails=(int)n;}
  else if(!strcmp(argv[i],"--login-lockout")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n<1||n>86400){fprintf(stderr,"Login lockout must be 1..86400 seconds\n");return 2;}c.login_lockout=(int)n;}
  else if(!strcmp(argv[i],"--slow-ms")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n>100000){fprintf(stderr,"Slow threshold must be 0..100000 ms\n");return 2;}c.slow_ms=(int)n;}
  else if(!strcmp(argv[i],"--backup")&&i+1<argc)c.backup_dest=argv[++i];
  else if(!strcmp(argv[i],"--remind-sec")&&i+1<argc){const char *v=argv[++i];Id n=0;if(strcmp(v,"0")&&(!parse_id(v,&n)||n>86400)){fprintf(stderr,"Remind seconds must be 0..86400 (0 disables)\n");return 2;}c.remind_sec=(int)n;}
  else if(!strcmp(argv[i],"--backup-interval")&&i+1<argc){const char *v=argv[++i];Id n=0;if(strcmp(v,"0")&&(!parse_id(v,&n)||n>604800)){fprintf(stderr,"Backup interval must be 0..604800 seconds (0 disables)\n");return 2;}c.backup_interval=(int)n;}
  else if(!strcmp(argv[i],"--demo-days")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n>365){fprintf(stderr,"Demo days must be 1..365\n");return 2;}demo_days=(int)n;}
  else if(!strcmp(argv[i],"--quota-weekly")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n>100){fprintf(stderr,"Quota weekly must be 0..100\n");return 2;}c.quota_weekly=(int)n;}
  else if(!strcmp(argv[i],"--lead-time")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)||n>86400){fprintf(stderr,"Lead time must be 0..86400 seconds\n");return 2;}c.lead_time=(int)n;}
  else if(!strcmp(argv[i],"--restore")&&i+1<argc)c.restore_from=argv[++i];
  /* r24 新增：HELD 限时保留窗口（0=关闭，保持旧行为）、候补策略、可注入时钟 */
  else if(!strcmp(argv[i],"--hold-window")&&i+1<argc){const char *v=argv[++i];Id n=0;if(strcmp(v,"0")&&(!parse_id(v,&n)||n>86400)){fprintf(stderr,"Hold window must be 0..86400 seconds (0 disables)\n");return 2;}c.hold_window=(int)n;}
  else if(!strcmp(argv[i],"--waitlist-strategy")&&i+1<argc){const char *v=argv[++i];if(strcmp(v,"strict")&&strcmp(v,"executable")){fprintf(stderr,"Waitlist strategy must be strict or executable\n");return 2;}c.waitlist_strict=!strcmp(v,"strict");}
  else if(!strcmp(argv[i],"--fake-now")&&i+1<argc){Id n=0;if(!parse_id(argv[++i],&n)){fprintf(stderr,"Fake now must be a positive epoch seconds value\n");return 2;}c.fake_now=(long long)n;}
#ifdef TEST_FAULTS
  else if(!strcmp(argv[i],"--fault")&&i+1<argc)c.fault=argv[++i];
  else if(!strcmp(argv[i],"--fault-request")&&i+1<argc)c.fault_request=argv[++i];
#endif
  else {fprintf(stderr,"Usage: lab-booking --db FILE --web DIR --port PORT [--checkin-window SEC] [--sweep-interval SEC] [--rate-burst N] [--rate-refill-sec SEC] [--login-max-fails N] [--login-lockout SEC] [--slow-ms MS] [--backup DEST] [--remind-sec SEC] [--backup-interval SEC] [--demo-days N] [--seed --init-only] [--check]\n");return 2;}
 }
 int sweep_mid=c.fault&&!strcmp(c.fault,"sweep-mid");
 if(sweep_mid){/* 扫描故障注入无需请求编号 */}
 else if((c.fault||c.fault_request)&&(!c.fault||!c.fault_request||!uuid_valid(c.fault_request)||(strcmp(c.fault,"cancel-before-promote")&&strcmp(c.fault,"after-commit"))))return 2;
 if(sodium_init()<0){fprintf(stderr,"Crypto initialization failed\n");return 1;}
 clock_configure(&c);
 _mkdir("data");DB db={0};
 if(!db_open(&db,c.db_path)){fprintf(stderr,"Cannot open database\n");db_close(&db);return 1;}
 if(db_num(&db,"PRAGMA user_version","")>5){fprintf(stderr,"Unsupported schema version\n");db_close(&db);return 1;} /* r24：支持到 v5 */
 if(!db_init(&db)||(seed&&!db_seed(&db,getenv("LAB_SEED_PASSWORD")))||!db_check(&db)){fprintf(stderr,"Database initialization/integrity check failed (code %d).\n",db.error);db_close(&db);return 1;}
 if(demo_days>0){if(!gen_demo(&db,demo_days,getenv("LAB_SEED_PASSWORD"))){fprintf(stderr,"Demo data generation failed\n");db_close(&db);return 1;}printf("Demo history generated for past %d days.\n",demo_days);}
 if(!init&&!check&&!db_run(&db,"DELETE FROM sessions WHERE expires_at<?","i",now_sec())){fprintf(stderr,"Session cleanup failed (code %d).\n",db.error);db_close(&db);return 1;}
 if(c.restore_from){ /* r17 恢复：备份文件灌回主库并验证完整性 */
  DB src={0};
  if(!db_open(&src,c.restore_from)){fprintf(stderr,"Cannot open backup file: %s\n",c.restore_from);db_close(&db);return 1;}
  db_close(&src);
  int ok=db_backup(c.restore_from,c.db_path);
  if(ok){DB v={0};if(db_open(&v,c.db_path)){cJSON *ic=db_first(&v,"PRAGMA integrity_check","");ok=ic&&jstr(ic,"integrity_check")&&!strcmp(jstr(ic,"integrity_check"),"ok");printf("Restore integrity_check=%s\n",ic&&jstr(ic,"integrity_check")?jstr(ic,"integrity_check"):"?");cJSON_Delete(ic);db_close(&v);}else ok=0;}
  printf(ok?"Restored %s from %s\n":"Restore failed\n",c.db_path,c.restore_from);
  db_close(&db);return ok?0:1;
 }
 if(c.backup_dest){int ok=db_backup(c.db_path,c.backup_dest);db_close(&db);printf(ok?"Backup written: %s\n":"Backup failed\n",c.backup_dest);return ok?0:1;}
 db_close(&db);if(init||check){puts("Database ready; integrity checks passed.");return 0;}return serve(&c);
}
int wmain(int argc,wchar_t **wide){
 char **args=calloc((size_t)argc+1,sizeof *args);if(!args)return 1;
 int rc=1;
 for(int i=0;i<argc;i++){
  int n=WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,wide[i],-1,NULL,0,NULL,NULL);
  if(!n)goto cleanup;
  args[i]=malloc((size_t)n);if(!args[i])goto cleanup;
  if(!WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,wide[i],-1,args[i],n,NULL,NULL))goto cleanup;
 }
 rc=app_main(argc,args);
cleanup:
 for(int i=0;i<argc;i++)free(args[i]);
 free(args);return rc;
}
