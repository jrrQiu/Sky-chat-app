// features/chat/components/ChatInput/ChatInputUI.tsx
'use client'

import { useCallback, useRef, useState } from 'react'
import { Mic, Loader2, Brain, Square, X, FileUp, Paintbrush, Search, ArrowUp } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { MarkdownIcon } from '@/components/icons/MarkdownIcon'
import { TextFileIcon } from '@/components/icons/TextFileIcon'
import { cn } from '@/lib/utils'

// 定义它需要接收的所有外部参数
export interface ChatInputUIProps {
  input: string
  setInput: (value: string) => void
  selectedModel: string
  enableThinking: boolean
  enableWebSearch: boolean
  isLoading: boolean
  isRecording: boolean
  isTranscribing: boolean
  uploadedFiles: Array<{ name: string; size: number; type: 'txt' | 'md' }>

  onSubmit: (e: React.FormEvent) => void
  onStop: () => void
  onThinkingToggle: (enabled: boolean) => void
  onWebSearchToggle: (enabled: boolean) => void
  onStartRecording: () => void
  onStopRecording: () => void
  onCancelRecording: () => void
  onFileUpload: (e: React.ChangeEvent<HTMLInputElement>) => void
  onRemoveFile: (index: number) => void
  onImageGenerate?: () => void
}

