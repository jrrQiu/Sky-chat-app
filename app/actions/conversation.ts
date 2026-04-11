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