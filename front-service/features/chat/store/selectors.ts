/**
 * Chat Store Selectors
 * 
 * 消息状态机相关的选择器
 */

import type { MessagePhase, ActiveTool } from '../types/message-state'

// 定义 Store 状态类型（避免循环依赖）
interface ChatStoreState {
  messageStates: Map<
    string,
    { phase: MessagePhase; activeTools: Map<string, ActiveTool>; errorMessage?: string }
  >
}

/** 阶段显示标签 */
const PHASE_LABELS: Record<MessagePhase, string> = {
  idle: '思考完成',
  thinking: '思考中',
  tool_calling: '工具调用中',
  answering: '回答中',
  error: '出错了',
}

/**
 * 获取消息阶段
 */
export const selectMessagePhase = (messageId: string) => 
  (state: ChatStoreState): MessagePhase => {
    return state.messageStates.get(messageId)?.phase ?? 'idle'
  }

/**
 * 获取消息活跃工具列表
 */
export const selectActiveTools = (messageId: string) => 
  (state: ChatStoreState): ActiveTool[] => {
    const msgState = state.messageStates.get(messageId)
    if (!msgState) return []
    return Array.from(msgState.activeTools.values())
  }

/**
 * 获取消息是否正在处理
 */
export const selectIsProcessing = (messageId: string) => 
  (state: ChatStoreState): boolean => {
    const phase = state.messageStates.get(messageId)?.phase
    return phase !== undefined && phase !== 'idle' && phase !== 'error'
  }

/**
 * 获取阶段显示文本
 */
export const selectPhaseLabel = (messageId: string) => 
  (state: ChatStoreState): string => {
    const phase = state.messageStates.get(messageId)?.phase ?? 'idle'
    return PHASE_LABELS[phase]
  }

/**
 * 获取是否有运行中的工具
 */
export const selectHasRunningTools = (messageId: string) => 
  (state: ChatStoreState): boolean => {
    const msgState = state.messageStates.get(messageId)
    if (!msgState) return false
    return Array.from(msgState.activeTools.values()).some(t => t.state === 'running')
  }

/**
 * 获取错误阶段带的服务端说明
 *
 * 界面用它替代固定的"生成失败"：服务端的文案里通常写着下一步该做什么
 * （"请重试；若刚才发生过写操作，请先确认结果再重试"）。没有时返回 undefined，
 * 由调用方回退到通用文案。
 */
export const selectMessageError = (messageId: string) =>
  (state: ChatStoreState): string | undefined => {
    return state.messageStates.get(messageId)?.errorMessage || undefined
  }