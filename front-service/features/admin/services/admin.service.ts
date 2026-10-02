// features/admin/services/admin.service.ts
// 成员管理 + 邀请：每个函数对应一个后端端点，错误码交给 store 统一映射成中文。
import { apiJson } from '@/lib/api/client'
import type { AuthSession } from '@/lib/auth/session'
import { MEMBER_PAGE_SIZE, type MemberStatus, type InvitationSummary, type UserSummary } from '@/features/admin/types'

export interface ListUsersParams {
  query?: string
  /** 不传即"全部"，与后端的可选 status 参数一致。 */
  status?: MemberStatus
  page?: number
  size?: number
}

export interface UserPage {
  items: UserSummary[]
  total: number
  page: number
  size: number
}

export interface CreateUserRequest {
  email: string
  name: string
  roles: string[]
  password: string
}

export interface CreateInvitationRequest {
  email: string
  name?: string
  roles: string[]
  ttlHours?: number
}

/** 创建邀请时后端会额外返回完整邀请链接，供管理员手动转发。 */
export interface InvitationCreateResult extends InvitationSummary {
  inviteUrl?: string | null
}

export interface InvitationPreview {
  email: string
  name?: string | null
  roles: string[]
  expiresAt?: string | null
  valid: boolean
}

export interface AcceptInvitationRequest {
  token: string
  password: string
  name?: string
}

/** 可分配角色白名单；接口不可用时由调用方回退到内置列表。 */
export async function listAssignableRoles(): Promise<string[]> {
  const data = await apiJson<{ roles?: string[] }>('/v1/admin/roles')
  return data.roles ?? []
}

export async function listUsers(params: ListUsersParams = {}): Promise<UserPage> {
  const search = new URLSearchParams()
  if (params.query) search.set('query', params.query)
  if (params.status) search.set('status', params.status)
  search.set('page', String(params.page ?? 0))
  search.set('size', String(params.size ?? MEMBER_PAGE_SIZE))

  return apiJson<UserPage>(`/v1/admin/users?${search.toString()}`)
}

export function createUser(payload: CreateUserRequest): Promise<UserSummary> {
  return apiJson<UserSummary>('/v1/admin/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateUserRoles(userId: string, roles: string[]): Promise<UserSummary> {
  return apiJson<UserSummary>(`/v1/admin/users/${encodeURIComponent(userId)}/roles`, {
    method: 'PATCH',
    body: JSON.stringify({ roles }),
  })
}

export function disableUser(userId: string): Promise<UserSummary> {
  return apiJson<UserSummary>(`/v1/admin/users/${encodeURIComponent(userId)}/disable`, {
    method: 'POST',
  })
}

export function enableUser(userId: string): Promise<UserSummary> {
  return apiJson<UserSummary>(`/v1/admin/users/${encodeURIComponent(userId)}/enable`, {
    method: 'POST',
  })
}

export function setUserEnabled(userId: string, enabled: boolean): Promise<UserSummary> {
  return enabled ? enableUser(userId) : disableUser(userId)
}

export function createInvitation(
  payload: CreateInvitationRequest
): Promise<InvitationCreateResult> {
  return apiJson<InvitationCreateResult>('/v1/auth/invitations', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function listInvitations(status?: string): Promise<InvitationSummary[]> {
  const search = new URLSearchParams()
  if (status) search.set('status', status)
  const suffix = search.toString()

  const data = await apiJson<InvitationSummary[] | { items?: InvitationSummary[] }>(
    `/v1/auth/invitations${suffix ? `?${suffix}` : ''}`
  )

  // 契约是数组；这里顺手兼容 { items: [...] } 包装，避免后端分期上线时白屏。
  return Array.isArray(data) ? data : (data.items ?? [])
}

export function revokeInvitation(invitationId: string): Promise<void> {
  return apiJson<void>(`/v1/auth/invitations/${encodeURIComponent(invitationId)}`, {
    method: 'DELETE',
  })
}

/** 公开接口：受邀人未登录时也要能拿到邀请详情。 */
export function previewInvitation(token: string): Promise<InvitationPreview> {
  const search = new URLSearchParams({ token })
  return apiJson<InvitationPreview>(`/v1/auth/invitations/preview?${search.toString()}`)
}

/** 公开接口：接受邀请并直接返回与登录同形的会话。 */
export function acceptInvitation(payload: AcceptInvitationRequest): Promise<AuthSession> {
  return apiJson<AuthSession>('/v1/auth/invitations/accept', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
