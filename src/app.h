#ifndef LAB_APP_H
#define LAB_APP_H
#include <stdint.h>
#include <stddef.h>
#include "sqlite3.h"
#include "cJSON.h"
typedef sqlite3_int64 Id;
typedef struct { sqlite3 *sql; int error; } DB;
typedef struct { const char *db_path; const char *web_path; int port; int checkin_window; int sweep_interval; int rate_burst; int rate_refill_sec; int login_max_fails; int login_lockout; const char *fault; const char *fault_request; } Config;
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
int publish_slots(DB *db,Id lab,Id start,Id end,Id capacity);
Id now_sec(void);
Id date_start(const char *date);
void date_text(Id day,char out[11]);
int parse_id(const char *s,Id *out);
int uuid_valid(const char *s);
int hex_token(const char *s,size_t len);
int page_arg(const char *s,int fallback,int min,int max);
const char *jstr(const cJSON *j,const char *key);
void jid(cJSON *j,const char *key,Id id);
void hash_text(const char *s,char out[65]);
void random_hex(char out[65]);
Result result(int status,const char *code,const char *message,cJSON *data);
Result db_failure(DB *db);
Result booking(DB *db,const Config *cfg,const User *u,const char *action,Id target,const char *request_id);
Result records(DB *db,const User *u,int all,Id date,int page,int size);
Result stats(DB *db,Id start,Id end);
Result stats_export(DB *db,Id start,Id end);
Result notifications(DB *db,const User *u,int unread,int page,int size);
Result notifications_read(DB *db,const User *u,const cJSON *body,const char *request_id);
Result sessions_list(DB *db,const User *u,const char *current_hash);
Result session_revoke(DB *db,const User *u,const char *token_hash,const char *request_id);
Result password_change(DB *db,const User *u,const char *old_password,const char *new_password,const char *current_hash,const char *request_id);
void notify(DB *db,Id user,const char *kind,const char *title,const char *body,Id slot,Id reservation);
int sweep_once(const Config *config);
void sweep_start(const Config *config);
int serve(const Config *config);
/* r5/security */
typedef struct { double tokens; Id last_ms; int started; } RateBucket;
typedef struct { int fails; Id locked_until; } LoginGuard;
int bucket_allow(RateBucket *bucket,Id now_ms,int burst,int refill_sec);
int login_allow(const LoginGuard *guard,Id now_ms);
void login_record_fail(LoginGuard *guard,Id now_ms,int max_fails,int lockout_sec);
void login_record_ok(LoginGuard *guard);
void rl_configure(const Config *config);
int rl_login_gate(const char *username);
void rl_login_fail(const char *username);
void rl_login_ok(const char *username);
int rl_consume(Id user_id);
#endif
