// app/api/conversations/[id]/messages/route.ts
import { NextResponse } from 'next/server'
import { getCurrentUserId } from '@/server/auth/utils'
import { ConversationRepository } from '@/server/repositories/conversation.repository'
import { MessageRepository } from '@/server/repositories/message.repository'

export async function GET(
  req: Request,
  { params }: { params: { id: string } }
) {
  try {
    // 1. 验证登录状态
    let userId: string
    try {
      userId = await getCurrentUserId()
    } catch {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
    }

    const { id: conversationId } = params

    // 2. 检查这个会话是否存在，以及是不是这个用户的
    // （极其重要：防止黑客遍历抓取别人的聊天记录！）
    const conversation = await ConversationRepository.findById(conversationId, userId)

    if (!conversation) {
      return NextResponse.json({ error: '找不到该会话' }, { status: 404 })
    }

    // 3. 从数据库捞出所有消息
    const messages = await MessageRepository.findByConversationId(conversationId)

    // 4. 格式化并返回给前端
    return NextResponse.json({
      messages: messages.map((msg) => ({
        id: msg.id,
        role: msg.role,
        content: msg.content,
        conversationId: msg.conversationId,
        createdAt: msg.createdAt.getTime(),
      })),
    })
  } catch (error) {
    console.error('获取历史消息失败:', error)
    return NextResponse.json({ error: '服务器内部错误' }, { status: 500 })
  }
}