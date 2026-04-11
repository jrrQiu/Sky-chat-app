// server/repositories/conversation.repository.ts
import { prisma } from '../db/client'

export const ConversationRepository = {
  /**
   * 创建新会话
   */
  async create(userId: string, title = '新对话') {
    return prisma.conversation.create({
      data: { userId, title },
    })
  },

  /**
   * 根据 ID 查找会话，并验证是否属于该用户
   */
  async findById(id: string, userId?: string) {
    if (userId) {
      return prisma.conversation.findFirst({
        where: { id, userId },
      })
    }
    return prisma.conversation.findUnique({
      where: { id },
    })
  },

  /**
   * 更新会话的标题和最后更新时间
   */
  async update(id: string, data: { title?: string; updatedAt?: Date }) {
    return prisma.conversation.update({
      where: { id },
      data,
    })
  },

  async findByUserId(userId: string) {
    return prisma.conversation.findMany({
      where: { userId },
      orderBy: [
        { isPinned: 'desc' },  // 置顶的排前面
        { updatedAt: 'desc' }, // 最近聊过的排前面
      ],
    })
  },
  /**
   * 更新会话标题
   */
  async updateTitle(id: string, userId: string, title: string) {
    const result = await prisma.conversation.updateMany({
      where: { id, userId },
      data: { title, updatedAt: new Date() },
    })
    return result.count > 0
  },

  /**
   * 删除会话
   */
  async delete(id: string, userId: string) {
    const result = await prisma.conversation.deleteMany({
      where: { id, userId },
    })
    return result.count > 0
  },

  /**
   * 置顶/取消置顶会话
   */
  async togglePin(id: string, userId: string, isPinned: boolean) {
    const result = await prisma.conversation.updateMany({
      where: { id, userId },
      data: {
        isPinned,
        pinnedAt: isPinned ? new Date() : null,
      },
    })
    return result.count > 0
  },
}