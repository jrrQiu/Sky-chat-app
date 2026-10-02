// features/admin/types.ts
// 成员管理与邀请领域类型：与 java-service 的 UserSummary / InvitationSummary 一一对应

import { formatDistanceToNow } from 'date-fns'
import { zhCN } from 'date-fns/locale'

export type MemberStatus = 'active' | 'disabled'

/** 列表筛选用的状态，比 MemberStatus 多一个"全部"。 */
export type MemberStatusFilter = 'all' | MemberStatus

export type InvitationStatus = 'pending' | 'accepted' | 'expired' | 'revoked'

/** 管理页的两个区块：成员与邀请。 */
export type AdminTab = 'members' | 'invitations'

/** GET /v1/admin/users 与 POST /v1/admin/users 返回的成员摘要。 */
export interface UserSummary {
  id: string
  email: string
  name: string
  roles: string[]
  status: MemberStatus
  createdAt?: string | null
  disabledAt?: string | null
}

/** 邀请记录。`revoked` 与 `status` 都会返回，判重时只看 status。 */
export interface InvitationSummary {
  id: string
  email: string
  name?: string | null
  roles: string[]
  invitedBy?: string | null
  expiresAt?: string | null
  acceptedAt?: string | null
  revoked: boolean
  status: InvitationStatus
  inviteUrl?: string | null
}

/** 分页大小固定 20，与后端默认值保持一致。 */
export const MEMBER_PAGE_SIZE = 20

/** 搜索框防抖：太短会打爆后端，太长会让用户以为没反应。 */
export const SEARCH_DEBOUNCE_MS = 350

// ---------------------------------------------------------------------------
// 密码规则
// 服务端才是权威；这里只是把同一套规则前置成提示，避免用户白填一次表单。
// 规则：长度 ≥ 12 且覆盖 4 类字符中的至少 3 类。
// ---------------------------------------------------------------------------

export const PASSWORD_MIN_LENGTH = 12
export const PASSWORD_MIN_CLASSES = 3
export const PASSWORD_CLASS_TOTAL = 4

/** 小写 / 大写 / 数字 / 符号 中命中了多少类。 */
export function countPasswordClasses(password: string): number {
  let classes = 0
  if (/[a-z]/.test(password)) classes += 1
  if (/[A-Z]/.test(password)) classes += 1
  if (/[0-9]/.test(password)) classes += 1
  if (/[^A-Za-z0-9]/.test(password)) classes += 1
  return classes
}

export interface PasswordStrength {
  length: number
  classes: number
  lengthOk: boolean
  classesOk: boolean
  /** 满足服务端规则的客户端镜像，仅用于提示与提前拦截。 */
  ok: boolean
  /** 中文强度档位：未填写 / 不合格 / 合格 / 强。 */
  label: string
  /** 差在哪里的中文说明。 */
  hint: string
}

export function evaluatePasswordStrength(password: string): PasswordStrength {
  const length = password.length
  const classes = countPasswordClasses(password)
  const lengthOk = length >= PASSWORD_MIN_LENGTH
  const classesOk = classes >= PASSWORD_MIN_CLASSES
  const ok = lengthOk && classesOk

  const problems: string[] = []
  if (!lengthOk) {
    problems.push(`至少 ${PASSWORD_MIN_LENGTH} 位（当前 ${length} 位）`)
  }
  if (!classesOk) {
    problems.push(`至少包含 ${PASSWORD_MIN_CLASSES} 类字符（当前 ${classes}/${PASSWORD_CLASS_TOTAL} 类）`)
  }

  const label = length === 0 ? '未填写' : !ok ? '不合格' : classes === PASSWORD_CLASS_TOTAL && length >= 16 ? '强' : '合格'

  return {
    length,
    classes,
    lengthOk,
    classesOk,
    ok,
    label,
    hint: problems.length > 0 ? problems.join('，') : '满足长度与字符类型要求',
  }
}

// ---------------------------------------------------------------------------
// 显示文案
// ---------------------------------------------------------------------------

/** 角色码 → 中文名。白名单之外的角色回退成原始码，避免界面出现空白。 */
export const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  user_admin: '用户管理员',
  network_admin: '网络管理员',
  network_user: '网络使用人',
  it_staff: 'IT 支持',
  it_user: 'IT 使用人',
  hr_staff: '人事',
  hr_user: '人事使用人',
  finance_user: '财务',
  approver: '审批人',
  auditor: '审计员',
  employee: '普通员工',
  knowledge_admin: '知识库管理员',
  security_admin: '安全管理员',
  ops_admin: '运维管理员',
  manager: '部门主管',
  user: '普通用户',
  guest: '访客',
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role
}

