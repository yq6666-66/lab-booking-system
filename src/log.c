/* r6/L3 结构化分级日志：DEBUG/INFO/WARN/ERROR，按大小轮转（app.log→.1→.2），SRWLOCK 线程安全 */
#include "app.h"
#include <windows.h>
#include <direct.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
static SRWLOCK lg_lock=SRWLOCK_INIT;
static FILE *lg_file;static char lg_path[300];static long lg_max=5*1024*1024;
static const char *lg_name(int lv){return lv>=3?"ERROR":lv==2?"WARN":lv==1?"INFO":"DEBUG";}
int log_init(const char *path,long max_bytes){
 if(max_bytes>0)lg_max=max_bytes;
 char dir[300];snprintf(dir,sizeof dir,"%s",path);
 char *cut=strrchr(dir,'/'),*bs=strrchr(dir,'\\');char *last=cut>bs?cut:bs;
 if(last){*last=0;_mkdir(dir);}
 AcquireSRWLockExclusive(&lg_lock);
 lg_file=fopen(path,"ab");
 if(lg_file)snprintf(lg_path,sizeof lg_path,"%s",path);
 ReleaseSRWLockExclusive(&lg_lock);
 return lg_file!=NULL;
}
void log_write(int level,const char *fmt,...){
 if(!lg_file||level<0||level>3)return;
 AcquireSRWLockExclusive(&lg_lock);
 long size=ftell(lg_file);
 if(size>lg_max){ /* 轮转：app.log→app.log.1→app.log.2（最旧丢弃） */
  fclose(lg_file);lg_file=NULL;
  char a[340],b[340];snprintf(a,sizeof a,"%s.1",lg_path);snprintf(b,sizeof b,"%s.2",lg_path);
  DeleteFileA(b);MoveFileA(a,b);MoveFileA(lg_path,a);
  lg_file=fopen(lg_path,"ab");
 }
 if(lg_file){
  time_t t=time(NULL)+28800;struct tm g;gmtime_s(&g,&t);
  fprintf(lg_file,"%04d-%02d-%02dT%02d:%02d:%02d+08:00 [%s] ",g.tm_year+1900,g.tm_mon+1,g.tm_mday,g.tm_hour,g.tm_min,g.tm_sec,lg_name(level));
  va_list a2;va_start(a2,fmt);vfprintf(lg_file,fmt,a2);va_end(a2);
  fputc('\n',lg_file);fflush(lg_file);
 }
 ReleaseSRWLockExclusive(&lg_lock);
}
