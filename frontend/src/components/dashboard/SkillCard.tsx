'use client';

import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';

interface SkillCardProps {
  name: string;
  score: number | null;
  trend?: 'improving' | 'declining' | 'stable';
  change?: number;
}

const skillColors: Record<string, string> = {
  pronunciation: 'bg-blue-500',
  grammar: 'bg-green-500',
  fluency: 'bg-purple-500',
  vocabulary: 'bg-orange-500',
  coherence: 'bg-pink-500',
  relevance: 'bg-cyan-500',
};

export function SkillCard({ name, score, trend = 'stable', change = 0 }: SkillCardProps) {
  const displayScore = score !== null ? Math.round(score * 100) : 0;
  const colorClass = skillColors[name.toLowerCase()] || 'bg-slate-500';

  const TrendIcon =
    trend === 'improving' ? TrendingUp : trend === 'declining' ? TrendingDown : Minus;

  const trendColor =
    trend === 'improving'
      ? 'text-green-600'
      : trend === 'declining'
      ? 'text-red-600'
      : 'text-slate-400';

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium capitalize text-slate-600">
          {name}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-end justify-between">
          <div>
            <span className="text-3xl font-bold">{displayScore}</span>
            <span className="text-lg text-slate-400">%</span>
          </div>
          <div className={cn('flex items-center gap-1', trendColor)}>
            <TrendIcon className="h-4 w-4" />
            {change !== 0 && (
              <span className="text-sm font-medium">
                {change > 0 ? '+' : ''}
                {Math.round(change * 100)}%
              </span>
            )}
          </div>
        </div>
        <Progress
          value={displayScore}
          className="mt-3 h-2"
        />
      </CardContent>
    </Card>
  );
}