export function roleLabels(roles?: string[] | null): string[] {
  return (roles ?? []).map(roleLabel)
}

/**
 * 可分配角色的兜底白名单。
 *
 * 正常流程会用 GET /v1/admin/roles 覆盖它；该接口不可用时至少保证界面还能用，
 * 服务端仍会对每一个角色做最终校验。
 */
export const DEFAULT_ASSIGNABLE_ROLES: string[] = [
  'admin',
  'user_admin',
  'network_admin',
  'it_staff',
  'hr_staff',
  'finance_user',
  'approver',
  'auditor',
  'employee',
]

/** 能进入成员管理页的角色。服务端会再校验一次，这里只决定界面可见性。 */
export const ADMIN_ROLES: readonly string[] = ['admin', 'user_admin']

export function hasAdminRole(roles?: string[] | null): boolean {
  return Boolean(roles?.some((role) => ADMIN_ROLES.includes(role)))
}

export const MEMBER_STATUS_LABELS: Record<MemberStatus, string> = {
  active: '正常',
  disabled: '已停用',
}

export const INVITATION_STATUS_LABELS: Record<InvitationStatus, string> = {
  pending: '待接受',
  accepted: '已接受',
  expired: '已过期',
  revoked: '已撤销',
}

export function memberDisplayName(member: Pick<UserSummary, 'name' | 'email'>): string {
  return member.name?.trim() || member.email
}

export function isPendingInvitation(invitation: InvitationSummary): boolean {
  return invitation.status === 'pending' && !invitation.revoked
}

// ---------------------------------------------------------------------------
// 错误码
// ---------------------------------------------------------------------------

/** 服务端稳定错误码 → 中文提示，避免把后端码直接抛给用户。 */
export const ADMIN_ERROR_MESSAGES: Record<string, string> = {
  FORBIDDEN_NOT_ADMIN: '当前账号没有成员管理权限，请联系管理员',
  FORBIDDEN_ROLE_ESCALATION: '只有管理员可以授予管理员角色',
  LAST_ADMIN_PROTECTED: '不能移除或停用最后一个管理员',
  CANNOT_DISABLE_SELF: '不能停用自己的账号',
  USER_NOT_FOUND: '用户不存在或已被删除',
  EMAIL_ALREADY_REGISTERED: '该邮箱已被注册，可直接在成员列表里操作',
  INVALID_ROLE: '包含不可分配的角色，请重新选择',
  PASSWORD_TOO_SHORT: `密码长度不足，至少需要 ${PASSWORD_MIN_LENGTH} 位`,
  PASSWORD_TOO_WEAK: `密码复杂度不足，至少需要 ${PASSWORD_MIN_CLASSES} 类字符`,
  PASSWORD_TOO_COMMON: '密码过于常见，请更换更复杂的密码',
  INVITATION_INVALID: '邀请链接无效，请向管理员确认链接是否完整',
  INVITATION_EXPIRED: '邀请链接已过期，请联系管理员重新发送',
  INVITATION_ALREADY_ACCEPTED: '该邀请已被接受，请直接登录',
  INVITATION_REVOKED: '该邀请已被管理员撤销',
  INVITATION_NOT_FOUND: '邀请不存在或已被使用',
  ACCOUNT_DISABLED: '账号已被停用，请联系管理员',
  RATE_LIMITED: '操作过于频繁，请稍后重试',
}

/** 未知错误码原样显示，方便排查，也避免把真实原因吞掉。 */
export function adminErrorMessage(code?: string | null): string {
  if (!code) {
    return '操作失败，请稍后重试'
  }
  return ADMIN_ERROR_MESSAGES[code] ?? code
}

/** preview 返回 valid=false 时，从 expiresAt 推断一个更具体的说法。 */
export function invitationInvalidReason(expiresAt?: string | null): string {
  if (expiresAt) {
    const parsed = new Date(expiresAt)
    if (!Number.isNaN(parsed.getTime()) && parsed.getTime() <= Date.now()) {
      return '邀请链接已过期，请联系管理员重新发送'
    }
  }
  return '邀请链接无效或已被撤销，请联系管理员重新获取'
}

// ---------------------------------------------------------------------------
// 时间
// ---------------------------------------------------------------------------

/** 列表里用相对时间更好读；解析失败时回退到空字符串而不是抛错。 */
export function formatRelativeTime(value?: string | null): string {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  try {
    return formatDistanceToNow(parsed, { addSuffix: true, locale: zhCN })
  } catch {
    return ''
  }
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '—'
  return parsed.toLocaleString('zh-CN')
}
