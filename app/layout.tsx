import { Providers } from '@/components/Providers'
import './globals.css'
//根布局使用 Providers

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html>
      <body>
        <Providers>
          {children}
        </Providers>
      </body>
    </html>
  )
}