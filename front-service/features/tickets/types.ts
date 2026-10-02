// features/tickets/types.ts
// 工单领域类型：与 java-service 的 TicketSummary / TicketDetail / TicketComment / TicketActivity / TicketLink 一一对应

import { formatDistanceToNow } from 'date-fns'
import { zhCN } from 'date-fns/locale'

export type TicketType = 'incident' | 'request' | 'problem' | 'change'

export type TicketStatus =
  | 'new'
  | 'open'
  | 'in_progress'
  | 'pending'
  | 'resolved'
  | 'closed'

export type TicketPriority = 'critical' | 'high' | 'medium' | 'low'

export type TicketImpact = 'high' | 'medium' | 'low'

export type TicketUrgency = 'high' | 'medium' | 'low'

export type TicketSource = 'manual' | 'chat' | 'email' | 'api'

export type TicketLinkType =
  | 'related'
  | 'duplicates'
  | 'blocks'
  | 'blocked_by'
  | 'caused_by'

/** outgoing = 本工单声明的关联；incoming = 对端工单声明的关联。 */
export type TicketLinkDirection = 'outgoing' | 'incoming'

export type CommentVisibility = 'public' | 'internal'

export interface TicketSummary {
  id: string
  /** 人类可读编号，界面统一显示为 #1042。 */
  number: number
  type: TicketType
  status: TicketStatus
  priority: TicketPriority
  impact: TicketImpact
  urgency: TicketUrgency
  title: string
  requesterId: string
  assigneeId?: string | null
  parentId?: string | null
  mergedIntoId?: string | null
  source: TicketSource
  createdAt?: string | null
  updatedAt?: string | null
  firstResponseAt?: string | null
  resolvedAt?: string | null
  closedAt?: string | null
}

export interface TicketComment {
  id: string
  ticketId: string
  authorId: string
  /** 逗号分隔的角色快照，服务端写入时固定。 */
  authorRoles: string
  visibility: CommentVisibility
  body: string
  createdAt?: string | null
}

export interface TicketActivity {
  id: string
  ticketId: string
  actorId?: string | null
  activityType: string
  fromValue?: string | null
  toValue?: string | null
  detail: Record<string, unknown>
  createdAt?: string | null
}

export interface TicketLink {
  id: string
  ticketId: string
  linkedTicketId: string
  linkType: TicketLinkType
  direction: TicketLinkDirection
  linkedNumber?: number | null
  linkedTitle?: string | null
  linkedStatus?: string | null
  createdBy: string
  createdAt?: string | null
}

export interface TicketDetail extends TicketSummary {
  description: string
  resolution?: string | null
  createdBy: string
  conversationId?: string | null
  comments: TicketComment[]
  activities: TicketActivity[]
  links: TicketLink[]
  children: TicketSummary[]
}

// ---------------------------------------------------------------------------
// 接口包装
// ---------------------------------------------------------------------------

export interface TicketCounts {
  byStatus: Record<string, number>
  byPriority: Record<string, number>
}

export interface TicketPage {
  items: TicketSummary[]
  total: number
  page: number
  size: number
  counts: TicketCounts
}

/** GET /v1/tickets/meta：所有枚举与状态机的唯一来源。 */
export interface TicketMeta {
  types: string[]
  statuses: string[]
  priorities: string[]
  impactLevels: string[]
  urgencyLevels: string[]
  linkTypes: string[]
  /** 处理人角色码；与登录用户的 roles 比对决定能否写内部备注。 */
  agentRoles: string[]
  /** status -> 允许的下一状态。界面只渲染这里给出的流转。 */
  transitions: Record<string, string[]>
}

export interface TicketListParams {
  status?: string
  type?: string
  assignee?: string
  requester?: string
  query?: string
  page?: number
  size?: number
}

export interface CreateTicketRequest {
  type: TicketType
  title: string
  description: string
  impact: TicketImpact
  urgency: TicketUrgency
  assigneeId?: string
  parentId?: string
  conversationId?: string
}

/** PATCH 只提交发生变化的字段；priority 由服务端按影响 × 紧急推导。 */
export interface UpdateTicketRequest {
  title?: string
  description?: string
  impact?: TicketImpact
  urgency?: TicketUrgency
  priority?: TicketPriority
  assigneeId?: string
  type?: TicketType
}

export interface TransitionTicketRequest {
  status: string
  comment?: string
  resolution?: string
}

export interface AddTicketCommentRequest {
  body: string
  visibility: CommentVisibility
}

export interface AddTicketLinkRequest {
  targetId: string
  linkType: TicketLinkType
}

