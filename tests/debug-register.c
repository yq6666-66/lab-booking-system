/* 独立复现 do_register：不经 HTTP，直接调用（http.c 被本文件包含以访问静态函数） */
#include "http.c"
int main(void){
 if(sodium_init()<0)return 1;
 remove("debug-reg.db");remove("debug-reg.db-wal");remove("debug-reg.db-shm");
 DB *d=db_thread_get("debug-reg.db");
 if(!d||!db_init(d)){printf("init fail: %s\n",d&&d->sql?sqlite3_errmsg(d->sql):"?");return 1;}
 printf("init ok\n");fflush(stdout);
 char cookie[256];
 cJSON *body=cJSON_Parse("{\"username\":\"stu2027\",\"password\":\"StuPass2026\"}");
 printf("body parsed\n");fflush(stdout);
 Result r=do_register(d,body,cookie);
 printf("status=%d sqlite_err=%s\n",r.status,d&&d->sql?sqlite3_errmsg(d->sql):"?");
 char *s=cJSON_PrintUnformatted(r.body);printf("body=%s\n",s?s:"NULL");
 if(s)cJSON_free(s);cJSON_Delete(r.body);
 return 0;
}
