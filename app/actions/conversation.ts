// app/actions/conversation.ts
'use server' // 魔法指令：告诉 Next.js 这整个文件里的函数都在服务器端运行

import { getCurrentUserId } from '@/server/auth/utils'
import { ConversationRepository } from '@/server/repositories/conversation.repository'

// 定义前端需要的会话数据结构
export interface ConversationData {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  isPinned: boolean
}

// 统一的返回格式
export interface ActionResult<T = void> {
  success: boolean
  data?: T
  error?: string
}

/**
 * 获取当前登录用户的所有历史会话
 */
export async function getConversations(): Promise<ActionResult<ConversationData[]>> {
  try {
    const userId = await getCurrentUserId()
    const conversations = await ConversationRepository.findByUserId(userId)
    
    // 必须把 Prisma 返回的 Date 对象转成 ISO 字符串，否则无法传给前端
    const data = conversations.map((c) => ({
      id: c.id,
      title: c.title,
      createdAt: c.createdAt.toISOString(),
      updatedAt: c.updatedAt.toISOString(),
      isPinned: c.isPinned || false,
    }))
    
    return { success: true, data }
  } catch (error) {
    return { success: false, error: '获取会话失败或未登录' }
  }
}

/**
 * 创建新会话
 */
export async function createConversation(title?: string): Promise<ActionResult<ConversationData>> {
  try {
    const userId = await getCurrentUserId()
    console.log('当前准备建会话的 userId 是:', userId)
    const conversation = await ConversationRepository.create(userId, title || '新对话')

    return {
      success: true,
      data: {
        id: conversation.id,
        title: conversation.title,
        createdAt: conversation.createdAt.toISOString(),
        updatedAt: conversation.updatedAt.toISOString(),
        isPinned: conversation.isPinned || false,
      },
    }
  } catch (error) {
    console.error('新建会话真实报错原因:', error.message || error)
    return { success: false, error: '创建会话失败' }
  }
}

/**
 * 删除会话
 */
export async function deleteConversation(id: string): Promise<ActionResult> {
  try {
    const userId = await getCurrentUserId()
    const success = await ConversationRepository.delete(id, userId)
    if (!success) return { success: false, error: '会话不存在或无权限' }
    return { success: true }
  } catch (error) {
    return { success: false, error: '删除会话失败' }
  }
}

/**
 * 更新会话标题
 */
export async function updateConversationTitle(id: string, title: string): Promise<ActionResult> {
  try {
    if (!title.trim()) return { success: false, error: '标题不能为空' }
    const userId = await getCurrentUserId()
    const success = await ConversationRepository.updateTitle(id, userId, title)
    if (!success) return { success: false, error: '更新失败' }
    return { success: true }
  } catch (error) {
    return { success: false, error: '系统错误' }
  }
}