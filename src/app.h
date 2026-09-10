#ifndef LAB_APP_H
#define LAB_APP_H
#include <stdint.h>
#include <stddef.h>
#include "sqlite3.h"
#include "cJSON.h"
typedef sqlite3_int64 Id;
typedef struct { sqlite3 *sql; int error; } DB;
typedef struct { const char *db_path; const char *web_path; int port; const char *fault; const char *fault_request; } Config;
typedef struct { Id id; int admin; char username[65]; char csrf[65]; } User;
typedef struct { int status; cJSON *body; } Result;
int db_open(DB *db,const char *path);
void db_close(DB *db);
int db_run(DB *db,const char *sql,const char *fmt,...);
Id db_num(DB *db,const char *sql,const char *fmt,...);
cJSON *db_rows(DB *db,const char *sql,const char *fmt,...);
cJSON *db_first(DB *db,const char *sql,const char *fmt,...);
int db_init(DB *db);
int db_check(DB *db);
int db_seed(DB *db,const char *password);
int publish_slots(DB *db,Id lab,Id start,Id end);
Id now_sec(void);
Id date_start(const char *date);
int parse_id(const char *s,Id *out);
int uuid_valid(const char *s);
const char *jstr(const cJSON *j,const char *key);
void jid(cJSON *j,const char *key,Id id);
void hash_text(const char *s,char out[65]);
void random_hex(char out[65]);
Result result(int status,const char *code,const char *message,cJSON *data);
Result db_failure(DB *db);
Result booking(DB *db,const Config *cfg,const User *u,const char *action,Id target,const char *request_id);
Result records(DB *db,const User *u,int all,Id date);
int serve(const Config *config);
#endif
