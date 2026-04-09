// server/services/chat/index.ts
import { createOpenAI } from '@ai-sdk/openai'
import { streamText } from 'ai'
import { nanoid } from 'nanoid'
import { MessagePersister } from './message-persister'

// 定义自定义错误类
export class NotFoundError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'NotFoundError'
  }
}

/**
 * 处理聊天请求的核心业务逻辑
 */
export async function handleChatRequest(
  userId: string,
  apiKey: string,
  body: any // 这里后续可以定义严格的 TS 接口
) {
  const { messages, conversationId: reqConversationId } = body

  // 1. 初始化模型客户端
  // Sky-Chat 默认使用的是 SiliconFlow 提供的接口（兼容 OpenAI 格式）
  const siliconflow = createOpenAI({
    baseURL: 'https://api.siliconflow.cn/v1',
    apiKey: apiKey,
  })

  // 2. 准备持久化所需的数据
  // 如果前端没有传 conversationId，说明是新开的对话，就生成一个新的
  const conversationId = reqConversationId || nanoid()
  const userMessageId = nanoid()
  const assistantMessageId = nanoid()
  // 提取用户发来的最新一条消息内容
  const latestUserMessage = messages[messages.length - 1].content
  const conversationTitle = '新对话'

  // 3. 调用模型生成流式文本
  // 注意：这里使用的是 Vercel AI SDK 提供的 streamText 方法
  const result = await streamText({
    model: siliconflow('deepseek-ai/DeepSeek-V3'), // 指定大模型
    messages: messages,
    temperature: 0.7,
    
    // ======== 生命周期的回调钩子 ========
    onFinish: async ({ text }) => {
      // text 是大模型最终生成的完整字符串
      
      // 异步触发持久化，把这一问一答悄悄存进数据库，不阻塞用户的流式响应
      await MessagePersister.persistCompleteTurn({
        userId,
        conversationId,
        userMessageId,
        userMessageContent: latestUserMessage,
        assistantMessageId,
        assistantMessageContent: text,
      })
    }
  })

  // 4. 构造返回值
  // 返回给外层 route.ts 的核心流，以及用于更新侧边栏的元数据
  const sessionId = nanoid()

  return {
    stream: result.toTextStreamResponse().body!, // 提取底层的 ReadableStream
    sessionId,
    conversationId,
    conversationTitle,
  }
}