// features/chat/components/ChatInput/use-chat-input.ts
import { useCallback, useState } from 'react'
import { useChatStore } from '@/features/chat/store/chat.store'
import { ChatService } from '@/features/chat/services/chat.service'

export function useChatInput(conversationId?: string) {
  // 1. 本地状态
  const [input, setInput] = useState('')
  const [uploadedFiles, setUploadedFiles] = useState<Array<{ name: string; size: number; type: 'txt' | 'md', content: string }>>([])
  
  // 这两个是为了后续接语音识别预留的状态
  const [isRecording, setIsRecording] = useState(false)
  const [isTranscribing, setIsTranscribing] = useState(false)

  // 2. 从全局 Store 拿发送状态
  const isSendingMessage = useChatStore((s) => s.isSendingMessage)

  // 3. 处理发送消息
  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault()
      
      // 如果没有文字也没文件，或者正在发送，直接返回
      if ((!input.trim() && uploadedFiles.length === 0) || isSendingMessage) return

      // 如果有附件，把附件内容拼接到消息最前面发给 AI
      let finalMessage = input.trim()
      if (uploadedFiles.length > 0) {
        const filesContent = uploadedFiles.map(f => `【附件：${f.name}】\n${f.content}\n`).join('\n')
        finalMessage = `${filesContent}\n用户留言：${finalMessage}`
      }

      // 清空输入框和附件
      setInput('')
      setUploadedFiles([])

      // 调用发消息服务
      await ChatService.sendMessage(finalMessage, conversationId)
    },
    [input, isSendingMessage, uploadedFiles, conversationId]
  )

  // 4. 处理停止生成
  const handleStop = useCallback(() => {
    ChatService.abortStream()
  }, [])

  // 5. 处理文件读取 (把 txt/md 文件读成纯文本)
  const handleFileUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    // 每次传完清空 input 的 value，允许重复传同一个文件
    e.target.value = ''

    // 限制大小 1MB
    if (file.size > 1024 * 1024) {
      alert('文件不能超过 1MB')
      return
    }

    const type = file.name.endsWith('.md') ? 'md' : 'txt'
    
    // 使用 FileReader 纯前端读取文件内容！不需要上传到服务器！
    const reader = new FileReader()
    reader.onload = (event) => {
      const content = event.target?.result as string
      setUploadedFiles(prev => [...prev, {
        name: file.name,
        size: file.size,
        type,
        content
      }])
    }
    reader.readAsText(file)
  }, [])

  // 6. 移除已选文件
  const handleRemoveFile = useCallback((index: number) => {
    setUploadedFiles(prev => prev.filter((_, i) => i !== index))
  }, [])

  // 7. 语音录制相关（占位）
  const startRecording = () => { alert('语音识别模块即将接入！') }
  const stopRecording = () => {}
  const cancelRecording = () => {}

  return {
    input,
    setInput,
    isSendingMessage,
    isRecording,
    isTranscribing,
    uploadedFiles,
    handleSubmit,
    handleStop,
    handleFileUpload,
    handleRemoveFile,
    startRecording,
    stopRecording,
    cancelRecording,
  }
}