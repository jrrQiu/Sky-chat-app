// app/api/chat/route.ts
/**
 * Chat API Route
 * 
 * 路由层：只负责请求校验和响应格式
 * 业务逻辑委托给 ChatService (handleChatRequest)
 */

import { getCurrentUserId } from '@/server/auth/utils'
import { UserRepository } from '@/server/repositories/user.repository'
import { handleChatRequest, NotFoundError } from '@/server/services/chat'

export async function POST(req: Request) {
  // 1. 认证校验 (判断是谁在发消息)
  let userId: string
  try {
    userId = await getCurrentUserId()
  } catch {
    return Response.json({ error: 'Unauthorized' }, { status: 401 })
  }

  // 2. 获取用户和 API Key
  const user = await UserRepository.findById(userId)
  if (!user) {
    return Response.json({ error: 'User not found' }, { status: 404 })
  }

  // 优先使用用户自己填写的 API Key，如果没有，降级使用环境变量里的系统 Key
  const apiKey = user.apiKey || process.env.SILICONFLOW_API_KEY || process.env.OPENAI_API_KEY
  if (!apiKey) {
     console.error('【400 错误】: 未配置 API Key')
    return Response.json(
      { error: '未配置 API Key。请在个人资料中设置您的 SiliconFlow API Key，或联系管理员。' },
      { status: 400 }
    )
  }

  // 3. 解析请求体 (前端 fetch 过来的 JSON)
  const body = await req.json()
  
  // 取出最新的一条消息，校验是否为空
  const latestMessageContent = body.messages?.[body.messages.length - 1]?.content
  if (!latestMessageContent?.trim()) {
    console.error('【400 错误】: 消息内容为空，前端发来的数据是:', JSON.stringify(body))
    return Response.json({ error: '消息内容不能为空' }, { status: 400 })
  }

  // 4. 调用核心服务处理请求 (这里会返回 ReadableStream)
  try {
    const { stream, sessionId, conversationId, conversationTitle } = await handleChatRequest(
      userId,
      apiKey,
      body
    )

    // 5. 将流返回给前端，并设置 SSE 必需的 Header！
    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream', // 告诉浏览器：这是一根水管，别急着关！
        'Cache-Control': 'no-cache',         // 不要缓存中间结果
        Connection: 'keep-alive',            // 保持连接
        // 把会话的元数据塞进 Header 里，前端接流的时候顺便能拿到
        'X-Session-ID': sessionId,
        'X-Conversation-ID': conversationId,
        'X-Conversation-Title': encodeURIComponent(conversationTitle),
      },
    })
  } catch (error) {
    // 统一的错误处理兜底
    if (error instanceof NotFoundError) {
      return Response.json({ error: error.message }, { status: 404 })
    }

    const errorMessage = error instanceof Error ? error.message : 'Unknown error'
    console.error('Chat API 发生错误:', errorMessage)
    return Response.json({ error: errorMessage }, { status: 500 })
  }
}