export interface MergeTicketRequest {
  targetId: string
}

export interface MergeTicketResponse {
  source: TicketDetail
  target: TicketDetail
}

export interface SplitTicketRequest {
  commentId: string
  title: string
  description?: string
}

// ---------------------------------------------------------------------------
// 界面筛选状态
// ---------------------------------------------------------------------------

/** 我的工单 = 处理人是我；全部 = 服务端返回的全部可见工单。 */
export type TicketScope = 'mine' | 'all'

export type TicketStatusFilter = 'all' | TicketStatus

export type TicketTypeFilter = 'all' | TicketType

export type TicketPriorityFilter = 'all' | TicketPriority

export const TICKET_PAGE_SIZE = 20

/** 搜索框防抖：太短会打爆后端，太长会让用户以为没反应。 */
export const SEARCH_DEBOUNCE_MS = 350

/**
 * meta 不可用时的兜底枚举。
 *
 * 正常流程一律以 GET /v1/tickets/meta 为准；这里只保证 meta 请求失败时界面还能用，
 * 服务端仍会对每个取值做最终校验。
 */
export const TICKET_TYPES: readonly TicketType[] = [
  'incident',
  'request',
  'problem',
  'change',
]

export const TICKET_STATUSES: readonly TicketStatus[] = [
  'new',
  'open',
  'in_progress',
  'pending',
  'resolved',
  'closed',
]

export const TICKET_PRIORITIES: readonly TicketPriority[] = [
  'critical',
  'high',
  'medium',
  'low',
]

export const TICKET_IMPACT_LEVELS: readonly TicketImpact[] = ['high', 'medium', 'low']

export const TICKET_URGENCY_LEVELS: readonly TicketUrgency[] = ['high', 'medium', 'low']

export const TICKET_LINK_TYPES: readonly TicketLinkType[] = [
  'related',
  'duplicates',
  'blocks',
  'blocked_by',
  'caused_by',
]

// ---------------------------------------------------------------------------
// 显示文案
// ---------------------------------------------------------------------------

export const TICKET_TYPE_LABELS: Record<string, string> = {
  incident: '事件',
  request: '服务请求',
  problem: '问题',
  change: '变更',
}

export const TICKET_STATUS_LABELS: Record<string, string> = {
  new: '新建',
  open: '已受理',
  in_progress: '处理中',
  pending: '等待中',
  resolved: '已解决',
  closed: '已关闭',
}

export const TICKET_PRIORITY_LABELS: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
}

export const TICKET_IMPACT_LABELS: Record<string, string> = {
  high: '高（影响面大）',
  medium: '中（影响部分用户）',
  low: '低（影响单人）',
}

export const TICKET_URGENCY_LABELS: Record<string, string> = {
  high: '高（需立即处理）',
  medium: '中（今日处理）',
  low: '低（可排期）',
}

export const TICKET_LINK_TYPE_LABELS: Record<string, string> = {
  related: '相关',
  duplicates: '重复于',
  blocks: '阻塞',
  blocked_by: '被阻塞',
  caused_by: '由此引起',
}

/** 针对关联对端的描述，读起来是"这条关联意味着什么"。 */
export const TICKET_LINK_TYPE_HINTS: Record<string, string> = {
  related: '两个工单互相关联',
  duplicates: '本工单与目标工单重复',
  blocks: '本工单阻塞目标工单',
  blocked_by: '本工单被目标工单阻塞',
  caused_by: '目标工单由本工单引起',
}

export const TICKET_SOURCE_LABELS: Record<string, string> = {
  manual: '手动创建',
  chat: '对话创建',
  email: '邮件',
  api: '接口',
}

export const COMMENT_VISIBILITY_LABELS: Record<string, string> = {
  public: '公开回复',
  internal: '内部备注',
}

export const TICKET_LINK_DIRECTION_LABELS: Record<string, string> = {
  outgoing: '本工单声明',
  incoming: '对端声明',
}

/** 活动类型 → 中文。未知类型回退成原始码，方便排查而不是显示空白。 */
export const TICKET_ACTIVITY_LABELS: Record<string, string> = {
  created: '创建了工单',
  ticket_created: '创建了工单',
  status_change: '变更了状态',
  status_changed: '变更了状态',
  transition: '变更了状态',
  transitioned: '变更了状态',
  assigned: '分派了工单',
  assignment: '分派了工单',
  reassigned: '重新分派了工单',
  unassigned: '取消了分派',
  priority_change: '调整了优先级',
  priority_changed: '调整了优先级',
  updated: '更新了工单',
  updated_fields: '更新了工单',
  link_added: '添加了关联',
  link_removed: '移除了关联',
  merged: '合并了工单',
  merge: '合并了工单',
  split: '拆分了工单',
  escalated: '升级了工单',
  resolved: '解决了工单',
  reopened: '重新打开了工单',
  closed: '关闭了工单',
  sla_breached: '触发了 SLA 超时',
}

