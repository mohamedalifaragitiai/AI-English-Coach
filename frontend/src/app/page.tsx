'use client';

import { useEffect, useState } from 'react';
import { BookOpen, Clock, Flame, GraduationCap } from 'lucide-react';
import { DashboardLayout } from '@/components/layout';
import { SkillCard, StatsCard, QuickActions, RecentActivity } from '@/components/dashboard';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import api, { ProgressSummary } from '@/lib/api';

const DEMO_USER_ID = 'demo_user';

const LEVEL_NAMES: Record<number, string> = {
  0: 'A0',
  1: 'A1',
  2: 'A2',
  3: 'B1',
  4: 'B2',
  5: 'C1',
  6: 'C2',
};

export default function Dashboard() {
  const [progress, setProgress] = useState<ProgressSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadProgress() {
      try {
        const data = await api.getProgress(DEMO_USER_ID);
        setProgress(data);
      } catch (err) {
        // In development, show demo data
        setProgress({
          user_id: DEMO_USER_ID,
          current_level: 2,
          level_name: 'A2',
          streak_days: 5,
          total_sessions: 12,
          total_assessments: 45,
          latest_overall: 0.52,
          skills: {
            pronunciation: 0.65,
            grammar: 0.48,
            fluency: 0.55,
            vocabulary: 0.42,
            coherence: 0.58,
            relevance: 0.72,
          },
          gaps: null,
          time_to_next_level: '~2 weeks',
        });
      } finally {
        setLoading(false);
      }
    }
    loadProgress();
  }, []);

  if (loading) {
    return (
      <DashboardLayout userName="Loading..." streakDays={0} level="--">
        <div className="flex h-full items-center justify-center">
          <p className="text-slate-500">Loading dashboard...</p>
        </div>
      </DashboardLayout>
    );
  }

  const levelProgress = progress?.latest_overall
    ? Math.round(progress.latest_overall * 100)
    : 0;

  return (
    <DashboardLayout
      userName="Demo User"
      streakDays={progress?.streak_days || 0}
      level={LEVEL_NAMES[progress?.current_level || 0]}
    >
      <div className="space-y-6">
        {/* Stats Row */}
        <div className="grid gap-4 md:grid-cols-4">
          <StatsCard
            title="Current Level"
            value={LEVEL_NAMES[progress?.current_level || 0]}
            subtitle={progress?.time_to_next_level || 'Keep practicing!'}
            icon={GraduationCap}
            iconColor="text-blue-600"
            iconBg="bg-blue-100"
          />
          <StatsCard
            title="Practice Streak"
            value={`${progress?.streak_days || 0} days`}
            subtitle="Keep it going!"
            icon={Flame}
            iconColor="text-orange-600"
            iconBg="bg-orange-100"
          />
          <StatsCard
            title="Total Sessions"
            value={progress?.total_sessions || 0}
            subtitle={`${progress?.total_assessments || 0} assessments`}
            icon={Clock}
            iconColor="text-purple-600"
            iconBg="bg-purple-100"
          />
          <StatsCard
            title="Overall Score"
            value={`${Math.round((progress?.latest_overall || 0) * 100)}%`}
            subtitle="Last assessment"
            icon={BookOpen}
            iconColor="text-green-600"
            iconBg="bg-green-100"
          />
        </div>

        {/* Main Content */}
        <div className="grid gap-6 lg:grid-cols-3">
          {/* Skills Section */}
          <div className="lg:col-span-2 space-y-6">
            {/* Level Progress */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Level Progress</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm text-slate-600">
                    Progress to {LEVEL_NAMES[(progress?.current_level || 0) + 1] || 'C2'}
                  </span>
                  <span className="text-sm font-medium">{levelProgress}%</span>
                </div>
                <Progress value={levelProgress} className="h-3" />
                <p className="mt-2 text-xs text-slate-500">
                  {progress?.time_to_next_level || 'Practice regularly to level up!'}
                </p>
              </CardContent>
            </Card>

            {/* Skills Grid */}
            <div>
              <h2 className="mb-4 text-lg font-semibold">Skill Scores</h2>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {Object.entries(progress?.skills || {}).map(([skill, score]) => (
                  <SkillCard
                    key={skill}
                    name={skill}
                    score={score}
                    trend={
                      skill === 'pronunciation'
                        ? 'improving'
                        : skill === 'vocabulary'
                        ? 'declining'
                        : 'stable'
                    }
                    change={skill === 'pronunciation' ? 0.05 : skill === 'vocabulary' ? -0.03 : 0}
                  />
                ))}
              </div>
            </div>
          </div>

          {/* Sidebar */}
          <div className="space-y-6">
            <QuickActions />
            <RecentActivity />
          </div>
        </div>
      </div>
    </DashboardLayout>
  );
}
