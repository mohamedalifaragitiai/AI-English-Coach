'use client';

import { useEffect, useState } from 'react';
import { TrendingUp, TrendingDown, Minus, Award, Target, Lightbulb } from 'lucide-react';
import { DashboardLayout } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import api, { ProgressReport, GapAnalysis } from '@/lib/api';
import { cn } from '@/lib/utils';

const DEMO_USER_ID = 'demo_user';

export default function ReportsPage() {
  const [report, setReport] = useState<ProgressReport | null>(null);
  const [gaps, setGaps] = useState<GapAnalysis | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const [reportData, gapsData] = await Promise.all([
          api.getProgressReport(DEMO_USER_ID),
          api.getGapAnalysis(DEMO_USER_ID),
        ]);
        setReport(reportData);
        setGaps(gapsData);
      } catch (err) {
        // Demo data
        setReport({
          user_id: DEMO_USER_ID,
          user_name: 'Demo User',
          generated_at: new Date().toISOString(),
          period: {
            start: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString(),
            end: new Date().toISOString(),
          },
          overall: {
            level: 2,
            level_name: 'A2',
            score: 0.52,
            level_progress: 0.65,
          },
          skill_trends: [
            { skill: 'pronunciation', current_score: 0.65, previous_score: 0.60, change: 0.05, trend_direction: 'improving', data_points: [] },
            { skill: 'grammar', current_score: 0.48, previous_score: 0.45, change: 0.03, trend_direction: 'improving', data_points: [] },
            { skill: 'fluency', current_score: 0.55, previous_score: 0.55, change: 0, trend_direction: 'stable', data_points: [] },
            { skill: 'vocabulary', current_score: 0.42, previous_score: 0.45, change: -0.03, trend_direction: 'declining', data_points: [] },
          ],
          practice_stats: {
            total_sessions: 12,
            total_utterances: 156,
            total_practice_minutes: 180,
            sessions_this_week: 4,
            sessions_last_week: 3,
            current_streak: 5,
            longest_streak: 12,
            average_session_minutes: 15,
            most_active_day: 'Monday',
          },
          achievements: [
            { id: 'streak_7', title: 'Week Warrior', description: 'Practice 7 days in a row', earned_at: null, progress: 0.71, earned: false },
            { id: 'improve_pronunciation', title: 'Pronunciation Champion', description: 'Improve pronunciation by 10%', earned_at: new Date().toISOString(), progress: 1, earned: true },
          ],
          recommendations: [
            'Focus on vocabulary - it has declined recently. Try dedicated practice exercises.',
            'Your pronunciation score is improving! Keep up the shadowing practice.',
          ],
          highlights: [
            'Great progress in pronunciation! Up 5% this period.',
            'Amazing 5-day practice streak!',
          ],
        });
        setGaps({
          user_id: DEMO_USER_ID,
          timestamp: new Date().toISOString(),
          overall_score: 0.52,
          overall_level: 2,
          overall_level_name: 'A2',
          gaps: [
            { skill: 'vocabulary', current_score: 0.42, target_score: 0.55, gap_size: 0.13, trend: -0.1, priority: 2.5, level: 2, level_name: 'A2', assessment_count: 10 },
            { skill: 'grammar', current_score: 0.48, target_score: 0.55, gap_size: 0.07, trend: 0.1, priority: 1.8, level: 2, level_name: 'A2', assessment_count: 10 },
            { skill: 'fluency', current_score: 0.55, target_score: 0.70, gap_size: 0.15, trend: 0, priority: 1.5, level: 3, level_name: 'B1', assessment_count: 10 },
          ],
          priority_skills: ['vocabulary', 'grammar', 'fluency'],
        });
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  if (loading) {
    return (
      <DashboardLayout userName="Demo User" streakDays={5} level="A2">
        <div className="flex h-full items-center justify-center">
          <p className="text-slate-500">Loading reports...</p>
        </div>
      </DashboardLayout>
    );
  }

  const getTrendIcon = (direction: string) => {
    if (direction === 'improving') return <TrendingUp className="h-4 w-4 text-green-500" />;
    if (direction === 'declining') return <TrendingDown className="h-4 w-4 text-red-500" />;
    return <Minus className="h-4 w-4 text-slate-400" />;
  };

  return (
    <DashboardLayout userName="Demo User" streakDays={5} level="A2">
      <div className="space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-2xl font-bold">Progress Reports</h1>
          <p className="text-slate-500">
            Your learning journey over the past 30 days
          </p>
        </div>

        <Tabs defaultValue="overview" className="space-y-6">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="gaps">Gap Analysis</TabsTrigger>
            <TabsTrigger value="achievements">Achievements</TabsTrigger>
          </TabsList>

          {/* Overview Tab */}
          <TabsContent value="overview" className="space-y-6">
            {/* Highlights */}
            {report?.highlights && report.highlights.length > 0 && (
              <Card className="bg-gradient-to-r from-blue-50 to-purple-50 border-0">
                <CardContent className="pt-6">
                  <div className="flex items-start gap-4">
                    <div className="rounded-full bg-white p-2">
                      <Award className="h-6 w-6 text-blue-600" />
                    </div>
                    <div>
                      <h3 className="font-semibold mb-2">Highlights</h3>
                      <ul className="space-y-1">
                        {report.highlights.map((highlight, i) => (
                          <li key={i} className="text-slate-700">{highlight}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Level Progress */}
            <Card>
              <CardHeader>
                <CardTitle>Level Progress</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-lg font-semibold">
                    {report?.overall.level_name}
                  </span>
                  <span className="text-sm text-slate-500">
                    {Math.round((report?.overall.level_progress || 0) * 100)}% to next level
                  </span>
                </div>
                <Progress
                  value={(report?.overall.level_progress || 0) * 100}
                  className="h-4"
                />
              </CardContent>
            </Card>

            {/* Skill Trends */}
            <Card>
              <CardHeader>
                <CardTitle>Skill Trends</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {report?.skill_trends.map((trend) => (
                    <div key={trend.skill} className="flex items-center gap-4">
                      <div className="w-28 font-medium capitalize">{trend.skill}</div>
                      <div className="flex-1">
                        <Progress value={trend.current_score * 100} className="h-2" />
                      </div>
                      <div className="w-16 text-right">
                        {Math.round(trend.current_score * 100)}%
                      </div>
                      <div className="flex items-center gap-1 w-20">
                        {getTrendIcon(trend.trend_direction)}
                        <span className={cn(
                          'text-sm',
                          trend.trend_direction === 'improving' && 'text-green-600',
                          trend.trend_direction === 'declining' && 'text-red-600',
                          trend.trend_direction === 'stable' && 'text-slate-400'
                        )}>
                          {trend.change > 0 ? '+' : ''}{Math.round(trend.change * 100)}%
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Recommendations */}
            {report?.recommendations && report.recommendations.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Lightbulb className="h-5 w-5 text-yellow-500" />
                    Recommendations
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-2">
                    {report.recommendations.map((rec, i) => (
                      <li key={i} className="flex items-start gap-2">
                        <div className="h-2 w-2 rounded-full bg-blue-500 mt-2" />
                        <span className="text-slate-700">{rec}</span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          {/* Gap Analysis Tab */}
          <TabsContent value="gaps" className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Target className="h-5 w-5" />
                  Priority Skills
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-slate-500 mb-4">
                  Focus on these skills to improve fastest:
                </p>
                <div className="flex gap-2 mb-6">
                  {gaps?.priority_skills.map((skill, i) => (
                    <Badge key={skill} variant={i === 0 ? 'default' : 'outline'} className="capitalize">
                      #{i + 1} {skill}
                    </Badge>
                  ))}
                </div>

                <div className="space-y-4">
                  {gaps?.gaps.map((gap) => (
                    <div key={gap.skill} className="rounded-lg border p-4">
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-medium capitalize">{gap.skill}</span>
                        <Badge variant="outline">{gap.level_name}</Badge>
                      </div>
                      <div className="grid grid-cols-3 gap-4 text-sm mb-3">
                        <div>
                          <p className="text-slate-500">Current</p>
                          <p className="font-semibold">{Math.round(gap.current_score * 100)}%</p>
                        </div>
                        <div>
                          <p className="text-slate-500">Target</p>
                          <p className="font-semibold">{Math.round(gap.target_score * 100)}%</p>
                        </div>
                        <div>
                          <p className="text-slate-500">Gap</p>
                          <p className="font-semibold text-orange-600">
                            {Math.round(gap.gap_size * 100)}%
                          </p>
                        </div>
                      </div>
                      <div className="relative pt-2">
                        <Progress value={gap.current_score * 100} className="h-2" />
                        <div
                          className="absolute top-2 h-2 w-0.5 bg-green-500"
                          style={{ left: `${gap.target_score * 100}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Achievements Tab */}
          <TabsContent value="achievements" className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>Achievements</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 md:grid-cols-2">
                  {report?.achievements.map((achievement) => (
                    <div
                      key={achievement.id}
                      className={cn(
                        'rounded-lg border p-4',
                        achievement.earned ? 'bg-yellow-50 border-yellow-200' : 'bg-slate-50'
                      )}
                    >
                      <div className="flex items-start gap-3">
                        <div className={cn(
                          'rounded-full p-2',
                          achievement.earned ? 'bg-yellow-200' : 'bg-slate-200'
                        )}>
                          <Award className={cn(
                            'h-5 w-5',
                            achievement.earned ? 'text-yellow-600' : 'text-slate-400'
                          )} />
                        </div>
                        <div className="flex-1">
                          <h4 className="font-medium">{achievement.title}</h4>
                          <p className="text-sm text-slate-500">{achievement.description}</p>
                          {!achievement.earned && (
                            <div className="mt-2">
                              <div className="flex justify-between text-xs text-slate-500 mb-1">
                                <span>Progress</span>
                                <span>{Math.round(achievement.progress * 100)}%</span>
                              </div>
                              <Progress value={achievement.progress * 100} className="h-1.5" />
                            </div>
                          )}
                          {achievement.earned && achievement.earned_at && (
                            <p className="text-xs text-yellow-600 mt-2">
                              Earned {new Date(achievement.earned_at).toLocaleDateString()}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}
