// server/repositories/message.repository.ts
import { prisma } from '../db/client'

export const MessageRepository = {
  /**
   * 批量保存消息
   * 这在一次完整的对话（一问一答）结束后非常有用
   */
  async createMany(messages: Array<{
    id: string
    role: string
    content: string
    conversationId: string
  }>) {
    return prisma.message.createMany({
      data: messages,
      skipDuplicates: true, // 防止因为网络重试导致重复存入
    })
  },
  
  /**
   * 获取一个会话的所有历史消息（用于传给大模型做上下文）
   */
  async findByConversationId(conversationId: string) {
    return prisma.message.findMany({
      where: { conversationId },
      orderBy: { createdAt: 'asc' }, // 按时间正序排列
    })
  }
}