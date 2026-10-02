// features/tickets/store/ticket.store.ts
import { create } from 'zustand'
import { ApiError } from '@/lib/api/client'
import { useAuthStore } from '@/features/auth/store/auth.store'
import {
  addTicketComment,
  addTicketLink,
  createTicket,
  getTicket,
  getTicketMeta,
  listTickets,
  mergeTicket,
  removeTicketLink,
  splitTicket,
  transitionTicket,
  updateTicket,
} from '@/features/tickets/services/ticket.service'
import {
  canActAsAgent,
  TICKET_ERROR_MESSAGES,
  TICKET_NOT_FOUND_MESSAGE,
  TICKET_PAGE_SIZE,
  ticketErrorMessage,
  type AddTicketCommentRequest,
  type AddTicketLinkRequest,
  type CreateTicketRequest,
  type SplitTicketRequest,
  type TicketCounts,
  type TicketDetail,
  type TicketMeta,
  type TicketPriorityFilter,
  type TicketScope,
  type TicketStatusFilter,
  type TicketSummary,
  type TicketTypeFilter,
  type TransitionTicketRequest,
  type UpdateTicketRequest,
} from '@/features/tickets/types'

/** 逐动作的结果：页面负责 toast，store 只描述发生了什么。 */
export interface TicketActionResult {
  ok: boolean
  message: string
}

export interface TicketCreateResult extends TicketActionResult {
  ticket?: TicketDetail
}

export interface TicketMergeResult extends TicketActionResult {
  target?: TicketDetail
}

export interface TicketSplitResult extends TicketActionResult {
  ticket?: TicketDetail
}

interface TicketState {
  // 列表
  items: TicketSummary[]
  total: number
  page: number
  size: number
  counts: TicketCounts
  scope: TicketScope
  statusFilter: TicketStatusFilter
  typeFilter: TicketTypeFilter
  /** 列表接口没有 priority 参数，该筛选只作用于当前页已加载的数据。 */
  priorityFilter: TicketPriorityFilter
  query: string
  loading: boolean
  error: string | null

  // meta（枚举 + 状态机 + 处理人角色）
  meta: TicketMeta | null
  metaLoading: boolean
  metaError: string | null

  // 详情
  current: TicketDetail | null
  currentId: string | null
  detailLoading: boolean
  detailError: string | null
  /** HTTP 状态码，404 需要渲染成"不可见"而不是"出错"。 */
  detailStatus: number | null

  // 逐动作 pending 标记：不同操作可以各自转圈，互不阻塞
  creating: boolean
  saving: boolean
  transitioning: Record<string, boolean>
  commenting: boolean
  linking: boolean
  unlinking: Record<string, boolean>
  merging: boolean
  splitting: boolean

  load: (options?: { silent?: boolean }) => Promise<void>
  loadMeta: () => Promise<void>
  loadDetail: (ticketId: string, options?: { silent?: boolean }) => Promise<void>
  refreshDetail: () => Promise<void>
  clearDetail: () => void
  setScope: (scope: TicketScope) => void
  setStatusFilter: (filter: TicketStatusFilter) => void
  setTypeFilter: (filter: TicketTypeFilter) => void
  setPriorityFilter: (filter: TicketPriorityFilter) => void
  setQuery: (query: string) => void
  setPage: (page: number) => void
  createTicket: (payload: CreateTicketRequest) => Promise<TicketCreateResult>
  updateTicket: (ticketId: string, payload: UpdateTicketRequest) => Promise<TicketActionResult>
  transitionTicket: (
    ticketId: string,
    payload: TransitionTicketRequest
  ) => Promise<TicketActionResult>
  addComment: (
    ticketId: string,
    payload: AddTicketCommentRequest
  ) => Promise<TicketActionResult>
  addLink: (ticketId: string, payload: AddTicketLinkRequest) => Promise<TicketActionResult>
  removeLink: (ticketId: string, linkId: string) => Promise<TicketActionResult>
  merge: (ticketId: string, targetId: string) => Promise<TicketMergeResult>
  split: (ticketId: string, payload: SplitTicketRequest) => Promise<TicketSplitResult>
}

const EMPTY_COUNTS: TicketCounts = { byStatus: {}, byPriority: {} }

/**
 * 把错误翻译成中文。
 *
 * readApiError 会把服务端的 error 值放进 ApiError.message，所以这里先查错误码表；
 * 服务端对"不可见"的工单返回 404 且可能不带错误码，这时也给同一句提示，
 * 避免把 "Not Found" 这种原始 HTTP 文案抛给用户。
 */
function toMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 404 && !TICKET_ERROR_MESSAGES[error.message]) {
      return TICKET_NOT_FOUND_MESSAGE
    }
    return ticketErrorMessage(error.message)
  }
  if (error instanceof Error && error.message) {
    return ticketErrorMessage(error.message)
  }
  return fallback
}

