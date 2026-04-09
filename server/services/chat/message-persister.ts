// server/services/chat/message-persister.ts
//负责在“大模型流式输出完毕”的时候，触发数据库保存操作。
import { ConversationRepository } from '@/server/repositories/conversation.repository'
import { MessageRepository } from '@/server/repositories/message.repository'

interface PersistOptions {
  userId: string
  conversationId: string
  userMessageId: string
  userMessageContent: string
  assistantMessageId: string
  assistantMessageContent: string
}

export class MessagePersister {
  /**
   * 将一轮完整的对话（用户提问 + AI 回答）保存到数据库
   */
  static async persistCompleteTurn(options: PersistOptions) {
    const {
      userId,
      conversationId,
      userMessageId,
      userMessageContent,
      assistantMessageId,
      assistantMessageContent,
    } = options

    try {
      // 1. 检查会话是否存在，不存在则创建
      let conversation = await ConversationRepository.findById(conversationId, userId)
      if (!conversation) {
        conversation = await ConversationRepository.create(userId)
      }

      // 2. 准备要存入数据库的消息数组
      const messagesToSave = [
        {
          id: userMessageId,
          role: 'user',
          content: userMessageContent,
          conversationId: conversation.id,
        },
        {
          id: assistantMessageId,
          role: 'assistant',
          content: assistantMessageContent,
          conversationId: conversation.id,
        },
      ]

      // 3. 批量写入数据库
      await MessageRepository.createMany(messagesToSave)

      // 4. 更新会话的最后活跃时间
      await ConversationRepository.update(conversation.id, {
        updatedAt: new Date(),
      })

      console.log(`[Persister] 成功保存会话 ${conversationId} 的消息`)
    } catch (error) {
      // 这里的错误不能阻断用户的流式体验，只打印日志
      console.error('[Persister] 保存消息失败:', error)
    }
  }
}