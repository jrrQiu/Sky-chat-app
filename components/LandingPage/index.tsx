// components/LandingPage/index.tsx
import { LandingHero } from './LandingHero'
import { LandingInput } from './LandingInput'
import { LandingTutorial } from './LandingTutorial' // <-- 新增引入

export function LandingPage() {
  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 pt-12 relative">
      <div className="flex flex-col items-center justify-center min-h-screen px-6">
        <div className="w-full max-w-6xl space-y-16">
          <LandingHero />
          <div className="max-w-2xl mx-auto">
            <LandingInput />
          </div>
        </div>
      </div>
      
      {/* 新增的新手教程悬浮组件 */}
      <LandingTutorial />
    </div>
  )
}