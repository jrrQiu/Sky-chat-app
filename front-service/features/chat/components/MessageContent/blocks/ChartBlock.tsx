'use client'

import { useState, useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { Loader2, AlertCircle } from 'lucide-react'

interface ChartBlockProps {
  data: string
  isStreaming?: boolean
}

export function ChartBlock({ data, isStreaming }: ChartBlockProps) {
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInstance = useRef<echarts.ECharts | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!chartRef.current) return

    let option: any = null
    try {
      // 尝试解析 JSON，如果正在流式输出，可能解析失败
      option = JSON.parse(data)
      setError(null)
    } catch (e) {
      // 如果还在流式输出中，我们不显示错误，只显示 loading
      if (isStreaming) return
      setError('图表数据格式错误')
      return
    }

    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current)
    }

    try {
      chartInstance.current.setOption(option, true)
    } catch (e) {
      console.error('ECharts render error:', e)
      setError('图表渲染失败')
    }

    const handleResize = () => {
      chartInstance.current?.resize()
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
    }
  }, [data, isStreaming])

  useEffect(() => {
    return () => {
      if (chartInstance.current) {
        chartInstance.current.dispose()
      }
    }
  }, [])

  if (error) {
    return (
      <div className="flex items-center gap-2 text-red-500 p-4 bg-red-50 dark:bg-red-950/20 rounded-lg my-4">
        <AlertCircle className="h-4 w-4" />
        <span className="text-sm">{error}</span>
      </div>
    )
  }

  return (
    <div className="my-4 rounded-lg border border-border bg-card p-4 relative min-h-[300px]">
      {isStreaming && !chartInstance.current && (
        <div className="absolute inset-0 flex items-center justify-center bg-background/50 z-10 backdrop-blur-sm rounded-lg">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">正在生成图表数据...</span>
          </div>
        </div>
      )}
      <div ref={chartRef} className="w-full h-[300px]" />
    </div>
  )
}