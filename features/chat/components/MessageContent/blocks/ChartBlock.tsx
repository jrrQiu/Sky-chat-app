// features/chat/components/MessageContent/blocks/ChartBlock.tsx
'use client'

import { useMemo } from 'react'
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts'

export interface MediaBlockProps {
  data: string      // 大模型吐出来的 JSON 字符串
  isStreaming?: boolean // 是否还在一个字一个字蹦
}

interface ChartData {
  type: 'bar' | 'line'
  title?: string
  labels: string[]
  values: number[]
}

export function ChartBlock({ data, isStreaming }: MediaBlockProps) {
  // 1. 尝试解析 JSON
  const chartData = useMemo(() => {
    try {
      return JSON.parse(data) as ChartData
    } catch {
      return null // 流式输出一半时，JSON 是残缺的，肯定会解析失败
    }
  }, [data])

  // 2. 如果还在流式输出，且 JSON 还不完整，显示一个酷炫的骨架屏
  if (!chartData || !chartData.labels || !chartData.values) {
    if (isStreaming) {
      return (
        <div className="my-4 overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="border-b border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 px-4 py-2">
            <span className="text-sm font-medium text-gray-500">图表生成中...</span>
          </div>
          <div className="flex h-[250px] items-center justify-center animate-pulse bg-gray-100 dark:bg-gray-800/50" />
        </div>
      )
    }
    return <div className="my-4 p-4 text-red-500 bg-red-50 rounded-lg">无法解析图表数据</div>
  }

  // 3. 把数据转成 recharts 需要的格式
  const formattedData = chartData.labels.map((label, index) => ({
    name: label,
    value: chartData.values[index] ?? 0,
  }))

  const isBar = chartData.type !== 'line'

  // 4. 渲染真实图表！
  return (
    <div className="my-4 overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
      <div className="border-b border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/50 px-4 py-3 flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-700 dark:text-gray-300">
          {chartData.title || '数据图表'}
        </span>
        <span className="text-xs text-gray-400 bg-white dark:bg-gray-800 px-2 py-1 rounded border dark:border-gray-700">
          交互式图表
        </span>
      </div>
      
      <div className="h-[300px] w-full p-4 pt-6">
        <ResponsiveContainer width="100%" height="100%">
          {isBar ? (
            <BarChart data={formattedData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
              <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 12 }} dy={10} />
              <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12 }} dx={-10} />
              <Tooltip 
                cursor={{ fill: 'rgba(0,0,0,0.05)' }}
                contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
              />
              <Bar dataKey="value" fill="#3b82f6" radius={[4, 4, 0, 0]} maxBarSize={50} />
            </BarChart>
          ) : (
            <LineChart data={formattedData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e5e7eb" />
              <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 12 }} dy={10} />
              <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12 }} dx={-10} />
              <Tooltip 
                contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
              />
              <Line type="monotone" dataKey="value" stroke="#3b82f6" strokeWidth={3} dot={{ r: 4, strokeWidth: 2 }} activeDot={{ r: 6 }} />
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
    </div>
  )
}