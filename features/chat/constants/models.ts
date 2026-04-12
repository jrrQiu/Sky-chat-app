// features/chat/constants/models.ts
export interface Model {
  id: string
  name: string
  description: string
  category: 'reasoning' | 'chat' | 'code'
}

export const CHAT_MODELS: Model[] = [
  {
    id: 'deepseek-ai/DeepSeek-V3',
    name: 'DeepSeek V3',
    description: '速度快，适合日常对话与代码',
    category: 'chat',
  },
  {
    id: 'deepseek-ai/DeepSeek-R1',
    name: 'DeepSeek R1',
    description: '深度思考模型，适合复杂逻辑推理',
    category: 'reasoning',
  },
  {
    id: 'Qwen/Qwen2.5-72B-Instruct',
    name: '通义千问 2.5',
    description: '中文能力优秀的开源模型',
    category: 'chat',
  }
]

export function getDefaultModel(): Model {
  return CHAT_MODELS[0]
}