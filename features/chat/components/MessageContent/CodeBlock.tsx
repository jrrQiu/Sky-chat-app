// features/chat/components/MessageContent/CodeBlock.tsx
'use client'

import { CopyButton } from './CopyButton'

interface CodeBlockProps {
  inline?: boolean
  className?: string
  children: React.ReactNode
}

export function CodeBlock({ inline, className, children }: CodeBlockProps) {
  // 从 className 中提取出语言名字，比如 "language-python" 提取出 "python"
  const match = /language-(\w+)/.exec(className || '')
  const language = match ? match[1] : ''
  const code = String(children).replace(/\n$/, '')

  // 如果是行内代码（比如 `npm install`），不需要复制按钮，直接返回灰色小框
  if (inline) {
    return (
      <code className="rounded bg-gray-200 dark:bg-gray-700 px-1.5 py-0.5 text-sm font-mono text-red-600 dark:text-red-400">
        {children}
      </code>
    )
  }

  // 如果是多行代码块
  return (
    <div className="group relative my-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 overflow-hidden">
      {/* 顶部语言提示和复制按钮栏 */}
      <div className="flex items-center justify-between bg-gray-200/50 dark:bg-gray-800 px-4 py-1.5 border-b border-gray-200 dark:border-gray-700">
        <span className="text-xs font-mono text-gray-500 dark:text-gray-400">
          {language || 'text'}
        </span>
        <div className="opacity-0 group-hover:opacity-100 transition-opacity duration-200">
          <CopyButton text={code} />
        </div>
      </div>

      {/* 真正的代码内容区 */}
      <pre className="overflow-x-auto p-4 text-sm font-mono leading-relaxed">
        <code className={className}>{children}</code>
      </pre>
    </div>
  )
}