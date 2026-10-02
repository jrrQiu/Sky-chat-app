// features/admin/store/admin.store.ts
import { create } from 'zustand'
import {
  createInvitation,
  createUser,
  listAssignableRoles,
  listInvitations,
  listUsers,
  revokeInvitation,
  setUserEnabled,
  updateUserRoles,
  type CreateInvitationRequest,
  type CreateUserRequest,
} from '@/features/admin/services/admin.service'
import {
  adminErrorMessage,
  DEFAULT_ASSIGNABLE_ROLES,
  MEMBER_PAGE_SIZE,
  type InvitationSummary,
  type MemberStatusFilter,
  type UserSummary,
} from '@/features/admin/types'

export interface ActionResult {
  ok: boolean
  message: string
}

export interface InviteActionResult extends ActionResult {
  inviteUrl?: string | null
}

interface AdminState {
  // 成员列表
  members: UserSummary[]
  total: number
  page: number
  size: number
  query: string
  statusFilter: MemberStatusFilter
  membersLoading: boolean
  membersError: string | null

  // 可分配角色白名单
  roles: string[]
  rolesLoading: boolean

  // 邀请列表
  invitations: InvitationSummary[]
  invitationsLoading: boolean
  invitationsError: string | null

  // 逐动作的 pending 标记：不同行可以各自转圈，不互相阻塞
  creatingUser: boolean
  creatingInvitation: boolean
  updatingRoles: Record<string, boolean>
  togglingEnabled: Record<string, boolean>
  revokingInvitations: Record<string, boolean>

  load: (options?: { silent?: boolean }) => Promise<void>
  loadRoles: () => Promise<void>
  loadInvitations: (options?: { silent?: boolean }) => Promise<void>
  loadAll: () => Promise<void>
  setQuery: (query: string) => void
  setStatusFilter: (filter: MemberStatusFilter) => void
  setPage: (page: number) => void
  createUser: (payload: CreateUserRequest) => Promise<ActionResult>
  changeRoles: (userId: string, roles: string[]) => Promise<ActionResult>
  setEnabled: (userId: string, enabled: boolean) => Promise<ActionResult>
  invite: (payload: CreateInvitationRequest) => Promise<InviteActionResult>
  revokeInvitation: (invitationId: string) => Promise<ActionResult>
}

/** 把错误对象翻译成用户能读懂的中文；未知码原样透出。 */
function toMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return adminErrorMessage(error.message)
  }
  return fallback
}

/** 从 record 里删掉一个 key，避免 pending 标记越积越多。 */
function clearFlag(record: Record<string, boolean>, key: string): Record<string, boolean> {
  const next = { ...record }
  delete next[key]
  return next
}