function errorStatus(error: unknown): number | null {
  if (error instanceof ApiError && typeof error.status === 'number') {
    return error.status
  }
  return null
}

function clearFlag(record: Record<string, boolean>, key: string): Record<string, boolean> {
  const next = { ...record }
  delete next[key]
  return next
}

/**
 * meta 只拉一次。
 *
 * 列表的 assignee/requester 参数取决于当前用户是不是处理人，而这个判断依赖 meta，
 * 所以 load 会等 meta 就绪；用模块级 promise 去重，避免并发请求打出多份 meta。
 */
let metaInFlight: Promise<void> | null = null

export const useTicketStore = create<TicketState>((set, get) => ({
  items: [],
  total: 0,
  page: 0,
  size: TICKET_PAGE_SIZE,
  counts: EMPTY_COUNTS,
  scope: 'all',
  statusFilter: 'all',
  typeFilter: 'all',
  priorityFilter: 'all',
  query: '',
  loading: false,
  error: null,

  meta: null,
  metaLoading: false,
  metaError: null,

  current: null,
  currentId: null,
  detailLoading: false,
  detailError: null,
  detailStatus: null,

  creating: false,
  saving: false,
  transitioning: {},
  commenting: false,
  linking: false,
  unlinking: {},
  merging: false,
  splitting: false,

  loadMeta: async () => {
    if (get().meta) return
    if (metaInFlight) return metaInFlight

    set({ metaLoading: true, metaError: null })
    metaInFlight = (async () => {
      try {
        const meta = await getTicketMeta()
        set({ meta, metaLoading: false, metaError: null })
      } catch (error) {
        // meta 失败不阻断列表：筛选回退到内置枚举，流转按钮为空（宁可不给，也不猜）。
        set({
          metaLoading: false,
          metaError: toMessage(error, '工单元数据加载失败，部分操作可能不可用'),
        })
      }
    })()

    try {
      await metaInFlight
    } finally {
      metaInFlight = null
    }
  },

  load: async (options) => {
    const silent = options?.silent ?? false
    // 处理人角色决定"我的工单"用 assignee=me 还是 requester=me，先保证 meta 就绪。
    if (!get().meta) {
      await get().loadMeta()
    }

    const { statusFilter, typeFilter, query, page, size, scope } = get()
    if (!silent) {
      set({ loading: true, error: null })
    }

    try {
      const user = useAuthStore.getState().user
      const isAgent = canActAsAgent(user?.roles, get().meta?.agentRoles)
      const result = await listTickets({
        status: statusFilter === 'all' ? undefined : statusFilter,
        type: typeFilter === 'all' ? undefined : typeFilter,
        // 非处理人不可能是 assignee，他们的"我的工单"就是自己提交的工单。
        assignee: scope === 'mine' && isAgent ? 'me' : undefined,
        requester: scope === 'mine' && !isAgent ? 'me' : undefined,
        query: query.trim() || undefined,
        page,
        size,
      })

      set({
        items: result.items ?? [],
        total: typeof result.total === 'number' ? result.total : 0,
        // 服务端可能纠正页码（筛选变化后越界），以它为准。
        page: typeof result.page === 'number' ? result.page : page,
        size: typeof result.size === 'number' ? result.size : size,
        counts: result.counts ?? EMPTY_COUNTS,
        loading: false,
        error: null,
      })
    } catch (error) {
      set({
        loading: false,
        error: toMessage(error, '工单列表加载失败'),
      })
    }
  },

  loadDetail: async (ticketId, options) => {
    const silent = options?.silent ?? false
    if (!silent) {
      set({
        detailLoading: true,
        detailError: null,
        detailStatus: null,
        currentId: ticketId,
        current: null,
      })
    }

    try {
      const detail = await getTicket(ticketId)
      set({
        current: detail,
        currentId: ticketId,
        detailLoading: false,
        detailError: null,
        detailStatus: null,
      })
    } catch (error) {
      const status = errorStatus(error)
      set({
        current: null,
        currentId: ticketId,
        detailLoading: false,
        detailStatus: status,
        detailError:
          status === 404
            ? TICKET_NOT_FOUND_MESSAGE
            : toMessage(error, '工单加载失败，请稍后重试'),
      })
    }
  },

  refreshDetail: async () => {
    const ticketId = get().currentId
    if (!ticketId) return
    await get().loadDetail(ticketId, { silent: true })
  },

  clearDetail: () =>
    set({
      current: null,
      currentId: null,
      detailLoading: false,
      detailError: null,
      detailStatus: null,
    }),

  // 筛选条件一变就回到第一页，否则用户会停在越界页码上看到空白。
  setScope: (scope) => {
    set({ scope, page: 0 })
    void get().load()
  },

  setStatusFilter: (statusFilter) => {
    set({ statusFilter, page: 0 })
    void get().load()
  },

  setTypeFilter: (typeFilter) => {
    set({ typeFilter, page: 0 })
    void get().load()
  },

  // 优先级是客户端筛选，不需要重新请求。
  setPriorityFilter: (priorityFilter) => set({ priorityFilter }),

  setQuery: (query) => {
    set({ query, page: 0 })
    void get().load()
  },

  setPage: (page) => {
    set({ page: Math.max(0, page) })
    void get().load()
  },

  createTicket: async (payload) => {
    set({ creating: true })
    try {
      const ticket = await createTicket(payload)
      set({
        creating: false,
        current: ticket,
        currentId: ticket.id,
        detailError: null,
        detailStatus: null,
      })
      await get().load({ silent: true })
      return { ok: true, message: `已创建工单 #${ticket.number}`, ticket }
    } catch (error) {
      set({ creating: false })
      return { ok: false, message: toMessage(error, '创建工单失败，请稍后重试') }
    }
  },

  updateTicket: async (ticketId, payload) => {
    set({ saving: true })
    try {
      const detail = await updateTicket(ticketId, payload)
      set((state) => ({
        saving: false,
        current: state.currentId === detail.id ? detail : state.current,
      }))
      await get().load({ silent: true })
      return { ok: true, message: '已保存工单修改' }
    } catch (error) {
      set({ saving: false })
      return { ok: false, message: toMessage(error, '保存失败，请稍后重试') }
    }
  },

  transitionTicket: async (ticketId, payload) => {
    set((state) => ({
      transitioning: { ...state.transitioning, [payload.status]: true },
    }))
    try {
      const detail = await transitionTicket(ticketId, payload)
      set((state) => ({
        transitioning: clearFlag(state.transitioning, payload.status),
        current: state.currentId === detail.id ? detail : state.current,
      }))
      await get().load({ silent: true })
      return { ok: true, message: '状态已更新' }
    } catch (error) {
      set((state) => ({
        transitioning: clearFlag(state.transitioning, payload.status),
      }))
      return { ok: false, message: toMessage(error, '状态流转失败，请稍后重试') }
    }
  },

  addComment: async (ticketId, payload) => {
    set({ commenting: true })
    try {
      const detail = await addTicketComment(ticketId, payload)
      set((state) => ({
        commenting: false,
        current: state.currentId === detail.id ? detail : state.current,
      }))
      await get().load({ silent: true })
      return {
        ok: true,
        message: payload.visibility === 'internal' ? '已添加内部备注' : '已回复',
      }
    } catch (error) {
      set({ commenting: false })
      return { ok: false, message: toMessage(error, '发送失败，请稍后重试') }
    }
  },

  addLink: async (ticketId, payload) => {
    set({ linking: true })
    try {
      const detail = await addTicketLink(ticketId, payload)
      set((state) => ({
        linking: false,
        current: state.currentId === detail.id ? detail : state.current,
      }))
      return { ok: true, message: '已添加关联' }
    } catch (error) {
      set({ linking: false })
      return { ok: false, message: toMessage(error, '添加关联失败，请稍后重试') }
    }
  },

  removeLink: async (ticketId, linkId) => {
    set((state) => ({ unlinking: { ...state.unlinking, [linkId]: true } }))
    try {
      const detail = await removeTicketLink(ticketId, linkId)
      set((state) => ({
        unlinking: clearFlag(state.unlinking, linkId),
        current: state.currentId === detail.id ? detail : state.current,
      }))
      return { ok: true, message: '已移除关联' }
    } catch (error) {
      set((state) => ({ unlinking: clearFlag(state.unlinking, linkId) }))
      return { ok: false, message: toMessage(error, '移除关联失败，请稍后重试') }
    }
  },

  merge: async (ticketId, targetId) => {
    set({ merging: true })
    try {
      const result = await mergeTicket(ticketId, targetId)
      set((state) => ({
        merging: false,
        // 被合并的是源工单本身，留在当前页比跳走更好：用户能看到 mergedInto 提示。
        current: state.currentId === result.source.id ? result.source : state.current,
      }))
      await get().load({ silent: true })
      return {
        ok: true,
        message: `已合并到 #${result.target.number}`,
        target: result.target,
      }
    } catch (error) {
      set({ merging: false })
      return { ok: false, message: toMessage(error, '合并失败，请稍后重试') }
    }
  },

  split: async (ticketId, payload) => {
    set({ splitting: true })
    try {
      const ticket = await splitTicket(ticketId, payload)
      set({ splitting: false })
      await get().load({ silent: true })
      return { ok: true, message: `已拆出新工单 #${ticket.number}`, ticket }
    } catch (error) {
      set({ splitting: false })
      return { ok: false, message: toMessage(error, '拆分失败，请稍后重试') }
    }
  },
}))
