// features/tickets/services/ticket.service.ts
// 工单接口：每个函数对应一个后端端点，错误码由调用方（store）映射成中文。
import { apiJson } from '@/lib/api/client'
import type {
  AddTicketCommentRequest,
  AddTicketLinkRequest,
  CreateTicketRequest,
  MergeTicketResponse,
  SplitTicketRequest,
  TicketDetail,
  TicketListParams,
  TicketMeta,
  TicketPage,
  TransitionTicketRequest,
  UpdateTicketRequest,
} from '@/features/tickets/types'

/** 列表查询：只拼接调用方显式给出的筛选条件，未给出的交给服务端默认。 */
export function listTickets(params: TicketListParams = {}): Promise<TicketPage> {
  const search = new URLSearchParams()

  if (params.status) search.set('status', params.status)
  if (params.type) search.set('type', params.type)
  if (params.assignee) search.set('assignee', params.assignee)
  if (params.requester) search.set('requester', params.requester)
  if (params.query) search.set('query', params.query)
  search.set('page', String(params.page ?? 0))
  search.set('size', String(params.size ?? 20))

  return apiJson<TicketPage>(`/v1/tickets?${search.toString()}`)
}

/** 枚举 + 状态机 + 处理人角色，界面所有可选项都来自这里。 */
export function getTicketMeta(): Promise<TicketMeta> {
  return apiJson<TicketMeta>('/v1/tickets/meta')
}

export function createTicket(payload: CreateTicketRequest): Promise<TicketDetail> {
  return apiJson<TicketDetail>('/v1/tickets', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getTicket(ticketId: string): Promise<TicketDetail> {
  return apiJson<TicketDetail>(`/v1/tickets/${encodeURIComponent(ticketId)}`)
}

export function updateTicket(
  ticketId: string,
  payload: UpdateTicketRequest
): Promise<TicketDetail> {
  return apiJson<TicketDetail>(`/v1/tickets/${encodeURIComponent(ticketId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

/**
 * 状态流转。
 *
 * 服务端校验状态机；界面只提交 meta.transitions 给出的目标状态，
 * 但仍可能因为并发（别人先改了一步）失败，由 store 映射 INVALID_TRANSITION。
 */
export function transitionTicket(
  ticketId: string,
  payload: TransitionTicketRequest
): Promise<TicketDetail> {
  return apiJson<TicketDetail>(
    `/v1/tickets/${encodeURIComponent(ticketId)}/transition`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    }
  )
}

/** 评论不可编辑也不可删除；服务端按可见性过滤，非处理人拿不到内部备注。 */
export function addTicketComment(
  ticketId: string,
  payload: AddTicketCommentRequest
): Promise<TicketDetail> {
  return apiJson<TicketDetail>(
    `/v1/tickets/${encodeURIComponent(ticketId)}/comments`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    }
  )
}

export function addTicketLink(
  ticketId: string,
  payload: AddTicketLinkRequest
): Promise<TicketDetail> {
  return apiJson<TicketDetail>(`/v1/tickets/${encodeURIComponent(ticketId)}/links`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function removeTicketLink(
  ticketId: string,
  linkId: string
): Promise<TicketDetail> {
  return apiJson<TicketDetail>(
    `/v1/tickets/${encodeURIComponent(ticketId)}/links/${encodeURIComponent(linkId)}`,
    { method: 'DELETE' }
  )
}

export function mergeTicket(
  ticketId: string,
  targetId: string
): Promise<MergeTicketResponse> {
  return apiJson<MergeTicketResponse>(
    `/v1/tickets/${encodeURIComponent(ticketId)}/merge`,
    {
      method: 'POST',
      body: JSON.stringify({ targetId }),
    }
  )
}

/** 从某条评论拆出新工单；返回的是新工单详情。 */
export function splitTicket(
  ticketId: string,
  payload: SplitTicketRequest
): Promise<TicketDetail> {
  return apiJson<TicketDetail>(
    `/v1/tickets/${encodeURIComponent(ticketId)}/split`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    }
  )
}
