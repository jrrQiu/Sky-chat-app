// features/chat/components/MessageContent/MarkdownComponents.tsx
import { CodeBlock } from './CodeBlock'
import type { Components } from 'react-markdown'

export function createMarkdownComponents(): Components {
  return {
    // 劫持所有的 <code> 标签
    code: ({ className, children }) => {
      // 如果 className 不存在，说明它是行内代码（没有 ``` 包裹的）
      const isInline = !className
      
      return (
        <CodeBlock inline={isInline} className={className}>
          {children}
        </CodeBlock>
      )
    },
    
    // 劫持 <a> 标签，让聊天里生成的链接全都在新标签页打开
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-blue-600 underline hover:text-blue-800 dark:text-blue-400"
      >
        {children}
      </a>
    ),
    
    // 劫持表格，给它加上 Tailwind 样式，防止太宽撑破气泡
    table: ({ children }) => (
      <div className="my-4 w-full overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="w-full text-left text-sm border-collapse">
          {children}
        </table>
      </div>
    ),
    th: ({ children }) => (
      <th className="border-b border-gray-200 bg-gray-100 dark:border-gray-700 dark:bg-gray-800 px-4 py-2 font-semibold">
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td className="border-b border-gray-200 dark:border-gray-700 px-4 py-2">
        {children}
      </td>
    ),
  }
}