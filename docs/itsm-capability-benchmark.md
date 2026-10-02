# 开源企业服务台能力基准（带一手文档引用）

调研方法：GitHub REST API 取仓库元数据（2026-09-30），能力项一律取各项目**自己的文档站**
（admin/user/developer guide、wiki），不依赖 README。未能一手证实的条目标注 `unverified`。

配套文档：[platform-gap-analysis.md](platform-gap-analysis.md)（本仓库差距与路线图）、
[ai-service-desk-sota-report.md](ai-service-desk-sota-report.md)（AI 原生侧基准）。

---

## 1. 项目清单

| 项目 | 仓库 | 语言 | Stars | License | 维护状态 |
|---|---|---|---|---|---|
| Zammad | https://github.com/zammad/zammad | Ruby (+Vue/TS) | ~6.0k | AGPL-3.0 | 活跃 |
| GLPI | https://github.com/glpi-project/glpi | PHP | ~6.4k | GPL-3.0 | 活跃 |
| Znuny（OTRS fork） | https://github.com/znuny/Znuny | Perl | ~600 | GPL-3.0 | 活跃 |
| osTicket | https://github.com/osTicket/osTicket | PHP | ~3.9k | GPL-2.0 | 活跃但慢 |
| FreeScout | https://github.com/freescout-help-desk/freescout | PHP | ~4.6k | AGPL-3.0 + 付费模块 | 活跃 |
| Chatwoot | https://github.com/chatwoot/chatwoot | Ruby | ~37.4k | CE + 同仓专有 EE | 活跃 |
| iTop | https://github.com/Combodo/iTop | PHP | ~1.2k | AGPL-3.0 | 活跃（多为付费扩展） |
| UVdesk | https://github.com/uvdesk/community-skeleton | PHP | ~19.6k* | OSL-3.0 | 低速 |
| Peppermint | https://github.com/Peppermint-Lab/peppermint | TypeScript | ~3.2k | unverified | **已归档** |
| Helpy | https://github.com/helpyio/helpy | Ruby | ~2.5k | MIT | 事实停更（2023-03） |

\* UVdesk 的 star 落在 skeleton 仓库上，与其它项目不可比；引擎模块（core-framework 等）仅数十星。
Peppermint 已归档、Helpy 停更，均不作为参考实现。