export const useAdminStore = create<AdminState>((set, get) => ({
  members: [],
  total: 0,
  page: 0,
  size: MEMBER_PAGE_SIZE,
  query: '',
  statusFilter: 'all',
  membersLoading: false,
  membersError: null,

  roles: DEFAULT_ASSIGNABLE_ROLES,
  rolesLoading: false,

  invitations: [],
  invitationsLoading: false,
  invitationsError: null,

  creatingUser: false,
  creatingInvitation: false,
  updatingRoles: {},
  togglingEnabled: {},
  revokingInvitations: {},

  load: async (options) => {
    const silent = options?.silent ?? false
    const { query, statusFilter, page, size } = get()
    if (!silent) {
      set({ membersLoading: true, membersError: null })
    }
    try {
      const result = await listUsers({
        query: query.trim() || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
        page,
        size,
      })
      set({
        members: result.items ?? [],
        total: result.total ?? 0,
        // 服务端可能纠正页码（例如删除后越界），以它为准。
        page: typeof result.page === 'number' ? result.page : page,
        size: typeof result.size === 'number' ? result.size : size,
        membersLoading: false,
        membersError: null,
      })
    } catch (error) {
      set({
        membersLoading: false,
        membersError: toMessage(error, '成员列表加载失败'),
      })
    }
  },

  loadRoles: async () => {
    set({ rolesLoading: true })
    try {
      const roles = await listAssignableRoles()
      set({ roles: roles.length > 0 ? roles : DEFAULT_ASSIGNABLE_ROLES, rolesLoading: false })
    } catch {
      // 白名单拿不到不影响主流程，回退到内置列表；服务端仍会校验。
      set({ roles: DEFAULT_ASSIGNABLE_ROLES, rolesLoading: false })
    }
  },

  loadInvitations: async (options) => {
    const silent = options?.silent ?? false
    if (!silent) {
      set({ invitationsLoading: true, invitationsError: null })
    }
    try {
      const invitations = await listInvitations()
      set({ invitations, invitationsLoading: false, invitationsError: null })
    } catch (error) {
      set({
        invitationsLoading: false,
        invitationsError: toMessage(error, '邀请列表加载失败'),
      })
    }
  },

  loadAll: async () => {
    await Promise.all([get().load(), get().loadRoles(), get().loadInvitations()])
  },

  // 筛选条件一变就回到第一页，否则用户会停在越界页码上看到空白。
  setQuery: (query) => {
    set({ query, page: 0 })
    void get().load()
  },

  setStatusFilter: (statusFilter) => {
    set({ statusFilter, page: 0 })
    void get().load()
  },

  setPage: (page) => {
    set({ page: Math.max(0, page) })
    void get().load()
  },

  createUser: async (payload) => {
    set({ creatingUser: true })
    try {
      const created = await createUser(payload)
      set({ creatingUser: false })
      // 新成员可能落在当前筛选之外，用服务端结果重新拉一次列表最稳。
      await get().load({ silent: true })
      return { ok: true, message: `已创建成员 ${created.email}` }
    } catch (error) {
      set({ creatingUser: false })
      return { ok: false, message: toMessage(error, '创建成员失败，请稍后重试') }
    }
  },

  changeRoles: async (userId, roles) => {
    set((state) => ({ updatingRoles: { ...state.updatingRoles, [userId]: true } }))
    try {
      const updated = await updateUserRoles(userId, roles)
      set((state) => ({
        updatingRoles: clearFlag(state.updatingRoles, userId),
        members: state.members.map((member) =>
          member.id === updated.id ? { ...member, ...updated } : member
        ),
      }))
      return { ok: true, message: `已更新 ${updated.email} 的角色` }
    } catch (error) {
      set((state) => ({ updatingRoles: clearFlag(state.updatingRoles, userId) }))
      return { ok: false, message: toMessage(error, '角色更新失败，请稍后重试') }
    }
  },

  setEnabled: async (userId, enabled) => {
    set((state) => ({ togglingEnabled: { ...state.togglingEnabled, [userId]: true } }))
    try {
      const updated = await setUserEnabled(userId, enabled)
      set((state) => ({
        togglingEnabled: clearFlag(state.togglingEnabled, userId),
        members: state.members.map((member) =>
          member.id === updated.id ? { ...member, ...updated } : member
        ),
      }))
      return { ok: true, message: enabled ? `已启用 ${updated.email}` : `已停用 ${updated.email}` }
    } catch (error) {
      set((state) => ({ togglingEnabled: clearFlag(state.togglingEnabled, userId) }))
      return { ok: false, message: toMessage(error, '状态更新失败，请稍后重试') }
    }
  },

  invite: async (payload) => {
    set({ creatingInvitation: true })
    try {
      const created = await createInvitation(payload)
      set({ creatingInvitation: false })
      await get().loadInvitations({ silent: true })
      return {
        ok: true,
        message: `已向 ${created.email} 发送邀请`,
        inviteUrl: created.inviteUrl ?? null,
      }
    } catch (error) {
      set({ creatingInvitation: false })
      return { ok: false, message: toMessage(error, '发送邀请失败，请稍后重试') }
    }
  },

  revokeInvitation: async (invitationId) => {
    set((state) => ({
      revokingInvitations: { ...state.revokingInvitations, [invitationId]: true },
    }))
    try {
      await revokeInvitation(invitationId)
      set((state) => ({
        revokingInvitations: clearFlag(state.revokingInvitations, invitationId),
      }))
      await get().loadInvitations({ silent: true })
      return { ok: true, message: '已撤销该邀请' }
    } catch (error) {
      set((state) => ({
        revokingInvitations: clearFlag(state.revokingInvitations, invitationId),
      }))
      return { ok: false, message: toMessage(error, '撤销失败，请稍后重试') }
    }
  },
}))
