/* service 模块内部共享头：业务常量与跨模块内部助手。
   拆分自单文件 service.c（r24 规划 P2-5）：仅调整文件组织，函数签名与调用契约不变；
   对外公开 API 仍以 app.h 为准，本头文件仅供 src/ 内 service 各域模块包含。 */
#ifndef LAB_SERVICE_H
#define LAB_SERVICE_H
#include "app.h"
/* -- r12 业务规则常量 -- */
#define NO_SHOW_GRACE 2      /* 窗口内第 2 次爽约起触发限制 */
#define PENALTY_DAYS 7       /* 限制时长：从触发爽约的场次时间起算 */
#define ARCHIVE_DAYS 30      /* 候补/请求回执保留天数（过期归档清理） */
#define CREDIT_BASE 5        /* r23 信用账户基准额度：预约消耗、签到返还、每周回补至此值 */
/* 跨模块内部助手（原单文件内 static，拆分后去 static 供 booking/asset/stats/token/service 共享） */
void event(DB *d,Id actor,const char *action,Id entity,const char *request);
void slot_when(DB *d,Id slot,char out[40]);
void fault(const Config *c,const char *stage,const char *key);
Result ok_id(const char *field,Id value);
Id promote_fill(DB *d,const Config *cfg,Id slot,Id actor,const char *key);
void credit_weekly_topup(DB *d,Id user);
int asset_window_ok(DB *d,Id asset,Id start_at);
#endif
