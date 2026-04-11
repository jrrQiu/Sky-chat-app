// server/services/chat/index.ts
import { createOpenAI } from '@ai-sdk/openai'
import { streamText } from 'ai'
import { nanoid } from 'nanoid'
import { MessagePersister } from './message-persister'
import { generateText } from 'ai'
import { ConversationRepository } from '@/server/repositories/conversation.repository'

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
    baseURL: process.env.OPENAI_BASE_URL || 'https://api.openai.com/v1', 
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
    model: siliconflow('meta-llama/llama-3.2-3b-instruct:free'), // 指定大模型
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

      // 2. 检查是不是第一轮对话？
      // 如果 messages 长度只有 1，说明这是新会话的第一条消息！
      if (messages.length === 1) {
        // 异步去生成标题，并把生成好的标题通过 Server-Sent Events 发给前端
        generateTitle(userId, apiKey, conversationId, latestUserMessage).then((newTitle) => {
          if (newTitle) {
            // 真实项目中，如果有长连接通道，这里会推送给前端
            // 在我们的精简版里，数据库已经更新了，前端只需在合适的时候刷新列表即可
          }
        })
      }
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

/**
 * 自动为会话生成精简标题
 */
export async function generateTitle(
  userId: string,
  apiKey: string,
  conversationId: string,
  firstUserMessage: string
) {
  try {
    const siliconflow = createOpenAI({
      baseURL: 'https://api.siliconflow.cn/v1',
      apiKey: apiKey,
    })

    // 请求大模型，让它总结标题（不需要流式，直接等结果 generateText）
    const { text } = await generateText({
      model: siliconflow('deepseek-ai/DeepSeek-V3'),
      system: '你是一个擅长总结核心词汇的助手。请根据用户的输入，总结出一个不超过10个字的极简标题，不要带引号或任何标点符号。',
      prompt: firstUserMessage,
      temperature: 0.3, // 稍微低一点，让它稳重些
    })

    const title = text.trim()
    if (!title) return null

    // 更新到数据库
    await ConversationRepository.updateTitle(conversationId, userId, title)
    
    console.log(`[AutoTitle] 会话 ${conversationId} 自动命名为: ${title}`)
    return title

  } catch (error) {
    console.error('[AutoTitle] 自动生成标题失败:', error)
    return null
  }
}