// features/chat/constants/models.ts
export interface Model {
  id: string
  name: string
  description: string
  category: 'reasoning' | 'chat' | 'code'
}

export const CHAT_MODELS: Model[] = [
  {
    id: 'deepseek-chat',
    name: 'DeepSeek Chat',
    description: '速度快，适合日常对话与代码',
    category: 'chat',
  },
  {
    id: 'deepseek-reasoner',
    name: 'DeepSeek Reasoner',
    description: '深度思考模型，适合复杂逻辑推理',
    category: 'reasoning',
  },
]

export function getDefaultModel(): Model {
  return CHAT_MODELS[0]
}

export function getModelById(id: string): Model | undefined {
  return CHAT_MODELS.find((m) => m.id === id)
}
