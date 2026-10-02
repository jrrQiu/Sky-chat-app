// features/approvals/store/approval.store.ts
import { create } from 'zustand'
import {
  decideApproval,
  listApprovalDecisions,
  listApprovals,
} from '@/features/approvals/services/approval.service'
import {
  approvalErrorMessage,
  type ApprovalDecision,
  type ApprovalScope,
  type ApprovalStatusFilter,
  type ApprovalTask,
} from '@/features/approvals/types'

interface ApprovalState {
  items: ApprovalTask[]
  scope: ApprovalScope
  statusFilter: ApprovalStatusFilter
  loading: boolean
  error: string | null

  /** 详情弹窗当前打开的审批；同时用于乐观更新。 */
  activeId: string | null
  decisions: Record<string, ApprovalDecision[]>
  decisionsLoading: boolean

  deciding: boolean
  /** 待我审批的数量，用于侧边栏角标。 */
  pendingForMe: number

  load: (options?: { silent?: boolean }) => Promise<void>
  refreshBadge: () => Promise<void>
  setScope: (scope: ApprovalScope) => void
  setStatusFilter: (filter: ApprovalStatusFilter) => void
  openDetail: (approvalId: string) => void
  closeDetail: () => void
  loadDecisions: (approvalId: string) => Promise<void>
  decide: (
    approvalId: string,
    action: 'approve' | 'reject',
    comment?: string
  ) => Promise<{ ok: boolean; message: string }>
}

/** 待我审批 = 我未发起、且仍在 pending 的行。 */
function countPendingForMe(items: ApprovalTask[], userId?: string): number {
  return items.filter(
    (item) => item.status === 'pending' && (!userId || item.userId !== userId)
  ).length
}

export const useApprovalStore = create<ApprovalState>((set, get) => ({
  items: [],
  scope: 'all',
  statusFilter: 'all',
  loading: false,
  error: null,
  activeId: null,
  decisions: {},
  decisionsLoading: false,
  deciding: false,
  pendingForMe: 0,

  load: async (options) => {
    const silent = options?.silent ?? false
    if (!silent) {
      set({ loading: true, error: null })
    }
    try {
      const items = await listApprovals(get().scope)
      set({
        items,
        loading: false,
        error: null,
        pendingForMe: countPendingForMe(items),
      })
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : '审批列表加载失败',
      })
    }
  },

  refreshBadge: async () => {
    try {
      const items = await listApprovals('to_approve')
      set({ pendingForMe: items.length })
    } catch {
      // 角标失败不打断页面；下一次轮询会再试。
    }
  },

  setScope: (scope) => {
    set({ scope })
    void get().load()
  },

  setStatusFilter: (statusFilter) => set({ statusFilter }),

  openDetail: (approvalId) => {
    set({ activeId: approvalId })
    void get().loadDecisions(approvalId)
  },

  closeDetail: () => set({ activeId: null }),

  loadDecisions: async (approvalId) => {
    set({ decisionsLoading: true })
    try {
      const decisions = await listApprovalDecisions(approvalId)
      set((state) => ({
        decisions: { ...state.decisions, [approvalId]: decisions },
        decisionsLoading: false,
      }))
    } catch {
      set((state) => ({
        decisions: { ...state.decisions, [approvalId]: [] },
        decisionsLoading: false,
      }))
    }
  },

  decide: async (approvalId, action, comment) => {
    set({ deciding: true })
    try {
      const response = await decideApproval(approvalId, { action, comment })
      if (response.error) {
        set({ deciding: false })
        return { ok: false, message: approvalErrorMessage(response.error) }
      }

      // 用服务端返回的权威对象替换本地行，并刷新角标与轨迹。
      const updated = response.approval
      if (updated) {
        set((state) => ({
          items: state.items.map((item) =>
            item.id === updated.id ? { ...item, ...updated } : item
          ),
        }))
      }
      await Promise.all([get().load({ silent: true }), get().loadDecisions(approvalId)])

      const retryable = response.resume?.retryable
      set({ deciding: false })
      if (retryable) {
        return {
          ok: true,
          message: '审批已记录，但执行恢复稍后重试',
        }
      }
      return {
        ok: true,
        message: action === 'approve' ? '已通过该申请' : '已驳回该申请',
      }
    } catch (error) {
      set({ deciding: false })
      return {
        ok: false,
        message: error instanceof Error ? error.message : '提交失败，请重试',
      }
    }
  },
}))

export { countPendingForMe }
