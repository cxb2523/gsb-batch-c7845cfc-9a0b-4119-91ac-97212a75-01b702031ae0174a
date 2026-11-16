// build 3c94ff7262
// (next) a1d638
import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '远程页面采集结果',
  description: '构建期远程页面采集结果：成功数、失败数与失败清单',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
