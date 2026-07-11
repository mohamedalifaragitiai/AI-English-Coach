'use client';

import { Flame, Bell } from 'lucide-react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

interface HeaderProps {
  userName?: string;
  streakDays?: number;
  level?: string;
}

export function Header({ userName = 'Learner', streakDays = 0, level = 'A1' }: HeaderProps) {
  return (
    <header className="flex h-16 items-center justify-between border-b bg-white px-6">
      <div className="flex items-center gap-4">
        <h1 className="text-lg font-semibold text-slate-900">
          Welcome back, {userName}!
        </h1>
        <Badge variant="secondary" className="text-sm">
          Level {level}
        </Badge>
      </div>

      <div className="flex items-center gap-4">
        {/* Streak */}
        {streakDays > 0 && (
          <div className="flex items-center gap-2 rounded-full bg-orange-100 px-3 py-1">
            <Flame className="h-4 w-4 text-orange-500" />
            <span className="text-sm font-medium text-orange-700">
              {streakDays} day streak
            </span>
          </div>
        )}

        {/* Notifications */}
        <Button variant="ghost" size="icon">
          <Bell className="h-5 w-5" />
        </Button>

        {/* Avatar */}
        <Avatar>
          <AvatarFallback className="bg-slate-200">
            {userName.charAt(0).toUpperCase()}
          </AvatarFallback>
        </Avatar>
      </div>
    </header>
  );
}
