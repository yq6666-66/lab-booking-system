/* stats.c —— 统计/导出域（r24 规划 P2-5 自 service.c 拆分；函数体未改，仅移动） */
#include "service.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
Result stats(DB *d,Id start,Id end){
 cJSON *days=db_rows(d,"SELECT (s.start_at+28800)/86400 AS day,count(DISTINCT s.id) AS slots,count(DISTINCT CASE WHEN r.status IN('CONFIRMED','HELD') THEN r.id END) AS confirmed,count(DISTINCT CASE WHEN r.status='CANCELLED' AND (r.cancel_reason IS NULL OR r.cancel_reason='USER') THEN r.id END) AS cancelled,count(DISTINCT CASE WHEN r.cancel_reason='NO_SHOW' THEN r.id END) AS no_show,count(DISTINCT CASE WHEN r.checked_in_at IS NOT NULL THEN r.id END) AS checked_in FROM slots s LEFT JOIN reservations r ON r.slot_id=s.id WHERE s.start_at>=? AND s.start_at<? GROUP BY day ORDER BY day","ii",start,end+86400);
 if(d->error)return db_failure(d);
 cJSON *waiting=db_rows(d,"SELECT (s.start_at+28800)/86400 AS day,count(*) AS waiting FROM waitlist w JOIN slots s ON s.id=w.slot_id JOIN users u ON u.id=w.user_id WHERE s.start_at>=? AND s.start_at<? AND w.status='WAITING' AND u.enabled=1 GROUP BY day ORDER BY day","ii",start,end+86400);
 if(d->error){cJSON_Delete(days);return db_failure(d);}
 cJSON *rows=cJSON_CreateArray(),*totals=cJSON_CreateObject();
 if(rows&&totals){
  double ts=0,tc=0,tx=0,tn=0,ti=0,tw=0;cJSON *it;
  cJSON_ArrayForEach(it,days){
   cJSON *row=cJSON_CreateObject();if(!row)break;
   char date[11];date_text((Id)cJSON_GetObjectItemCaseSensitive(it,"day")->valuedouble,date);
   double w=0;cJSON *jt;cJSON_ArrayForEach(jt,waiting)if((Id)cJSON_GetObjectItemCaseSensitive(jt,"day")->valuedouble==(Id)cJSON_GetObjectItemCaseSensitive(it,"day")->valuedouble){w=cJSON_GetObjectItemCaseSensitive(jt,"waiting")->valuedouble;break;}
   double s=cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble,c=cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble,x=cJSON_GetObjectItemCaseSensitive(it,"cancelled")->valuedouble,ns=cJSON_GetObjectItemCaseSensitive(it,"no_show")->valuedouble,ci=cJSON_GetObjectItemCaseSensitive(it,"checked_in")->valuedouble;
   cJSON_AddStringToObject(row,"date",date);cJSON_AddNumberToObject(row,"slots",s);cJSON_AddNumberToObject(row,"confirmed",c);cJSON_AddNumberToObject(row,"cancelled",x);cJSON_AddNumberToObject(row,"no_show",ns);cJSON_AddNumberToObject(row,"checked_in",ci);cJSON_AddNumberToObject(row,"waiting",w);
   cJSON_AddItemToArray(rows,row);ts+=s;tc+=c;tx+=x;tn+=ns;ti+=ci;tw+=w;
  }
  cJSON_AddNumberToObject(totals,"slots",ts);cJSON_AddNumberToObject(totals,"confirmed",tc);cJSON_AddNumberToObject(totals,"cancelled",tx);cJSON_AddNumberToObject(totals,"no_show",tn);cJSON_AddNumberToObject(totals,"checked_in",ti);cJSON_AddNumberToObject(totals,"waiting",tw);
 }
 cJSON_Delete(days);cJSON_Delete(waiting);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"stats",rows);cJSON_AddItemToObject(j,"totals",totals);
 return result(200,"OK","查询成功",j);
}
Result stats_export(DB *d,Id start,Id end){
 Result r=stats(d,start,end);if(r.status!=200)return r;
 cJSON *data=cJSON_GetObjectItemCaseSensitive(r.body,"data"),*rows=cJSON_GetObjectItemCaseSensitive(data,"stats"),*totals=cJSON_GetObjectItemCaseSensitive(data,"totals");
 char a[11],b[11];date_text((start+28800)/86400,a);date_text((end+28800)/86400,b);
 size_t cap=4096+(size_t)(rows?cJSON_GetArraySize(rows):0)*160;char *csv=malloc(cap);
 if(!csv){cJSON_Delete(r.body);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(csv,cap,"\xEF\xBB\xBF" "日期,开放场次,有效预约,已取消,已爽约,已签到,候补人数\n");
 if(n<0||(size_t)n>=cap)n=0;
 cJSON *it;cJSON_ArrayForEach(it,rows){
  if((size_t)n+160>=cap)break;
  const char *day=jstr(it,"date");
  n+=snprintf(csv+n,cap-(size_t)n,"%s,%g,%g,%g,%g,%g,%g\n",day?day:"",
   cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble,
   cJSON_GetObjectItemCaseSensitive(it,"cancelled")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"no_show")->valuedouble,
   cJSON_GetObjectItemCaseSensitive(it,"checked_in")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"waiting")->valuedouble);
 }
 if(totals&&(size_t)n+160<cap)n+=snprintf(csv+n,cap-(size_t)n,"合计,%g,%g,%g,%g,%g,%g\n",
  cJSON_GetObjectItemCaseSensitive(totals,"slots")->valuedouble,cJSON_GetObjectItemCaseSensitive(totals,"confirmed")->valuedouble,
  cJSON_GetObjectItemCaseSensitive(totals,"cancelled")->valuedouble,cJSON_GetObjectItemCaseSensitive(totals,"no_show")->valuedouble,
  cJSON_GetObjectItemCaseSensitive(totals,"checked_in")->valuedouble,cJSON_GetObjectItemCaseSensitive(totals,"waiting")->valuedouble);
 cJSON_Delete(r.body);
 char name[64];snprintf(name,sizeof name,"lab-stats-%s_%s.csv",a,b);
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"filename",name);cJSON_AddStringToObject(j,"content",csv);free(csv);
 return result(200,"OK","导出完成",j);
}
/* -- r46 用户通知 CSV 导出：全部通知按时间倒序，带 BOM 兼容 Excel。
   与既有 stats_export 对齐的 {filename, content} 封套。 -- */