/** 活动类型 → 展示分组，决定图标与配色。 */
export type TicketActivityCategory =
  | 'status'
  | 'assignment'
  | 'link'
  | 'merge'
  | 'split'
  | 'created'
  | 'updated'
  | 'other'

const ACTIVITY_CATEGORIES: Record<string, TicketActivityCategory> = {
  created: 'created',
  ticket_created: 'created',
  status_change: 'status',
  status_changed: 'status',
  transition: 'status',
  transitioned: 'status',
  resolved: 'status',
  reopened: 'status',
  closed: 'status',
  assigned: 'assignment',
  assignment: 'assignment',
  reassigned: 'assignment',
  unassigned: 'assignment',
  link_added: 'link',
  link_removed: 'link',
  merged: 'merge',
  merge: 'merge',
  split: 'split',
  priority_change: 'updated',
  priority_changed: 'updated',
  updated: 'updated',
  updated_fields: 'updated',
  escalated: 'updated',
  sla_breached: 'updated',
}

/** 这些活动与 comments 数组重复，合并时间线时跳过。 */
const COMMENT_ACTIVITY_TYPES = new Set([
  'comment',
  'comment_added',
  'comment_created',
  'comment_deleted',
  'internal_note',
  'internal_note_added',
])

export function isCommentActivityType(activityType: string): boolean {
  return COMMENT_ACTIVITY_TYPES.has(activityType)
}

export function activityCategory(activityType: string): TicketActivityCategory {
  return ACTIVITY_CATEGORIES[activityType] ?? 'other'
}

export function ticketTypeLabel(type?: string | null): string {
  if (!type) return '未知类型'
  return TICKET_TYPE_LABELS[type] ?? type
}

export function ticketStatusLabel(status?: string | null): string {
  if (!status) return '未知状态'
  return TICKET_STATUS_LABELS[status] ?? status
}

export function ticketPriorityLabel(priority?: string | null): string {
  if (!priority) return '未定级'
  return TICKET_PRIORITY_LABELS[priority] ?? priority
}

export function ticketLinkTypeLabel(linkType?: string | null): string {
  if (!linkType) return '关联'
  return TICKET_LINK_TYPE_LABELS[linkType] ?? linkType
}

export function ticketSourceLabel(source?: string | null): string {
  if (!source) return '—'
  return TICKET_SOURCE_LABELS[source] ?? source
}

export function commentVisibilityLabel(visibility?: string | null): string {
  if (!visibility) return '公开回复'
  return COMMENT_VISIBILITY_LABELS[visibility] ?? visibility
}

export function ticketLinkDirectionLabel(direction?: string | null): string {
  if (!direction) return ''
  return TICKET_LINK_DIRECTION_LABELS[direction] ?? direction
}

export function activityLabel(activityType?: string | null): string {
  if (!activityType) return '更新了工单'
  return TICKET_ACTIVITY_LABELS[activityType] ?? activityType
}

/**
 * 活动里的状态/优先级/类型值是机器码，能翻译就翻译。
 * 分派人这类值是用户 id，原样显示即可。
 */
export function humanizeTicketValue(value?: string | null): string {
  if (value === null || value === undefined || value === '') return ''
  if (value === 'null') return ''
  if (value in TICKET_STATUS_LABELS) return TICKET_STATUS_LABELS[value]
  if (value in TICKET_PRIORITY_LABELS) return TICKET_PRIORITY_LABELS[value]
  if (value in TICKET_TYPE_LABELS) return TICKET_TYPE_LABELS[value]
  if (value in TICKET_LINK_TYPE_LABELS) return TICKET_LINK_TYPE_LABELS[value]
  if (value in COMMENT_VISIBILITY_LABELS) return COMMENT_VISIBILITY_LABELS[value]
  if (value === 'true') return '是'
  if (value === 'false') return '否'
  return value
}

export const TICKET_ACTIVITY_DETAIL_LABELS: Record<string, string> = {
  reason: '原因',
  note: '说明',
  comment: '备注',
  resolution: '解决方案',
  targetId: '目标工单',
  sourceId: '来源工单',
  targetNumber: '目标编号',
  linkedTicketId: '关联工单',
  linkType: '关联类型',
  assigneeId: '处理人',
  fromAssigneeId: '原处理人',
  priority: '优先级',
  status: '状态',
  visibility: '可见性',
  fields: '变更字段',
}

