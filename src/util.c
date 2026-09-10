#include "app.h"
#include <sodium.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <time.h>
#include <ctype.h>
Id now_sec(void){ return (Id)time(NULL); }
int parse_id(const char *s,Id *out){
 if(!s||!*s||strlen(s)>18) return 0;
 for(const char *p=s;*p;p++) if(*p<'0'||*p>'9') return 0;
 errno=0; char *end; long long n=strtoll(s,&end,10);
 if(errno||*end||n<=0) return 0;
 *out=(Id)n; return 1;
}
int uuid_valid(const char *s){
 if(!s||strlen(s)!=36) return 0;
 for(int i=0;i<36;i++){ if(i==8||i==13||i==18||i==23){if(s[i]!='-')return 0;}else if(!isxdigit((unsigned char)s[i]))return 0; }
 return 1;
}
Id date_start(const char *s){
 if(!s||strlen(s)!=10||s[4]!='-'||s[7]!='-') return -1;
 for(int i=0;i<10;i++) if(i!=4&&i!=7&&(s[i]<'0'||s[i]>'9')) return -1;
 int y=(s[0]-'0')*1000+(s[1]-'0')*100+(s[2]-'0')*10+s[3]-'0';
 int m=(s[5]-'0')*10+s[6]-'0',d=(s[8]-'0')*10+s[9]-'0';
 if(y<1970||y>2100||m<1||m>12) return -1;
 int days[]={31,28,31,30,31,30,31,31,30,31,30,31};
 if(y%4==0&&(y%100!=0||y%400==0)) days[1]=29;
 if(d<1||d>days[m-1])return -1;
 Id n=d-1; for(int a=1970;a<y;a++)n+=365+(a%4==0&&(a%100!=0||a%400==0));
 for(int a=0;a<m-1;a++) n+=days[a];
 return n*86400-28800;
}
const char *jstr(const cJSON *j,const char *key){ cJSON *v=cJSON_GetObjectItemCaseSensitive(j,key);return cJSON_IsString(v)?v->valuestring:NULL; }
void jid(cJSON *j,const char *key,Id id){char buf[32];snprintf(buf,sizeof buf,"%lld",(long long)id);cJSON_AddStringToObject(j,key,buf);}
void hash_text(const char *s,char out[65]){unsigned char h[32];crypto_generichash(h,sizeof h,(const unsigned char*)s,strlen(s),NULL,0);sodium_bin2hex(out,65,h,sizeof h);}
void random_hex(char out[65]){unsigned char b[32];randombytes_buf(b,sizeof b);sodium_bin2hex(out,65,b,sizeof b);}
Result result(int status,const char *code,const char *message,cJSON *data){
 cJSON *b=cJSON_CreateObject();if(!b){cJSON_Delete(data);return (Result){500,NULL};}
 cJSON_AddStringToObject(b,"code",code);cJSON_AddStringToObject(b,"message",message);cJSON_AddItemToObject(b,"data",data?data:cJSON_CreateObject());return (Result){status,b};
}
Result db_failure(DB *d){int e=d->error&255;return result(e==SQLITE_BUSY||e==SQLITE_LOCKED?503:500,e==SQLITE_BUSY||e==SQLITE_LOCKED?"DATABASE_BUSY":"INTERNAL_ERROR",e==SQLITE_BUSY||e==SQLITE_LOCKED?"数据库忙碌，请使用原请求编号重试":"服务处理失败，请稍后重试",NULL);}