Result notifications_export(DB *d,const User *u){
 cJSON *rows=db_rows(d,
  "SELECT n.kind,n.title,n.body,n.read_at,n.created_at,n.slot_id,n.reservation_id "
  "FROM notifications n WHERE n.user_id=? ORDER BY n.id DESC LIMIT 500","i",u->id);
 if(d->error)return db_failure(d);
 int count=rows?cJSON_GetArraySize(rows):0;
 size_t cap=256+(size_t)count*512;char *csv=malloc(cap);
 if(!csv){cJSON_Delete(rows);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(csv,cap,"\xEF\xBB\xBF" "时间,类型,标题,内容,已读时间,场次编号,预约编号\n");
 cJSON *it;cJSON_ArrayForEach(it,rows){
  if((size_t)n+480>=cap)break;
  cJSON *ct=cJSON_GetObjectItemCaseSensitive(it,"created_at"),*ra=cJSON_GetObjectItemCaseSensitive(it,"read_at");
  cJSON *si=cJSON_GetObjectItemCaseSensitive(it,"slot_id"),*ri=cJSON_GetObjectItemCaseSensitive(it,"reservation_id");
  Id created=(ct&&cJSON_IsNumber(ct))?(Id)ct->valuedouble:0;
  Id read=(ra&&cJSON_IsNumber(ra))?(Id)ra->valuedouble:0;
  char cs[24]={0},rs[24]={0};
  if(created){Id bj=created+28800;date_text(bj/86400,cs);int len=(int)strlen(cs);snprintf(cs+len,sizeof(cs)-len," %02d:%02d",(int)((bj%86400)/3600),(int)((bj%3600)/60));}
  if(read){Id bj=read+28800;date_text(bj/86400,rs);int len=(int)strlen(rs);snprintf(rs+len,sizeof(rs)-len," %02d:%02d",(int)((bj%86400)/3600),(int)((bj%3600)/60));}
  /* CSV 转义：内容含逗号/引号/换行时用双引号包裹并重复内部引号 */
  const char *title=jstr(it,"title"),*body=jstr(it,"body");
  char et[256]={0},eb[512]={0};
  {int k=0;for(const char*p=title;*p&&k<250;p++)et[k++]=(*p=='"')?'\"':*p;et[k]=0;}
  {int k=0;for(const char*p=body;*p&&k<505;p++)eb[k++]=(*p=='"')?'\"':*p;eb[k]=0;}
  const char *kind=jstr(it,"kind");
  n+=snprintf(csv+n,cap-(size_t)n,"%s,%s,\"%s\",\"%s\",%s,%lld,%lld\n",
   cs,kind?kind:"",et,eb,rs[0]?rs:"未读",
   (si&&cJSON_IsNumber(si))?(long long)si->valuedouble:0,
   (ri&&cJSON_IsNumber(ri))?(long long)ri->valuedouble:0);
 }
 cJSON_Delete(rows);
 char name[48];snprintf(name,sizeof name,"notifications-%lld.csv",(long long)u->id);
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"filename",name);cJSON_AddStringToObject(j,"content",csv);free(csv);
 return result(200,"OK","导出完成",j);
}
/* 资源利用率：按实验室聚合区间内的开放场次/席位与预约、签到、爽约，利用率=有效预约/总席位。 */
Result lab_utilization(DB *d,Id start,Id end){
 cJSON *rows=db_rows(d,
  "SELECT l.id AS lab_id,l.name AS lab_name,count(s.id) AS slots,CAST(total(s.capacity) AS INTEGER) AS seats,"
  "(SELECT count(*) FROM reservations r JOIN slots s2 ON s2.id=r.slot_id WHERE s2.lab_id=l.id AND r.status IN('CONFIRMED','HELD') AND s2.start_at>=? AND s2.start_at<?) AS confirmed,"
  "(SELECT count(*) FROM reservations r JOIN slots s3 ON s3.id=r.slot_id WHERE s3.lab_id=l.id AND r.checked_in_at IS NOT NULL AND s3.start_at>=? AND s3.start_at<?) AS checked_in,"
  "(SELECT count(*) FROM reservations r JOIN slots s4 ON s4.id=r.slot_id WHERE s4.lab_id=l.id AND r.cancel_reason='NO_SHOW' AND s4.start_at>=? AND s4.start_at<?) AS no_show,"
  "(SELECT CAST(total(r.checked_out_at-r.checked_in_at)/60 AS INTEGER) FROM reservations r JOIN slots s5 ON s5.id=r.slot_id WHERE s5.lab_id=l.id AND r.checked_out_at IS NOT NULL AND r.checked_in_at IS NOT NULL AND s5.start_at>=? AND s5.start_at<?) AS actual_minutes "
  "FROM labs l LEFT JOIN slots s ON s.lab_id=l.id AND s.start_at>=? AND s.start_at<? GROUP BY l.id ORDER BY l.id",
  "iiiiiiiiii",start,end+86400,start,end+86400,start,end+86400,start,end+86400,start,end+86400);
 if(!rows)return db_failure(d);
 cJSON *out=cJSON_CreateArray();cJSON *it;
 cJSON_ArrayForEach(it,rows){
  cJSON *row=cJSON_CreateObject();if(!row)break;
  double seats=cJSON_GetObjectItemCaseSensitive(it,"seats")->valuedouble;
  double confirmed=cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble;
  double util=seats>0?confirmed/seats*100.0:0.0;
  cJSON *lid=cJSON_GetObjectItemCaseSensitive(it,"lab_id");
  if(lid&&lid->valuestring)cJSON_AddStringToObject(row,"lab_id",lid->valuestring);
  else if(lid)cJSON_AddNumberToObject(row,"lab_id",lid->valuedouble);
  cJSON_AddStringToObject(row,"lab_name",jstr(it,"lab_name"));
  cJSON_AddNumberToObject(row,"slots",cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble);
  cJSON_AddNumberToObject(row,"seats",seats);
  cJSON_AddNumberToObject(row,"confirmed",confirmed);
  cJSON_AddNumberToObject(row,"checked_in",cJSON_GetObjectItemCaseSensitive(it,"checked_in")->valuedouble);
  cJSON_AddNumberToObject(row,"no_show",cJSON_GetObjectItemCaseSensitive(it,"no_show")->valuedouble);
  cJSON_AddNumberToObject(row,"utilization",((int)(util*10+0.5))/10.0);
  {double actual=cJSON_GetObjectItemCaseSensitive(it,"actual_minutes")->valuedouble;
   double seat_min=seats*60.0;
   cJSON_AddNumberToObject(row,"actual_minutes",(int)actual);
   cJSON_AddNumberToObject(row,"seat_minutes",(int)seat_min);
   cJSON_AddNumberToObject(row,"utilization_actual",seat_min>0?((int)(actual/seat_min*1000.0+0.5))/10.0:0.0);}
  cJSON_AddItemToArray(out,row);
 }
 cJSON_Delete(rows);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"utilization",out);
 return result(200,"OK","查询成功",j);
}
/* r21 资源声明占用查询：区间内各资源的声明次数与最近声明记录（管理端资源页签展示）。 */
Result asset_claim_report(DB *d,Id start,Id end){
 cJSON *rows=db_rows(d,
  "SELECT a.id AS asset_id,a.name AS asset_name,a.total,count(c.reservation_id) AS claims,"
  "MAX(s.start_at) AS last_start "
  "FROM assets a LEFT JOIN asset_claims c ON c.asset_id=a.id "
  "LEFT JOIN reservations r ON r.id=c.reservation_id AND r.status IN('CONFIRMED','HELD') "
  "LEFT JOIN slots s ON s.id=r.slot_id AND s.start_at>=? AND s.start_at<? "
  "GROUP BY a.id ORDER BY a.id","ii",start,end+86400);
 if(!rows)return db_failure(d);
 cJSON *out=cJSON_CreateArray();cJSON *it;
 cJSON_ArrayForEach(it,rows){
  cJSON *row=cJSON_CreateObject();if(!row)break;
  cJSON *aid=cJSON_GetObjectItemCaseSensitive(it,"asset_id");
  if(aid&&aid->valuestring)cJSON_AddStringToObject(row,"asset_id",aid->valuestring);
  else cJSON_AddNumberToObject(row,"asset_id",aid?aid->valuedouble:0);
  cJSON_AddStringToObject(row,"asset_name",jstr(it,"asset_name"));
  cJSON_AddNumberToObject(row,"claims",cJSON_GetObjectItemCaseSensitive(it,"claims")->valuedouble);
  cJSON_AddNumberToObject(row,"last_start",cJSON_GetObjectItemCaseSensitive(it,"last_start")?cJSON_GetObjectItemCaseSensitive(it,"last_start")->valuedouble:0);
  cJSON_AddItemToArray(out,row);
 }
 cJSON_Delete(rows);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"claims",out);
 return result(200,"OK","查询成功",j);
}
/* r22 声明占用报表 CSV 导出：复用 asset_claim_report 的结果，输出带 BOM 的 CSV（对齐 stats_export / utilization_export）。
   仅导出报表已有字段，避免为导出而改动既有响应契约。 */