/** 工单编号统一显示成 #1042，任何地方都不要裸奔数字。 */
export function formatTicketNumber(number?: number | null): string {
  if (number === null || number === undefined) return '—'
  return `#${number}`
}

export function formatTicketRelativeTime(value?: string | null): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  try {
    return formatDistanceToNow(parsed, { addSuffix: true, locale: zhCN })
  } catch {
    return ''
  }
}

export function formatTicketDateTime(value?: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 内部备注：只对工单处理人可见，非处理人的载荷里不应该出现。 */
export function isInternalComment(comment: Pick<TicketComment, 'visibility'>): boolean {
  return comment.visibility === 'internal'
}

// ---------------------------------------------------------------------------
// 权限
// ---------------------------------------------------------------------------

/**
 * 当前用户是否是工单处理人（agent）。
 *
 * 处理人角色完全由 GET /v1/tickets/meta 的 agentRoles 决定，这里做的是同一个判断的
 * 客户端镜像：只用来决定"要不要渲染内部备注入口"。服务端仍会独立校验，
 * 因此不一致时最坏结果是多看到/少看到一个按钮，绝不会绕过权限。
 */
export function canActAsAgent(
  roles?: string[] | null,
  agentRoles?: string[] | null
): boolean {
  if (!roles || roles.length === 0) return false
  if (!agentRoles || agentRoles.length === 0) return false
  const held = new Set(roles.map((role) => role.toLowerCase()))
  return agentRoles.some((role) => held.has(role.toLowerCase()))
}

/** 非处理人只能写公开回复，内部备注入口必须隐藏。 */
export function canWriteInternalNote(
  roles?: string[] | null,
  agentRoles?: string[] | null
): boolean {
  return canActAsAgent(roles, agentRoles)
}

/**
 * 当前状态允许的下一步。
 *
 * 状态机在服务端，界面只回放 meta.transitions；缺失或为空时不渲染任何流转按钮，
 * 而不是猜一个"大概可以"的动作。
 */
export function allowedTransitions(
  meta: TicketMeta | null | undefined,
  status?: string | null
): string[] {
  if (!meta || !status) return []
  const next = meta.transitions?.[status]
  return Array.isArray(next) ? next : []
}

/** 服务端对不可见的工单返回 404，界面统一用这句话，绝不暗示工单存在。 */
export const TICKET_NOT_FOUND_MESSAGE = '工单不存在，或你的账号无权查看该工单'

/** 服务端稳定错误码 → 中文提示，避免把后端码直接抛给用户。 */
export const TICKET_ERROR_MESSAGES: Record<string, string> = {
  TICKET_NOT_FOUND: TICKET_NOT_FOUND_MESSAGE,
  INVALID_TRANSITION: '该状态流转已不被允许，请刷新后按当前可用操作重试',
  INVALID_TICKET_TYPE: '工单类型无效，请重新选择',
  INVALID_TICKET_STATUS: '工单状态无效，请刷新后重试',
  FORBIDDEN_TICKET_ACTION: '当前账号没有执行该操作的权限',
  TICKET_ALREADY_MERGED: '该工单已被合并，无法继续操作',
  MERGE_SELF: '不能把工单合并到它自己',
  MERGE_CYCLE: '该合并会形成循环，已被阻止',
  LINK_SELF: '不能把工单关联到它自己',
  LINK_DUPLICATE: '该关联已存在，无需重复添加',
  SPLIT_COMMENT_NOT_FOUND: '选中的回复不存在或已被移除，请刷新后重试',
  INTERNAL_NOTE_FORBIDDEN: '只有工单处理人可以添加内部备注',
  INVALID_LINK_TYPE: '关联类型无效，请重新选择',
  RATE_LIMITED: '操作过于频繁，请稍后重试',
  INVALID_REQUEST: '请求参数不合法，请检查后重试',
}

/** 未知错误码原样显示，方便排查，也避免把真实原因吞掉。 */
export function ticketErrorMessage(code?: string | null): string {
  if (!code) {
    return '操作失败，请稍后重试'
  }
  return TICKET_ERROR_MESSAGES[code] ?? code
}

/** 优先级筛选是客户端行为（列表接口没有 priority 参数），只作用在当前页数据上。 */
export function matchesPriorityFilter(
  ticket: Pick<TicketSummary, 'priority'>,
  filter: TicketPriorityFilter
): boolean {
  return filter === 'all' || ticket.priority === filter
}