**OTRS → Znuny**：OTRS AG 于 2020 年停掉 Community Edition，Edenhofer 的 Znuny GmbH 以 GPLv3 分叉为
Znuny — [Znuny About](https://doc.znuny.org/znuny_lts/about/)、[Why we forked](https://www.znuny.com/en/blog/why)。

---

## 2. 能力基准（按领域，附一手文档）

### 2.1 SLA 与服务日历

- SLA 通常有**三个独立计时器**：首次响应 / 更新 / 解决（Zammad）— [SLAs](https://admin-docs.zammad.org/en/latest/manage/slas.html)
- **日历是一等对象**：工作日多时段、时区、节假日（国家源或 iCalendar 订阅），SLA 引用日历后只计工作时段；一个实例可有多套日历按客户区分 — [Calendars](https://admin-docs.zammad.org/en/latest/manage/calendars.html)
- **SLA/OLA 分离 + TTO/TTR**（GLPI）— [Service levels](https://help.glpi-project.org/documentation/modules/configuration/service_levels.md)
- **暂停语义**：GLPI Pending 让 SLA "sleep" 并把到期时间顺延；Zammad 用内置 "SLA ignored" 状态标志冻结 — 同上 + [Zammad SLAs](https://admin-docs.zammad.org/en/latest/manage/slas.html)
- **升级**：按剩余时间**百分比**预警、`Escalation Before` 事件、每 5 分钟 cron `EscalationCheck`，且**只在工作时段触发** — [Ticket Escalation](https://doc.znuny.org/znuny_lts/generalinformation/ticketescalation/index.html)
- GLPI 升级级别可带条件（如仅当 Status=New）并执行改派/提优先级 — [Service levels](https://help.glpi-project.org/documentation/modules/configuration/service_levels.md)
- **失败模式（务必避免）**：Znuny "若未设置日历，升级计算不套用任何日历，等效 24×7×365" — [Service levels](https://doc.znuny.org/znuny_lts/admin/servicemanagement/servicelevels/index.html)
- osTicket 的极简模型：单一 Grace Period + Transient + 逾期告警开关 — [SLA Plans](https://docs.osticket.com/en/latest/Admin/Manage/SLA%20Plans.html)
- Chatwoot：FRT/NRT/RT 指标，SLA 一旦套用**不可修改或移除** — [SLA](https://www.chatwoot.com/hc/user-guide/articles/1713167310-service-level-agreemtns)

### 2.2 队列、组与分派

- Znuny：队列 = 组 + 工作日历 + 邮箱地址，是路由与 ACL 的单位 — [Admin](https://doc.znuny.org/znuny_lts/admin/)
- GLPI：指派给组/技术员/供应商即驱动状态进入 *Processing (assigned)* — [Ticket lifecycle](https://help.glpi-project.org/documentation/modules/assistance/tickets/ticketlifecycle.md)
- Chatwoot：轮询分派、高级分派策略、**单坐席容量上限**、Inbox(渠道) 与 Team(人) 分离 — [round-robin](https://www.chatwoot.com/hc/user-guide/articles/1677696868-assigning-conversations-in-a-round_robin-fashion)、[assignment v2](https://www.chatwoot.com/hc/user-guide/articles/1763978164-chatwoot-assignment-v2)、[capacity](https://www.chatwoot.com/hc/user-guide/articles/1741998212-agent-capacity)
- 关注者/订阅：Znuny Watch + `Manage Ticket Subscriptions` — [Watcher](https://doc.znuny.org/znuny_lts/agentinterface/ticketviews/agentticketwatcher/index.html)；iTop 订阅粒度（全退 / 留一个 / 不退）— [Notifications](https://www.itophub.io/wiki/page?id=3_2_0:admin:notifications)
- 邮件入站时从头部**采集 CC/观察者**、Reply-To 当请求人（GLPI Receivers）— [Receivers](https://help.glpi-project.org/documentation/modules/configuration/collectors.md)
- Znuny 区分 owner / responsible / lock 三种归属 — [Responsible](https://doc.znuny.org/znuny_lts/agentinterface/ticketviews/agentticketresponsible/index.html)
- **技能路由：全部项目 `unverified`**（Chatwoot 是负载型而非技能型）

### 2.3 自动化

- Zammad **Activator** 三类触发：Selective / Always / **Time Event**（提醒、升级、升级预警）；动作含改单、发邮件/SMS、Webhook、加备注、调 AI Agent — [Trigger](https://admin-docs.zammad.org/en/latest/manage/trigger.html)
- Zammad **Scheduler** 按 日×时间 矩阵作用于 Organization/Ticket/User，并有**每次 2000 对象上限**作为误配保险 — [Scheduler](https://admin-docs.zammad.org/en/latest/manage/scheduler.html)
- Zammad 刻意区分 Trigger（自动）/ Email Filter（渠道）/ Macro（坐席手动）— 同上
- Znuny **Generic Agent** 周期批量改单 — [Generic Agent](https://doc.znuny.org/znuny_lts/admin/automation/generic_agent/index.html)
- GLPI **Business Rules** 驱动多级审批，文档强调规则必须**倒序执行**（最高审批级别在前），否则一次全部触发 — [Approvals](https://help.glpi-project.org/documentation/modules/assistance/tabs/approvals.md)
- 条件字段显隐/必填：Zammad Core Workflows、Znuny Dynamic Field Screens、GLPI FormCreator（25 种问题类型、条件显隐、target 可把表单答案写进 SLA）— [Core Workflows](https://admin-docs.zammad.org/en/latest/system/core-workflows.html)、[FormCreator](https://help.glpi-project.org/faq/plugins/formcreator.md)
- FreeScout 明确区分自动/手动 workflow，日期条件**只到小时不含分钟**，并有 `Max Executions` 因为 workflow 会互相触发成死循环 — [Workflows](https://freescout.net/module/workflows/)
- **自动化配置变更本身要进审计**（Zammad audit 覆盖 trigger/scheduler/macro/calendar/SLA/webhook…）— [Audit Logs](https://admin-docs.zammad.org/en/latest/system/audit-logs.html)

### 2.4 审批工作流（差距最大的一环）

- GLPI **Approval Step**：可设**最低通过百分比**，审批人可以是用户/组/**组成员个人** — [Approvals](https://help.glpi-project.org/documentation/modules/assistance/tabs/approvals.md)
- 顺序链用业务规则实现（HR→财务→IT→部门负责人），拒绝规则短路 — 同上
- 审批状态模型 Pending/Granted/Rejected，**允许本人改判**；删除审批**不改变**最终状态 — 同上
- iTop **Approval process automation**：多级、每级多个审批人**并行**、按状态进入触发 — [Approval extended](https://www.itophub.io/wiki/page?id=extensions:approval_extended)
- **无账号者用一次性邮件链接审批** — 同上
- **超时**：Delay 只在**覆盖时间窗**内计时，且有 **"Approved if no answer"** 默认结论字段 — 同上
- **法定人数语义枚举**：`Approval ending` ∈ {ends on first approve / first reject / first reply} — 同上
- **委派/代理人**：Substitute 用 OQL 解析（如 `SELECT Person WHERE id=:approver->manager_id`），按 delay 百分比发提醒；`reuse_previous_answers` 复用上级结论 — 同上
- **特权绕过单独记录**：`bypass_profiles`（默认 Administrator/SuperUser/Service Manager）— 同上
- **审批审计轨迹**：按审批人列出答复、时间戳、意见；重新进入待审批会重置；催办也是被追踪的动作 — 同上
- 变更/CAB：Znuny 变更管理为**单独安装**的 ITSM 包，含 change builder / change manager / **change advisory board** 角色；并建议"多数情况下用流程而非启用变更管理" — [ITSM features](https://doc.znuny.org/znuny_lts/itsmfeatures/index.html)
- Zammad / FreeScout / osTicket / Chatwoot **的审批引擎 `unverified`**（未见一手文档）

### 2.5 知识库

- Zammad 三层可见性：Public / Internal / Draft-Scheduled-Archived，可定时发布 — [KB](https://user-docs.zammad.org/en/latest/extras/knowledge-base.html)
- 分类权限**可继承**：父类权限向下继承，`reader` 可在子类收紧/放宽，`editor` 不可以，"none" 不能在下级恢复 — [KB admin](https://admin-docs.zammad.org/en/latest/manage/knowledge-base.html)
- 多语言：未翻译页面在编辑态告警、发布态对访客**隐藏** — 同上
- GLPI **Revision**：每次保存生成修订、可查看并可**还原** — [Knowledge base](https://help.glpi-project.org/documentation/modules/tools/knowledgebase.md)
- GLPI **target 模型**：文章必须有一个或多个 target，**无 target 即 unpublished**，仅作者可见 — 同上
- 可见起止时间、内部 KB 与公开 FAQ 分离、检索运算符、浏览量、评论 — 同上
- 双向文章↔工单关联；工单里输入 `??` 触发全库全文检索并插入光标处（Zammad）— [KB](https://user-docs.zammad.org/en/latest/extras/knowledge-base.html)
- **KCS 式"从工单沉淀草稿"、有用/无用反馈、评审/过期工作流：全部 `unverified`**（GLPI 有起止时间但那是可见性，不是评审到期）

### 2.6 邮件与渠道

- GLPI Receivers：IMAP/POP、SSL/TLS、证书校验、OAuth、按文件夹归档、附件上限、"只收未读"，以及**引用清理**："`up` 与 `bottom` 标签之间的内容会被删除，答复必须写在原文之前或之后" — [Receivers](https://help.glpi-project.org/documentation/modules/configuration/collectors.md)
- 入站**规则路由**：按收件人、请求人、域名已知/未知、头部（`in_reply_to`/`auto_submitted`/`X-Auto-Response-Suppress`/`X-priority`…）与正文分流，动作为"拒收（可通知发件人）"或"导入某实体"，**命中第一条即停止** — 同上
- 租户判定用规则 + 头部证据（known email domain / user group / single profile），目的正是避免单画像用户越权跨实体 — 同上
- Znuny PostMaster Mail Account + PostMaster Filter — [PostMaster](https://doc.znuny.org/znuny_lts/admin/email/postmaster_mail_account.html)
- osTicket 同时支持 POP3/IMAP 与 **Email Piping** — [Email Piping](https://docs.osticket.com/en/latest/Getting%20Started/Email%20Piping.html)
- 通知模板 i18n：iTop 每个邮件动作带 Language 字段 + HTML 模板 + **Test recipient** 测试态 — [Notifications](https://www.itophub.io/wiki/page?id=3_2_0:admin:notifications)
- **把慢 I/O 移出请求路径**：iTop 推荐 SMTP 而非 PHP `mail()`，并用 `email_asynchronous` + 后台 cron 异步发送 — 同上
- Web 表单渠道的**入口滥用控制**（Zammad）：蜜罐 + ALTCHA/Turnstile/hCaptcha/Friendly Captcha/reCAPTCHA，且限流可配 `form_ticket_create_by_ip_per_hour=20`、`by_ip_per_day=240`、`per_day=5000` — [Form channel](https://admin-docs.zammad.org/en/latest/channels/form.html)
- Webhook 作为一等动作（Zammad）— [Webhook](https://admin-docs.zammad.org/en/latest/manage/webhook.html)
- iTop 通知传输：Email / Webhook / iTop REST / Slack / Rocket.Chat / Google Chat / **MS Teams** / 站内 News / 日历邀请 / SMS — [Notifications](https://www.itophub.io/wiki/page?id=3_2_0:admin:notifications)
- Chatwoot 全渠道：Web widget、FB、IG、WhatsApp、Telegram、Line、SMS、X、TikTok、**语音** — [user guide](https://www.chatwoot.com/hc/user-guide/en)
- **摘要邮件 / 定时报表投递：`unverified`**

### 2.7 CMDB / 资产

- GLPI 类型极全（计算机/显示器/软件/网络设备/外设/打印机/机架/线缆/SIM…）+ **用户自定义资产类型**；新类型同样进入影响分析 — [Assets](https://help.glpi-project.org/documentation/modules/assets.md)、[Asset definitions](https://help.glpi-project.org/documentation/modules/configuration/asset-definitions.md)
- **按类型启用影响分析** + 影响图遍历（iTop `core/get_related` 带 `relation:"impacts"`、`direction`、`depth`、`redundancy`）— [Impact analysis](https://help.glpi-project.org/documentation/modules/configuration/general/impact_analysis.md)、[REST/JSON](https://www.itophub.io/wiki/page?id=3_2_0:advancedtopics:rest_json)
- CI ↔ 工单关联表（iTop `lnkFunctionalCIToTicket`），甚至可用于通知收件人 OQL — [Notifications](https://www.itophub.io/wiki/page?id=3_2_0:admin:notifications)
- 自动发现：GLPI Agent/SNMP/云清单；iTop Data Collectors（LDAP、OCS、vSphere、Azure、InTune、Proxmox、CheckMK、Ansible）— [Inventory](https://help.glpi-project.org/tutorials/inventory.md)
- 资产生命周期：iTop 的 obsolescence（列表隐藏）与 archived（软删除）— [Data model](https://www.itophub.io/wiki/page?id=3_2_0:datamodel:start)
- 纯 helpdesk（osTicket/FreeScout/Chatwoot/UVdesk）**没有 CMDB**，最近的对象是 Organization/Company

### 2.8 审计、RBAC 与合规

- GLPI：**按实体（entity）授权的 Profile 矩阵** + 生命周期矩阵（哪些状态可见/可流转）— [Profiles](https://help.glpi-project.org/documentation/modules/administration/profiles.md)、[Lifecycle matrix](https://help.glpi-project.org/documentation/modules/administration/profiles/lifecyclematrix.md)
- Znuny 独立 ACL 层（role/group 之上）— [ACL reference](https://doc.znuny.org/znuny_lts/annexes/acl_reference/acl_properties.html)
- **不可变性作为承诺**（Znuny）："用户无法修改核心数据；所有通信及其附件对用户不可更改；工单生命周期内的所有修改都记录在历史中 —— *谁在何时做了什么*"，删除是单独记录的超管操作 — [About Znuny](https://doc.znuny.org/znuny_lts/about/)
- **专用安全审计日志**（Zammad）：记录管理权限动作（操作者、动作、对象类型、对象名、**来源 IP**、时间），只读，**刻意不记录日常工单更新**，固定 12 个月后自动清理且**不可配置** — [Audit Logs](https://admin-docs.zammad.org/en/latest/system/audit-logs.html)
- 逐对象变更历史带 old→new 差异（GLPI History）— [History](https://help.glpi-project.org/documentation/modules/tools/knowledgebase.md)
- GDPR 作为管理面：Zammad Data Privacy 删除用户会连带删除其客户工单（不能只删人留单）、有影响工单预览、需输入 `DELETE` 确认、删除队列有活动流记录；文档坦承内部备注里的引用**不会被清除** — [Data privacy](https://admin-docs.zammad.org/en/latest/system/data-privacy.html)
- 2FA / SSO / LDAP / SAML / SCIM / OAuth2 scope 最小权限 — [Znuny user backends](https://doc.znuny.org/znuny_lts/admin/usermanagement/user_backends/index.html)、[GLPI LDAP](https://help.glpi-project.org/documentation/modules/configuration/authentication/ldap.md)、[GLPI SCIM](https://help.glpi-project.org/doc-plugins/plugin-glpi-network/scim.md)、[Chatwoot SAML](https://www.chatwoot.com/hc/user-guide/articles/1758635327-setting-up-saml)
- **IP 允许列表：全部 `unverified`**

### 2.9 报表与质量

- Znuny 一等统计模块 + 可配置模板 + 统计属性参考 — [Statistics](https://doc.znuny.org/znuny_lts/agentinterface/statistics/index.html)
- GLPI 工单级 SLA 统计页（TTO/等待/TTR/关单，超标标红）— [Service levels FAQ](https://help.glpi-project.org/faq/glpi/service_levels.md)
- CSAT：GLPI 满意度调研、Chatwoot CSAT（含 WhatsApp 模板调研与 Review Notes）、iTop Customer Survey — [GLPI satisfaction](https://help.glpi-project.org/tutorials/helpdesk/satisfaction.md)、[Chatwoot CSAT](https://www.chatwoot.com/hc/user-guide/articles/1677503828-how-to-enable-csat-surveys)
- Chatwoot：FRT/NRT/RT + **Bot 报表与坐席报表分离** + 实时概览 — [SLA reports](https://www.chatwoot.com/hc/user-guide/articles/1724145182-how-to-read-sla-reports)、[Bot reports](https://www.chatwoot.com/hc/user-guide/articles/1724141456-how-to-read-the-bot-reports)
- 原始数据导出：osTicket CSV、iTop Export + OQL — [Data extraction](https://docs.osticket.com/en/latest/Guides/Data%20Extraction%20Guide.html)
- **重开率作为命名指标 / 定时报表：`unverified`**

### 2.10 多租户与品牌

- GLPI **递归实体树**是隔离原语：Profile 按实体绑定、KB target 可为实体、邮件规则把工单路由**进**实体 — [Entities](https://help.glpi-project.org/documentation/modules/administration/entities.md)
- 多品牌/白标：Zammad Branding、Chatwoot Whitelabeling(EE)、FreeScout Customization 模块 — [Branding](https://admin-docs.zammad.org/en/latest/settings/branding.html)
- iTop 有面向服务商的交付变体（每客户独立基础设施 + 供应商/客户合同）— [Service mgmt](https://www.itophub.io/wiki/page?id=3_2_0:datamodel:itop-service-mgmt)
- **硬隔离（每租户独立库/schema）：全部 `unverified`**

### 2.11 API 与扩展（压缩）

- GLPI 双版本 API：`/api.php/v1` 旧、`/api.php/v2` 新（OAuth2 密码/授权码、版本钉扎、Swagger、**只读 GraphQL**、RSQL/FIQL 过滤）— [RESTful API V2](https://help.glpi-project.org/documentation/modules/configuration/general/api/restful-api-v2.md)
- iTop 对**任意对象**开放 `core/create|get|update|delete|apply_stimulus|get_related|check_credentials`，token 认证、bulk read/write 开关、**参数版本 + 变更历史表**保证操作稳定 — [REST/JSON](https://www.itophub.io/wiki/page?id=3_2_0:advancedtopics:rest_json)
- Znuny `.opm` 包校验 MD5、逐文件差异 + 一键还原、安装前展示代码与库变更、6.5 起强制依赖检查 — [Package management](https://doc.znuny.org/znuny_lts/admin/packagemanagement/index.html)

### 2.12 运维（压缩）

- Znuny 每个次版本独立升级指南 + 专用备份附录 — [Update](https://doc.znuny.org/znuny_lts/releases/installupdate/update.html)、[Backup](https://doc.znuny.org/znuny_lts/annexes/backup/index.html)
- GLPI SQL 只读副本、缓存层、状态/监控页、CLI、10→11 插件迁移流程 — [SQL replicas](https://help.glpi-project.org/documentation/modules/configuration/general/sql_replicas.md)、[Status](https://help.glpi-project.org/documentation/advanced/status.md)
- Chatwoot 发布 Docker Compose / Kubernetes Helm / VM / 多云指南 + 备份/升级/v4 升级/IP 日志 runbook — [Docker](https://developers.chatwoot.com/self-hosted/deployment/docker)
- **Zammad 是唯一有一手文档化入口限流的项目** — [Form channel](https://admin-docs.zammad.org/en/latest/channels/form.html)
- **各项目测试套件/CI 细节、无障碍 WCAG 声明：`unverified`**

---

## 3. 表列项 vs 差异化项

### 表列项（缺席即为硬伤）

1. **带系统语义的工单状态类型**（不是自由文本标签）——SLA/升级/报表/筛选全都依赖它
2. **日历对象（工作时间/时区/节假日）被 SLA 引用**——否则 SLA 是 24×7，且是静默错误
3. SLA 同时含**响应与解决**目标 + 带预警阈值的升级
4. **暂停/冻结语义**（GLPI sleep、Zammad SLA-ignored 标志）
5. **队列/组模型同时充当 ACL 边界**
6. 触发器/规则 + 快捷回复
7. **自定义字段 + 条件可见表单**
8. **入站邮件成单**（引用剥离、规则路由、可拒收）
9. **内部备注 vs 公开回复**是类型化区别
10. 客户自助门户
11. 保存视图/个人队列/批量操作
12. 角色权限模型 + 变更历史
13. REST API（token/OAuth）+ Webhook
14. 知识库
15. CSAT 满意度调研
16. 备份恢复 + 有文档的升级路径
17. 容器化部署

### 差异化项（只有最强的产品有）

1. **多级审批 + 显式法定人数模型 + 可配置超时结论**（iTop 三种 `Approval ending` + `Approved if no answer`；GLPI 百分比阈值 + 规则链）——**大多数产品根本没有审批引擎**
2. **无账号者一次性邮件链接审批**（仅 iTop）
3. **代理人/委派 + 按超时升级**（仅 iTop）
4. 单产品内 ITIL 全流程广度（事件+请求+问题+变更+已知错误+CAB+服务目录）
5. **真 CMDB + 影响分析图遍历**（GLPI、iTop；纯 helpdesk 都没有）
6. **自动发现/清单反哺 CMDB**
7. **审计不可变性作为明示承诺**（Znuny 最强，Zammad 次之）
8. **知识文章版本化 + 还原**（GLPI Revisions）
9. 可按分类继承的细粒度 KB 权限（Zammad）
10. 对象可见性权限**级联到选择器与快捷回复**（osTicket）
11. 协作编辑：共享草稿、检查清单、@提及（Zammad/Znuny）
12. **坐席容量策略 + 高级分派策略**（Chatwoot）
13. 邮件+Web 之外的**全渠道**
14. **内置 AI 面**：Chatwoot Captain（assistants/documents/FAQs/memories/copilot/custom tools/audience+schedule）、Zammad AI Agents（可由 Trigger/Scheduler 触发 + KB 助手 + 写作助手 + 工单摘要 + 反馈日志）
15. **面向运维的规模化文档**（GLPI 副本/缓存/大库；Chatwoot 多云 + Helm 矩阵）
16. **为表单渠道调优的入口滥用控制**

---

## 4. 架构教训（每条 1-3 句）

1. **SLA 时间是日历运算，不是时长运算**——所有成熟产品都把工作时间/时区/节假日做成一等对象，只在时间窗内计算升级；Znuny 明确记录了"没配日历就静默退化成 24×7"这一失败模式。
2. **暂停用状态标志实现，不要写特例**——Zammad 的状态自带 "SLA ignored" 标志，GLPI 用显式 sleep 模式顺延到期时间。
3. **把升级做成对持久化计时字段的定时扫描，而不是请求内计算**——Znuny 每 5 分钟跑一次 `EscalationCheck`，并只在定义好的事件集上重算升级索引；这才让 SLA 能跨重启与横向扩展。
4. **通信日志与行政审计日志必须分开**——Znuny 保证通信/附件不可更改、生命周期变更进历史；Zammad 的审计日志只读、记来源 IP、**刻意排除日常工单更新**、固定 12 个月清理。混为一谈会得到"无限增长的审计表"或"不可审计的历史"。
5. **永不原地覆盖：内容要版本化，还原要是一等操作**——GLPI KB 每次保存生成修订且可还原；iTop 的 case-log API 把变更模式（append / single add_item / 全量 items）显式化。
6. **入站邮件线程化是数据清洗问题，不是解析问题**——GLPI 导入时剥离 `up`/`bottom` 之间的引用内容并要求答复写在原文之外；路由是按头部（`in_reply_to`、`auto_submitted`…）的规则引擎，**命中第一条即停**，拒收是合法的终态。
7. **租户在"入库时"由规则与头部证据判定，而不是创建后再归属**——GLPI 用 known-email-domain / user-group / single-profile 规则避免单画像用户跨实体泄漏。
8. **审批时限要从覆盖时间窗里算，且"无答复的结论"必须是配置字段**——iTop 只把时间窗内的小时计入审批 delay，到期即终止，结论取自 `Approved if no answer`；没有显式默认结论，审批流会死锁。
9. **法定人数语义要枚举化，不要写成散落的分支**——iTop 的三种 `Approval ending` 用一个引擎表达"任一通过/全体通过/全体否决"；GLPI 用每步最低通过百分比 + 规则链达到类似效果，并警告规则**顺序即语义**：高级别审批必须排在前面，否则一次全部触发。
10. **把委派设计进审批人模型，并给代理人一个提醒阈值**——iTop 用 OQL 解析代理人并按 delay 百分比（如 70h 的 80%）提醒。
11. **知识库的"受众模型"与"权限模型"要分开**——GLPI 要求文章必须有 target，无 target 即 `unpublished` 仅作者可见；Zammad 用三层可见性 + 分类继承权限表达同一件事但语义更丰富。
12. **扩展分发要可校验、可回滚**——Znuny `.opm` 携带 MD5、逐文件 diff + 一键还原、安装前展示代码与库变更、强制依赖声明；卸载会删掉自己的库表，这正是"安装前审查库变更"的意义。
13. **把慢 I/O 移出请求路径，并给失控自动化加上限**——iTop 异步发信；Zammad 给一次 scheduler 运行加 2000 对象上限；FreeScout 在发现 workflow 会互相触发成死循环后加了 `Max Executions`。
14. **尽早决定企业特性是否同仓 + 许可，并写进文档**——Chatwoot 同仓 CE+专有 EE，GLPI 用需订阅的付费插件；否则评估者会误判产品能力。
15. **API 要显式版本化并让旧版存活**——GLPI 用 `/v1`、`/v2` 路由并加只读 GraphQL 补齐嵌套字段；iTop 版本化**参数**并公布"iTop 版本 → JSON REST 版本 → 变更"的历史表，承诺除缺陷修复、文案与新增响应字段外操作保持稳定。

---

## 5. 明确未证实（不要当作"不存在"，只是未证实）

拆分工单、技能路由、KB 有用/无用反馈、KB 评审/到期流程、KCS 从工单沉淀、摘要邮件、
定时报表投递、重开率命名指标、IP 允许列表、WCAG/无障碍声明、各项目测试套件与 CI 细节、
OTOOO 分叉细节、每租户硬隔离、服务端幂等键作为平台特性、工具结果 schema 校验。
