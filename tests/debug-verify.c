/* 验证 T30 运行库中存储的 stu2026 哈希是否匹配口令 */
#include <sodium.h>
#include <stdio.h>
#include <sqlite3.h>
int main(int argc,char **argv){
 if(sodium_init()<0)return 1;
 sqlite3 *db;if(sqlite3_open(argv[1],&db)!=SQLITE_OK)return 1;
 sqlite3_stmt *s;if(sqlite3_prepare_v2(db,"SELECT password_hash FROM users WHERE username='stu2026'",-1,&s,NULL)!=SQLITE_OK)return 1;
 if(sqlite3_step(s)!=SQLITE_ROW)return 1;
 const char *hash=(const char*)sqlite3_column_text(s,0);
 int ok=!crypto_pwhash_str_verify(hash,"StuPass2026",11);
 printf("hash=%s\nverify(StuPass2026)=%s\n",hash,ok?"MATCH":"MISMATCH");
 sqlite3_finalize(s);sqlite3_close(db);
 return ok?0:1;
}
