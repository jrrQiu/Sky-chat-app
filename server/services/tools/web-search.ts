// server/services/tools/web-search.ts
const TAVILY_API_URL = 'https://api.tavily.com/search'
const SEARCH_TIMEOUT = 10000 // 10 秒超时

interface TavilySearchResult {
  title: string
  url: string
  content: string
}

interface TavilyResponse {
  answer?: string
  results: TavilySearchResult[]
}

/**
 * 调用 Tavily API 进行全网搜索
 */
export async function searchWeb(query: string): Promise<string> {
  const apiKey = process.env.TAVILY_API_KEY
  if (!apiKey) {
    console.warn('未配置 TAVILY_API_KEY，跳过联网搜索')
    return '（未配置联网搜索功能，请基于你自己的知识回答）'
  }

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), SEARCH_TIMEOUT)

  try {
    const response = await fetch(TAVILY_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_key: apiKey,
        query,
        search_depth: 'basic',
        include_answer: false,
        max_results: 5, // 只取前5条最相关的网页结果
      }),
      signal: controller.signal,
    })

    if (!response.ok) {
      throw new Error(`Tavily API error: ${response.status}`)
    }

    const data: TavilyResponse = await response.json()
    return formatSearchResults(data)
  } catch (error) {
    console.error('联网搜索失败:', error)
    return '（联网搜索失败，请基于你自己的知识回答）'
  } finally {
    clearTimeout(timeoutId)
  }
}

/**
 * 把爬取到的网页内容，格式化成一段大模型能看懂的纯文本
 */
function formatSearchResults(response: TavilyResponse): string {
  if (!response.results || response.results.length === 0) {
    return '未找到相关搜索结果'
  }

  const parts = ['以下是我为你检索到的全网最新参考资料：\n']
  
  response.results.forEach((result, index) => {
    parts.push(`【参考资料 ${index + 1}】: ${result.title}`)
    parts.push(`来源: ${result.url}`)
    parts.push(`内容片段: ${result.content.substring(0, 300)}...\n`)
  })

  return parts.join('\n')
}