Result asset_claim_export(DB *d,Id start,Id end){
 Result r=asset_claim_report(d,start,end);if(r.status!=200)return r;
 cJSON *data=cJSON_GetObjectItemCaseSensitive(r.body,"data"),*rows=cJSON_GetObjectItemCaseSensitive(data,"claims");
 char a[11],b[11];date_text((start+28800)/86400,a);date_text((end+28800)/86400,b);
 size_t cap=4096+(size_t)(rows?cJSON_GetArraySize(rows):0)*160;char *csv=malloc(cap);
 if(!csv){cJSON_Delete(r.body);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(csv,cap,"\xEF\xBB\xBF" "资源编号,资源名称,声明次数,最近占用日期\n");
 if(n<0||(size_t)n>=cap)n=0;
 Id total=0;cJSON *it;
 cJSON_ArrayForEach(it,rows){
  if((size_t)n+160>=cap)break;
  cJSON *aid=cJSON_GetObjectItemCaseSensitive(it,"asset_id");
  const char *idtext=(aid&&aid->valuestring)?aid->valuestring:"";
  const char *nm=jstr(it,"asset_name");
  double claims=cJSON_GetObjectItemCaseSensitive(it,"claims")?cJSON_GetObjectItemCaseSensitive(it,"claims")->valuedouble:0;
  cJSON *ls=cJSON_GetObjectItemCaseSensitive(it,"last_start");
  char ld[11]="";if(ls&&ls->valuedouble>0)date_text(((Id)ls->valuedouble+28800)/86400,ld);
  n+=snprintf(csv+n,cap-(size_t)n,"%s,%s,%g,%s\n",idtext,nm?nm:"",claims,ld);
  total+=(Id)claims;
 }
 if(rows&&(size_t)n+64<cap)n+=snprintf(csv+n,cap-(size_t)n,"合计,,%lld,\n",(long long)total);
 cJSON_Delete(r.body);
 char name[72];snprintf(name,sizeof name,"lab-asset-claims-%s_%s.csv",a,b);
 cJSON *j2=cJSON_CreateObject();cJSON_AddStringToObject(j2,"filename",name);cJSON_AddStringToObject(j2,"content",csv);free(csv);
 return result(200,"OK","导出完成",j2);
}
Result calendar_export(DB *d,const User *u){
 cJSON *rows=db_rows(d,"SELECT r.id,r.checked_in_at,s.start_at,s.end_at,l.name AS lab FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id WHERE r.user_id=? AND r.status IN('CONFIRMED','HELD') ORDER BY s.start_at","i",u->id);
 if(!rows)return db_failure(d);
 size_t cap=1024+(size_t)(rows?cJSON_GetArraySize(rows):0)*400;char *cal=malloc(cap);
 if(!cal){cJSON_Delete(rows);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(cal,cap,"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//LabBooking//CN\r\n");
 cJSON *it;cJSON_ArrayForEach(it,rows){
  Id st=(Id)cJSON_GetObjectItemCaseSensitive(it,"start_at")->valuedouble;
  Id en=(Id)cJSON_GetObjectItemCaseSensitive(it,"end_at")->valuedouble;
  Id rid=(Id)cJSON_GetObjectItemCaseSensitive(it,"id")->valuedouble;
  cJSON *ci=cJSON_GetObjectItemCaseSensitive(it,"checked_in_at");
  const char *lab=jstr(it,"lab");
  char ds[24],de[24],tstat[32];
  time_t tu=(time_t)st;strftime(ds,sizeof ds,"%Y%m%dT%H%M%SZ",gmtime(&tu));
  tu=(time_t)en;strftime(de,sizeof de,"%Y%m%dT%H%M%SZ",gmtime(&tu));
  snprintf(tstat,sizeof tstat," · %s",(ci&&cJSON_IsNumber(ci)&&ci->valuedouble>0)?"已签到":"未签到");
  char sum[160];snprintf(sum,sizeof sum,"实验室预约：%s%s",lab?lab:"",tstat);
  if((size_t)n+460>=cap)break;
  n+=snprintf(cal+n,cap-(size_t)n,"BEGIN:VEVENT\r\nUID:lab-booking-%lld@lab-booking\r\nDTSTART:%s\r\nDTEND:%s\r\nSUMMARY:%s\r\nEND:VEVENT\r\n",(long long)rid,ds,de,sum);
 }
 cJSON_Delete(rows);
 n+=snprintf(cal+n,cap-(size_t)n,"END:VCALENDAR\r\n");
 char name[40];snprintf(name,sizeof name,"lab-schedule.ics");
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"filename",name);cJSON_AddStringToObject(j,"content",cal);free(cal);
 return result(200,"OK","导出完成",j);
}
/* r14 资源使用统计：区间内某资源的声明次数（按有效预约、按日聚合），用于资源维度闭环分析。 */
Result asset_usage(DB *d,Id aid,Id start,Id end){
 cJSON *a=db_first(d,"SELECT id,name,spec,total,status FROM assets WHERE id=?","i",aid);
 if(d->error)return db_failure(d);
 if(!a)return result(404,"NOT_FOUND","资源不存在",NULL);
 cJSON *days=db_rows(d,"SELECT (s.start_at+28800)/86400 AS day,count(*) AS claims FROM asset_claims c JOIN reservations r ON r.id=c.reservation_id AND r.status IN('CONFIRMED','HELD') JOIN slots s ON s.id=r.slot_id WHERE c.asset_id=? AND s.start_at>=? AND s.start_at<? GROUP BY day ORDER BY day","iii",aid,start,end+86400);
 if(!days){cJSON_Delete(a);return db_failure(d);}
 cJSON *out=cJSON_CreateArray();cJSON *it;
 cJSON_ArrayForEach(it,days){
  cJSON *row=cJSON_CreateObject();if(!row)break;
  char date[11];date_text((Id)cJSON_GetObjectItemCaseSensitive(it,"day")->valuedouble,date);
  cJSON_AddStringToObject(row,"date",date);
  cJSON_AddNumberToObject(row,"claims",cJSON_GetObjectItemCaseSensitive(it,"claims")->valuedouble);
  cJSON_AddItemToArray(out,row);
 }
 cJSON_Delete(days);
 cJSON *j=cJSON_CreateObject();cJSON_AddItemToObject(j,"asset",a);cJSON_AddItemToObject(j,"usage",out);
 return result(200,"OK","查询成功",j);
}
Result utilization_export(DB *d,Id start,Id end){ Result r=lab_utilization(d,start,end);if(r.status!=200)return r;
 cJSON *rows=cJSON_GetObjectItemCaseSensitive(cJSON_GetObjectItemCaseSensitive(r.body,"data"),"utilization");
 char a[11],b[11];date_text((start+28800)/86400,a);date_text((end+28800)/86400,b);
 size_t cap=2048+(size_t)(rows?cJSON_GetArraySize(rows):0)*160;char *csv=malloc(cap);
 if(!csv){cJSON_Delete(r.body);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(csv,cap,"\xEF\xBB\xBF" "实验室,开放场次,总席位,有效预约,已签到,已爽约,利用率%%\n");
 if(n<0||(size_t)n>=cap)n=0;
 cJSON *it;cJSON_ArrayForEach(it,rows){
  if((size_t)n+200>=cap)break;
  n+=snprintf(csv+n,cap-(size_t)n,"%s,%g,%g,%g,%g,%g,%g\n",jstr(it,"lab_name"),
   cJSON_GetObjectItemCaseSensitive(it,"slots")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"seats")->valuedouble,
   cJSON_GetObjectItemCaseSensitive(it,"confirmed")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"checked_in")->valuedouble,
   cJSON_GetObjectItemCaseSensitive(it,"no_show")->valuedouble,cJSON_GetObjectItemCaseSensitive(it,"utilization")->valuedouble);
 }
 cJSON_Delete(r.body);
 char name[64];snprintf(name,sizeof name,"lab-utilization-%s_%s.csv",a,b);
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"filename",name);cJSON_AddStringToObject(j,"content",csv);free(csv);
 return result(200,"OK","导出完成",j);
}
/* -- r23 日历导出（对标 LibreBooking ICS）：以 API 令牌做免登录访问，输出 ICS 文本。
      时间统一用 UTC（带 Z 后缀），客户端会按本地时区正确呈现。 -- */