export function ChatInputUI({
  input,
  setInput,
  selectedModel,
  enableThinking,
  enableWebSearch,
  isLoading,
  isRecording,
  isTranscribing,
  uploadedFiles,
  onSubmit,
  onStop,
  onThinkingToggle,
  onWebSearchToggle,
  onStartRecording,
  onStopRecording,
  onCancelRecording,
  onFileUpload,
  onRemoveFile,
  onImageGenerate,
}: ChatInputUIProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const disabled = isLoading || isRecording || isTranscribing
  const canSend = input.trim() && !isRecording && !isTranscribing

  // ====== 拖拽相关逻辑 ======
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setIsDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setIsDragging(false)
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setIsDragging(false)
    const files = e.dataTransfer.files
    if (files.length > 0 && fileInputRef.current) {
      const dataTransfer = new DataTransfer()
      dataTransfer.items.add(files[0])
      fileInputRef.current.files = dataTransfer.files
      const event = new Event('change', { bubbles: true })
      fileInputRef.current.dispatchEvent(event)
    }
  }, [])

  // ====== 渲染图标 ======
  const getFileIcon = (type: 'txt' | 'md') => {
    return type === 'md' 
      ? <MarkdownIcon className="h-3.5 w-3.5 text-orange-500" />
      : <TextFileIcon className="h-3.5 w-3.5 text-blue-500" />
  }

  return (
    <div
      className={cn("shrink-0 transition-colors relative w-full", isDragging && "bg-blue-50 dark:bg-blue-900/10")}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* === 1. 拖拽浮层 === */}
      {isDragging && (
        <div className="absolute inset-0 flex items-center justify-center bg-blue-50/90 dark:bg-blue-900/20 backdrop-blur-sm z-10 rounded-3xl">
          <div className="text-center">
            <FileUp className="h-12 w-12 text-blue-500 mx-auto mb-2" />
            <p className="text-sm font-medium text-blue-600">拖放文件到这里解析 (.txt, .md)</p>
          </div>
        </div>
      )}

      <div className="mx-auto max-w-4xl px-4 py-4">
        
        {/* === 2. 录音状态栏 === */}
        {isRecording && (
          <div className="mb-4 bg-red-50 dark:bg-red-950/30 rounded-2xl p-4 border border-red-200 shadow-lg flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-red-500 rounded-full flex items-center justify-center animate-pulse">
                <Mic className="h-5 w-5 text-white" />
              </div>
              <div>
                <p className="text-sm font-medium text-red-900">正在录音...</p>
                <p className="text-xs text-red-600">点击停止完成录音，或点击取消</p>
              </div>
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={onCancelRecording} className="text-red-600">
                <X className="h-4 w-4 mr-1" /> 取消
              </Button>
              <Button size="sm" onClick={onStopRecording} className="bg-red-500 hover:bg-red-600 text-white">
                <Square className="h-4 w-4 mr-1 fill-current" /> 停止
              </Button>
            </div>
          </div>
        )}

        {/* === 3. 主输入框区域 === */}
        <div className="bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-3xl p-3 shadow-sm focus-within:ring-1 focus-within:ring-blue-500 transition-all">
          
          {/* 上半部分：已上传文件列表 */}
          {uploadedFiles.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-2 px-2">
              {uploadedFiles.map((file, idx) => (
                <div key={idx} className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm bg-blue-50 border border-blue-200 dark:bg-blue-900/20 dark:border-blue-800">
                  {getFileIcon(file.type)}
                  <span className="max-w-[150px] truncate text-blue-700 dark:text-blue-300 font-medium">{file.name}</span>
                  <button onClick={() => onRemoveFile(idx)} className="text-blue-600 hover:opacity-70">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* 中间部分：输入框 */}
          <form onSubmit={onSubmit} className="relative mb-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={isRecording ? '正在录音...' : isTranscribing ? '正在转录...' : '发消息、查资料、或者拖拽文本文件...'}
              disabled={disabled}
              className="w-full h-12 bg-transparent rounded-3xl px-3 text-[15px] outline-none placeholder:text-gray-400"
              autoComplete="off"
            />
          </form>

          {/* 下半部分：工具栏 */}
          <div className="flex items-center justify-between px-1">
            
            {/* 左侧工具：上传、思考、生图、搜索 */}
            <div className="flex items-center gap-1">
              <input ref={fileInputRef} type="file" accept=".txt,.md" onChange={onFileUpload} className="hidden" />
              
              <Button variant="ghost" size="icon" className="h-8 w-8 rounded-lg text-gray-500 hover:text-gray-800" onClick={() => fileInputRef.current?.click()} disabled={disabled} title="上传文本文件">
                <FileUp className="h-4 w-4" />
              </Button>

              <Button variant="ghost" size="icon" onClick={() => onThinkingToggle(!enableThinking)} disabled={disabled} className={cn('h-8 w-8 rounded-lg', enableThinking ? 'bg-blue-500 text-white hover:bg-blue-600' : 'text-gray-500 hover:text-gray-800')} title="深度思考模式">
                <Brain className="h-4 w-4" />
              </Button>

              <Button variant="ghost" size="icon" onClick={() => onWebSearchToggle(!enableWebSearch)} disabled={disabled} className={cn('h-8 w-8 rounded-lg', enableWebSearch ? 'bg-blue-500 text-white hover:bg-blue-600' : 'text-gray-500 hover:text-gray-800')} title="联网搜索">
                <Search className="h-4 w-4" />
              </Button>
            </div>

            {/* 右侧工具：语音、发送 */}
            <div className="flex items-center gap-2">
              {!isLoading && (
                <Button variant="ghost" size="icon" onClick={isRecording ? onStopRecording : onStartRecording} disabled={isTranscribing} className={cn('h-8 w-8 rounded-lg', isRecording ? 'text-red-500 bg-red-50' : 'text-gray-500')} title="语音输入">
                  {isTranscribing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mic className="h-4 w-4" />}
                </Button>
              )}

              {isLoading ? (
                <Button size="icon" variant="destructive" onClick={onStop} className="h-8 w-8 rounded-full" title="停止生成">
                  <Square className="h-3.5 w-3.5 fill-current" />
                </Button>
              ) : (
                <Button size="icon" onClick={onSubmit} disabled={!canSend} className="h-8 w-8 rounded-full bg-black hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-gray-200" title="发送">
                  <ArrowUp className="h-4 w-4" />
                </Button>
              )}
            </div>
            
          </div>
        </div>

      </div>
    </div>
  )
}