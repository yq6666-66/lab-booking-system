#include "app.h"
#include <sodium.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <direct.h>
#include <windows.h>
 static int app_main(int argc,char **argv){
 Config c={"data/lab.db","web",8080,900,30,30,1,5,900,NULL,NULL};int seed=0,init=0,check=0;
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
#ifdef TEST_FAULTS
  else if(!strcmp(argv[i],"--fault")&&i+1<argc)c.fault=argv[++i];
  else if(!strcmp(argv[i],"--fault-request")&&i+1<argc)c.fault_request=argv[++i];
#endif
  else {fprintf(stderr,"Usage: lab-booking --db FILE --web DIR --port PORT [--checkin-window SEC] [--sweep-interval SEC] [--rate-burst N] [--rate-refill-sec SEC] [--login-max-fails N] [--login-lockout SEC] [--seed --init-only] [--check]\n");return 2;}
 }
 int sweep_mid=c.fault&&!strcmp(c.fault,"sweep-mid");
 if(sweep_mid){/* 扫描故障注入无需请求编号 */}
 else if((c.fault||c.fault_request)&&(!c.fault||!c.fault_request||!uuid_valid(c.fault_request)||(strcmp(c.fault,"cancel-before-promote")&&strcmp(c.fault,"after-commit"))))return 2;
 if(sodium_init()<0){fprintf(stderr,"Crypto initialization failed\n");return 1;}
 _mkdir("data");DB db={0};
 if(!db_open(&db,c.db_path)){fprintf(stderr,"Cannot open database\n");db_close(&db);return 1;}
 if(db_num(&db,"PRAGMA user_version","")>3){fprintf(stderr,"Unsupported schema version\n");db_close(&db);return 1;}
 if(!db_init(&db)||(seed&&!db_seed(&db,getenv("LAB_SEED_PASSWORD")))||!db_check(&db)){fprintf(stderr,"Database initialization/integrity check failed (code %d).\n",db.error);db_close(&db);return 1;}
 if(!init&&!check&&!db_run(&db,"DELETE FROM sessions WHERE expires_at<?","i",now_sec())){fprintf(stderr,"Session cleanup failed (code %d).\n",db.error);db_close(&db);return 1;}
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
