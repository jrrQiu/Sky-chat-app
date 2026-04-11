// features/conversation/components/ConversationList/ConversationItem.tsx
//这个组件负责渲染单个会话项
'use client'

import { useState } from 'react'
import { MessageSquare, Trash2, Edit2, Check, X, MoreVertical } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Button } from '@/components/ui/button'
import type { ConversationData } from '@/app/actions/conversation'

interface ConversationItemProps {
  conversation: ConversationData
  isActive: boolean
  onClick: () => void
  onDelete: (id: string) => void
  onRename: (id: string, newTitle: string) => void
}

export function ConversationItem({
  conversation,
  isActive,
  onClick,
  onDelete,
  onRename,
}: ConversationItemProps) {
  const [isEditing, setIsEditing] = useState(false)
  const [editTitle, setEditTitle] = useState(conversation.title)

  // 处理保存标题
  const handleSave = () => {
    if (editTitle.trim() && editTitle !== conversation.title) {
      onRename(conversation.id, editTitle.trim())
    } else {
      setEditTitle(conversation.title) // 恢复原状
    }
    setIsEditing(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSave()
    if (e.key === 'Escape') {
      setEditTitle(conversation.title)
      setIsEditing(false)
    }
  }

  return (
    <div
      className={`group relative flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors ${
        isActive
          ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
          : 'text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800'
      }`}
    >
      {/* 1. 图标 */}
      <MessageSquare className="h-4 w-4 shrink-0 opacity-70" />

      {/* 2. 中间的内容区（展示标题或编辑框） */}
      {isEditing ? (
        <div className="flex flex-1 items-center gap-1">
          <input
            type="text"
            value={editTitle}
            onChange={(e) => setEditTitle(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleSave}
            autoFocus
            className="flex-1 bg-transparent border-b border-blue-500 outline-none px-1 text-sm text-gray-900 dark:text-white"
          />
          <button onClick={handleSave} className="text-green-600 hover:text-green-700 p-1">
            <Check className="h-3 w-3" />
          </button>
          <button 
            onClick={() => {
              setEditTitle(conversation.title)
              setIsEditing(false)
            }} 
            className="text-red-600 hover:text-red-700 p-1"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      ) : (
        <button
          onClick={onClick}
          className="truncate flex-1 font-medium text-left outline-none"
        >
          {conversation.title}
        </button>
      )}

      {/* 3. 右侧的下拉菜单（仅在悬浮或激活时显示） */}
      {!isEditing && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className={`h-6 w-6 shrink-0 opacity-0 group-hover:opacity-100 ${isActive ? 'opacity-100' : ''}`}
            >
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-32">
            <DropdownMenuItem onClick={() => setIsEditing(true)}>
              <Edit2 className="mr-2 h-4 w-4" />
              <span>重命名</span>
            </DropdownMenuItem>
            <DropdownMenuItem 
              onClick={() => onDelete(conversation.id)}
              className="text-red-600 focus:text-red-600 focus:bg-red-50 dark:focus:bg-red-950"
            >
              <Trash2 className="mr-2 h-4 w-4" />
              <span>删除会话</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}