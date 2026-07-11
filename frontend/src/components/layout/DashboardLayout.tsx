'use client';

import { ReactNode } from 'react';
import { Sidebar } from './Sidebar';
import { Header } from './Header';

interface DashboardLayoutProps {
  children: ReactNode;
  userName?: string;
  streakDays?: number;
  level?: string;
}

export function DashboardLayout({
  children,
  userName,
  streakDays,
  level,
}: DashboardLayoutProps) {
  return (
    <div className="flex h-screen bg-slate-50">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header userName={userName} streakDays={streakDays} level={level} />
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  );
}
