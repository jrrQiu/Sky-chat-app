'use client'

import { useState } from 'react'
import { Copy, Check, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface MessageActionsProps {
  content: string
  messageId: string
  role: 'user' | 'assistant'
  hasError?: boolean
  onRetry?: () => void
}

export function MessageActions({ content, role, hasError, onRetry }: MessageActionsProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    if (!content) return
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      console.error('Failed to copy text: ', err)
    }
  }

  // 只有 AI 消息才显示操作栏
  if (role !== 'assistant') return null

  return (
    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
      <Button
        variant="ghost"
        size="icon"
        onClick={handleCopy}
        className={cn(
          "h-7 w-7 hover:bg-gray-100 dark:hover:bg-gray-800",
          copied && "text-green-500 hover:text-green-600"
        )}
        title="复制内容"
      >
        {copied ? (
          <Check className="h-3.5 w-3.5" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </Button>

      {onRetry && (
        <Button
          variant="ghost"
          size="icon"
          onClick={onRetry}
          className="h-7 w-7 hover:bg-gray-100 dark:hover:bg-gray-800"
          title={hasError ? "重试" : "重新生成"}
        >
          <RotateCw className={cn("h-3.5 w-3.5", hasError && "text-red-500")} />
        </Button>
      )}
    </div>
  )
}