static void ics_stamp(Id t,char out[32]){
 time_t tt=(time_t)t;struct tm g;
 #ifdef _WIN32
 gmtime_s(&g,&tt);
 #else
 gmtime_r(&tt,&g);
 #endif
 /* 取模限定各字段范围，既保证语义不变也让编译器的格式截断分析能通过 */
 snprintf(out,32,"%04d%02d%02dT%02d%02d%02dZ",
  (g.tm_year+1900)%10000,(g.tm_mon+1)%100,(g.tm_mday)%100,g.tm_hour%100,g.tm_min%100,g.tm_sec%100);
}
Result calendar_ics(DB *d,const User *u){
 cJSON *rows=db_rows(d,"SELECT r.id,r.slot_id,l.name AS lab_name,s.start_at,s.end_at,r.status FROM reservations r JOIN slots s ON s.id=r.slot_id JOIN labs l ON l.id=s.lab_id WHERE r.user_id=? AND r.status IN('CONFIRMED','PENDING') ORDER BY s.start_at LIMIT 500","i",u->id);
 if(d->error)return db_failure(d);
 size_t cap=2048+(size_t)(rows?cJSON_GetArraySize(rows):0)*420;char *ics=malloc(cap);
 if(!ics){cJSON_Delete(rows);return result(500,"INTERNAL_ERROR","内存不足",NULL);}
 int n=snprintf(ics,cap,"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//lab-booking//r23//CN\r\nCALSCALE:GREGORIAN\r\nMETHOD:PUBLISH\r\nX-WR-CALNAME:lab-booking\r\n");
 if(n<0||(size_t)n>=cap)n=0;
 char nows[32];ics_stamp(now_sec(),nows);
 cJSON *it;cJSON_ArrayForEach(it,rows){
  if((size_t)n+420>=cap)break;
  Id t0=(Id)cJSON_GetObjectItemCaseSensitive(it,"start_at")->valuedouble;
  Id t1=(Id)cJSON_GetObjectItemCaseSensitive(it,"end_at")->valuedouble;
  char s0[32],s1[32];ics_stamp(t0,s0);ics_stamp(t1,s1);
  const char *lab=jstr(it,"lab_name"),*stt=jstr(it,"status");
  n+=snprintf(ics+n,cap-(size_t)n,
   "BEGIN:VEVENT\r\nUID:lab-%s-%s@lab-booking\r\nDTSTAMP:%s\r\nDTSTART:%s\r\nDTEND:%s\r\nSUMMARY:%s (%s)\r\nEND:VEVENT\r\n",
   jstr(it,"id"),jstr(it,"slot_id"),nows,s0,s1,lab?lab:"lab",(!stt||!strcmp(stt,"PENDING"))?"PENDING":"CONFIRMED");
 }
 n+=snprintf(ics+n,cap-(size_t)n,"END:VCALENDAR\r\n");
 cJSON_Delete(rows);
 char name[72];snprintf(name,sizeof name,"lab-booking-%lld.ics",(long long)u->id);
 cJSON *j=cJSON_CreateObject();cJSON_AddStringToObject(j,"filename",name);cJSON_AddStringToObject(j,"content",ics);free(ics);
 return result(200,"OK","导出完成",j);
}
/* -- r45 管理台聚合概览：一次调用返回首屏所需的全部数据。
   5 条并行安全的 SQL：今日统计 / 待审批数 / 候补 top / 近 7 日趋势 / 最近操作日志。
   比前端串行调 5 个端点少 4 个 HTTP 往返。 -- */
