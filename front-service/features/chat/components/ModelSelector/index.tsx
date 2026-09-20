// features/chat/components/ModelSelector/index.tsx
'use client'

import { ChevronDown, Check } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { CHAT_MODELS } from '../../constants/models'

interface ModelSelectorProps {
  value: string
  onChange: (modelId: string) => void
}

export function ModelSelector({ value, onChange }: ModelSelectorProps) {
  const currentModel = CHAT_MODELS.find(m => m.id === value) || CHAT_MODELS[0]

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800 transition-colors outline-none">
        {currentModel.name}
        <ChevronDown className="h-4 w-4 opacity-50" />
      </DropdownMenuTrigger>
      
      <DropdownMenuContent align="start" className="w-56 bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-800">
        {CHAT_MODELS.map((model) => (
          <DropdownMenuItem
            key={model.id}
            onClick={() => onChange(model.id)}
            className="flex flex-col items-start gap-1 p-3 cursor-pointer focus:bg-gray-100 dark:focus:bg-gray-800"
          >
            <div className="flex w-full items-center justify-between">
              <span className="font-medium">{model.name}</span>
              {model.id === value && <Check className="h-4 w-4 text-blue-500" />}
            </div>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {model.description}
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}