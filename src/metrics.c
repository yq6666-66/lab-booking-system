/* r5/metrics：进程内存态性能指标（无锁原子计数 + 延迟直方图），重启清零。 */
#include "app.h"
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#define METRIC_BUCKETS 12 /* 11 个边界桶 + 1 个溢出桶 */
static const double bucket_max[]={1.0,2.0,5.0,10.0,20.0,50.0,100.0,200.0,500.0,1000.0,2000.0};
static volatile LONG64 g_requests=0,g_ok=0,g_4xx=0,g_5xx=0,g_busy=0,g_logins=0;
static volatile LONG64 g_count=0,g_sum_us=0,g_max_us=0;
static volatile LONG64 g_buckets[METRIC_BUCKETS];
void metrics_init(void){ /* 静态零初始化已满足，本函数保留以便将来显式重置 */ }
void metrics_inc_login(void){InterlockedIncrement64(&g_logins);}
void metrics_record_request(int status,double elapsed_ms){
 InterlockedIncrement64(&g_requests);
 if(status>=200&&status<300)InterlockedIncrement64(&g_ok);
 else if(status>=400&&status<500)InterlockedIncrement64(&g_4xx);
 else if(status>=500)InterlockedIncrement64(&g_5xx);
 if(status==503)InterlockedIncrement64(&g_busy);
 LONG64 us=(LONG64)(elapsed_ms*1000.0);
 InterlockedIncrement64(&g_count);
 InterlockedExchangeAdd64(&g_sum_us,us);
 LONG64 cur=g_max_us;while(us>cur){if(InterlockedCompareExchange64(&g_max_us,us,cur)==cur)break;cur=g_max_us;}
 int b=METRIC_BUCKETS-1;for(int i=0;i<METRIC_BUCKETS-1;i++)if(elapsed_ms<=bucket_max[i]){b=i;break;}
 InterlockedIncrement64(&g_buckets[b]);
}
/* r17 Prometheus 文本暴露格式（0.0.4）：计数器 + 延迟直方图（bucket 累积化）。返回 malloc 缓冲，调用方 free。 */
char *metrics_prometheus(void){
 size_t cap=4096;char *t=malloc(cap);if(!t)return NULL;
 int n=snprintf(t,cap,
  "# HELP lab_booking_requests_total Total HTTP requests.\n"
  "# TYPE lab_booking_requests_total counter\n"
  "lab_booking_requests_total %lld\n"
  "# TYPE lab_booking_responses_total counter\n"
  "lab_booking_responses_total{code=\"2xx\"} %lld\n"
  "lab_booking_responses_total{code=\"4xx\"} %lld\n"
  "lab_booking_responses_total{code=\"5xx\"} %lld\n"
  "lab_booking_responses_total{code=\"503_busy\"} %lld\n"
  "# HELP lab_booking_logins_total Successful logins.\n"
  "# TYPE lab_booking_logins_total counter\n"
  "lab_booking_logins_total %lld\n"
  "# HELP lab_booking_request_latency_ms Request latency in ms.\n"
  "# TYPE lab_booking_request_latency_ms histogram\n",
  (long long)g_requests,(long long)g_ok,(long long)g_4xx,(long long)g_5xx,(long long)g_busy,(long long)g_logins);
 LONG64 cum=0;
 for(int i=0;i<METRIC_BUCKETS;i++){
  cum+=g_buckets[i];
  char le[24];if(i<METRIC_BUCKETS-1)snprintf(le,sizeof le,"%g",bucket_max[i]);else snprintf(le,sizeof le,"+Inf");
  if((size_t)n+160>=(int)cap){cap*=2;t=realloc(t,(size_t)cap);if(!t)return NULL;}
  n+=snprintf(t+n,cap-(size_t)n,"lab_booking_request_latency_ms_bucket{le=\"%s\"} %lld\n",le,(long long)cum);
 }
 if((size_t)n+320>=(int)cap){cap*=2;t=realloc(t,(size_t)cap);if(!t)return NULL;}
 n+=snprintf(t+n,cap-(size_t)n,
  "lab_booking_request_latency_ms_sum %lld\n"
  "lab_booking_request_latency_ms_count %lld\n",
  (long long)(g_sum_us/1000),(long long)g_count);
 return t;
}
cJSON *metrics_snapshot(void){
 cJSON *root=cJSON_CreateObject(),*ctr=cJSON_CreateObject(),*lat=cJSON_CreateObject();
 if(!root||!ctr||!lat){cJSON_Delete(root);cJSON_Delete(ctr);cJSON_Delete(lat);return NULL;}
 cJSON_AddNumberToObject(ctr,"requests_total",(double)g_requests);
 cJSON_AddNumberToObject(ctr,"ok_2xx",(double)g_ok);
 cJSON_AddNumberToObject(ctr,"err_4xx",(double)g_4xx);
 cJSON_AddNumberToObject(ctr,"err_5xx",(double)g_5xx);
 cJSON_AddNumberToObject(ctr,"db_busy_503",(double)g_busy);
 cJSON_AddNumberToObject(ctr,"logins",(double)g_logins);
 cJSON_AddNumberToObject(lat,"count",(double)g_count);
 cJSON_AddNumberToObject(lat,"sum",(double)g_sum_us/1000.0);
 cJSON_AddNumberToObject(lat,"max",(double)g_max_us/1000.0);
 cJSON *bk=cJSON_CreateArray();for(int i=0;i<METRIC_BUCKETS;i++)cJSON_AddItemToArray(bk,cJSON_CreateNumber((double)g_buckets[i]));
 cJSON_AddItemToObject(lat,"buckets",bk);
 cJSON_AddItemToObject(root,"counters",ctr);
 cJSON_AddItemToObject(root,"latency_ms",lat);
 return root;
}
