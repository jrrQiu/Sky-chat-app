// features/chat/components/ChatInput/index.tsx
'use client'

import { useChatStore } from '@/features/chat/store/chat.store'
import { useChatInput } from './use-chat-input'
import { ChatInputUI } from './ChatInputUI'

interface ChatInputProps {
  conversationId?: string
}

export function ChatInput({ conversationId }: ChatInputProps) {
  // 1. 从 Store 取出全局状态
  const selectedModel = useChatStore((s) => s.selectedModel)
  const enableThinking = useChatStore((s) => s.enableThinking)
  const enableWebSearch = useChatStore((s) => s.enableWebSearch)
  
  const setModel = useChatStore((s) => s.setModel)
  const toggleThinking = useChatStore((s) => s.toggleThinking)
  const toggleWebSearch = useChatStore((s) => s.toggleWebSearch)
  
  // 2. 从我们刚才写的自定义 Hook 获取逻辑和局部状态
  const {
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
  } = useChatInput(conversationId)

  // 3. 把它们全部灌入我们刚才写的 UI 壳子中！
  return (
    <ChatInputUI
      input={input}
      setInput={setInput}
      selectedModel={selectedModel}
      enableThinking={enableThinking}
      enableWebSearch={enableWebSearch}
      isLoading={isSendingMessage}
      isRecording={isRecording}
      isTranscribing={isTranscribing}
      uploadedFiles={uploadedFiles}
      
      onSubmit={handleSubmit}
      onStop={handleStop}
      _onModelChange={setModel}
      onThinkingToggle={toggleThinking}
      onWebSearchToggle={toggleWebSearch}
      onStartRecording={startRecording}
      onStopRecording={stopRecording}
      onCancelRecording={cancelRecording}
      onFileUpload={handleFileUpload}
      onRemoveFile={handleRemoveFile}
    />
  )
}