Result admin_dashboard(DB *d){
 Id now=now_sec();
 Id day0=(now+28800)/86400*86400-28800; /* 北京今日 0 点 */
 /* 1. 今日统计 */
 cJSON *today=db_rows(d,
  "SELECT count(DISTINCT s.id) AS slots,"
  "count(DISTINCT CASE WHEN r.status IN('CONFIRMED','HELD') THEN r.id END) AS confirmed,"
  "count(DISTINCT CASE WHEN r.checked_in_at IS NOT NULL THEN r.id END) AS checked_in,"
  "(SELECT count(*) FROM waitlist w JOIN users u ON u.id=w.user_id WHERE w.status='WAITING' AND u.enabled=1) AS waiting_total "
  "FROM slots s LEFT JOIN reservations r ON r.slot_id=s.id WHERE s.start_at>=? AND s.start_at<?",
  "ii",day0,day0+86400);
 if(d->error)return db_failure(d);
 /* 2. 待审批数 */
 Id pending=db_num(d,"SELECT count(*) FROM reservations WHERE status='PENDING'","");
 if(d->error){cJSON_Delete(today);return db_failure(d);}
 /* 3. 候补 top5（场次+人数） */
 cJSON *top_wait=db_rows(d,
  "SELECT s.id AS slot_id,l.name AS lab_name,s.start_at,s.capacity,count(*) AS waiting "
  "FROM waitlist w JOIN slots s ON s.id=w.slot_id JOIN labs l ON l.id=s.lab_id JOIN users u ON u.id=w.user_id "
  "WHERE w.status='WAITING' AND u.enabled=1 AND s.start_at>? "
  "GROUP BY s.id ORDER BY waiting DESC LIMIT 5","i",now);
 if(d->error){cJSON_Delete(today);return db_failure(d);}
 /* 4. 近 7 日预约趋势 */
 cJSON *trend=db_rows(d,
  "SELECT (s.start_at+28800)/86400 AS day,"
  "count(DISTINCT CASE WHEN r.status IN('CONFIRMED','HELD') THEN r.id END) AS confirmed,"
  "count(DISTINCT CASE WHEN r.checked_in_at IS NOT NULL THEN r.id END) AS checked_in "
  "FROM slots s LEFT JOIN reservations r ON r.slot_id=s.id "
  "WHERE s.start_at>=? AND s.start_at<? GROUP BY day ORDER BY day",
  "ii",day0-6*86400,day0+86400);
 if(d->error){cJSON_Delete(today);cJSON_Delete(top_wait);return db_failure(d);}
 /* 5. 最近 10 条操作日志 */
 cJSON *recent=db_rows(d,
  "SELECT e.action,e.entity_id,e.created_at,u.username AS actor FROM operation_events e "
  "LEFT JOIN users u ON u.id=e.actor_id ORDER BY e.id DESC LIMIT 10","");
 if(d->error){cJSON_Delete(today);cJSON_Delete(top_wait);cJSON_Delete(trend);return db_failure(d);}
 /* 趋势日期格式化 */
 cJSON *it;cJSON_ArrayForEach(it,trend){
  cJSON *dj=cJSON_GetObjectItemCaseSensitive(it,"day");
  if(dj&&cJSON_IsNumber(dj)){char date[11];date_text((Id)dj->valuedouble,date);
   cJSON_DeleteItemFromObject(it,"day");cJSON_AddStringToObject(it,"date",date);}}
 /* 组装 */
 cJSON *j=cJSON_CreateObject();
 cJSON *tj=today&&today->child?cJSON_DetachItemFromArray(today,0):cJSON_CreateObject();
 cJSON_AddItemToObject(j,"today",tj);
 cJSON_AddNumberToObject(j,"pending_approvals",(double)pending);
 cJSON_AddItemToObject(j,"top_waitlists",top_wait);
 cJSON_AddItemToObject(j,"trend_7d",trend);
 cJSON_AddItemToObject(j,"recent_events",recent);
 cJSON_Delete(today);
 return result(200,"OK","查询成功",j);
}
