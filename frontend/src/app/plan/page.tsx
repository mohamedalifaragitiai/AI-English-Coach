'use client';

import { useEffect, useState } from 'react';
import { Target, Clock, Calendar, CheckCircle } from 'lucide-react';
import { DashboardLayout } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import api, { LearningPlan } from '@/lib/api';

const DEMO_USER_ID = 'demo_user';

const exerciseColors: Record<string, string> = {
  conversation: 'bg-blue-100 text-blue-800',
  shadowing: 'bg-purple-100 text-purple-800',
  reading_aloud: 'bg-green-100 text-green-800',
  grammar_drill: 'bg-orange-100 text-orange-800',
  vocabulary_review: 'bg-pink-100 text-pink-800',
  pronunciation_focus: 'bg-cyan-100 text-cyan-800',
  role_play: 'bg-yellow-100 text-yellow-800',
  dictation: 'bg-red-100 text-red-800',
};

export default function LearningPlanPage() {
  const [plan, setPlan] = useState<LearningPlan | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadPlan() {
      try {
        const data = await api.getLearningPlan(DEMO_USER_ID);
        setPlan(data);
      } catch (err) {
        // Demo data
        setPlan({
          user_id: DEMO_USER_ID,
          created_at: new Date().toISOString(),
          valid_until: new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString(),
          current_level: 2,
          current_level_name: 'A2',
          target_level: 3,
          target_level_name: 'B1',
          focus_skills: ['grammar', 'pronunciation', 'fluency'],
          daily_goal_minutes: 30,
          weekly_goal_sessions: 5,
          items: [
            {
              skill: 'grammar',
              exercise_type: 'grammar_drill',
              description: 'Complete grammar exercises and get feedback',
              duration_minutes: 15,
              frequency: 'daily',
              goal: 'Reduce grammar errors and reach A2 proficiency',
              tips: ['Pay attention to verb tenses', 'Practice subject-verb agreement'],
            },
            {
              skill: 'pronunciation',
              exercise_type: 'shadowing',
              description: 'Listen to native speech and repeat immediately after',
              duration_minutes: 15,
              frequency: 'daily',
              goal: 'Improve pronunciation clarity',
              tips: ['Focus on sounds that dont exist in your native language'],
            },
            {
              skill: 'fluency',
              exercise_type: 'conversation',
              description: 'Practice free-form conversation with the AI coach',
              duration_minutes: 20,
              frequency: 'every_other_day',
              goal: 'Increase speaking fluency and reduce hesitations',
              tips: ['Dont stop to correct every mistake', 'Practice thinking in English'],
            },
          ],
          milestones: [
            {
              week: 1,
              goal: 'Complete initial practice sessions',
              criteria: ['Complete 5 grammar exercises', 'Establish daily practice habit'],
            },
            {
              week: 2,
              goal: 'Show measurable improvement',
              criteria: ['Improve grammar score by 5%', 'Complete 10 total sessions'],
            },
          ],
        });
      } finally {
        setLoading(false);
      }
    }
    loadPlan();
  }, []);

  if (loading) {
    return (
      <DashboardLayout userName="Demo User" streakDays={5} level="A2">
        <div className="flex h-full items-center justify-center">
          <p className="text-slate-500">Loading learning plan...</p>
        </div>
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout userName="Demo User" streakDays={5} level="A2">
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Learning Plan</h1>
            <p className="text-slate-500">Your personalized study roadmap</p>
          </div>
          <Badge variant="outline" className="text-lg px-4 py-2">
            {plan?.current_level_name} → {plan?.target_level_name}
          </Badge>
        </div>

        {/* Goals Overview */}
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center gap-4">
                <div className="rounded-full bg-blue-100 p-3">
                  <Target className="h-6 w-6 text-blue-600" />
                </div>
                <div>
                  <p className="text-sm text-slate-500">Focus Skills</p>
                  <p className="font-semibold capitalize">
                    {plan?.focus_skills.join(', ')}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center gap-4">
                <div className="rounded-full bg-green-100 p-3">
                  <Clock className="h-6 w-6 text-green-600" />
                </div>
                <div>
                  <p className="text-sm text-slate-500">Daily Goal</p>
                  <p className="font-semibold">{plan?.daily_goal_minutes} minutes</p>
                </div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <div className="flex items-center gap-4">
                <div className="rounded-full bg-purple-100 p-3">
                  <Calendar className="h-6 w-6 text-purple-600" />
                </div>
                <div>
                  <p className="text-sm text-slate-500">Weekly Sessions</p>
                  <p className="font-semibold">{plan?.weekly_goal_sessions} sessions</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Exercise Items */}
        <Card>
          <CardHeader>
            <CardTitle>Practice Exercises</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {plan?.items.map((item, index) => (
              <div
                key={index}
                className="rounded-lg border p-4 hover:bg-slate-50 transition-colors"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <Badge className={exerciseColors[item.exercise_type] || 'bg-slate-100'}>
                        {item.exercise_type.replace('_', ' ')}
                      </Badge>
                      <Badge variant="outline" className="capitalize">
                        {item.skill}
                      </Badge>
                      <span className="text-sm text-slate-500">
                        {item.duration_minutes} min | {item.frequency.replace('_', ' ')}
                      </span>
                    </div>
                    <p className="text-slate-700 mb-2">{item.description}</p>
                    <p className="text-sm text-slate-500 mb-2">
                      <strong>Goal:</strong> {item.goal}
                    </p>
                    {item.tips.length > 0 && (
                      <ul className="text-sm text-slate-500 list-disc list-inside">
                        {item.tips.map((tip, i) => (
                          <li key={i}>{tip}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        {/* Milestones */}
        <Card>
          <CardHeader>
            <CardTitle>Milestones</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-6">
              {plan?.milestones.map((milestone, index) => (
                <div key={index} className="flex gap-4">
                  <div className="flex flex-col items-center">
                    <div className="rounded-full bg-slate-200 p-2">
                      <CheckCircle className="h-5 w-5 text-slate-500" />
                    </div>
                    {index < (plan?.milestones.length || 0) - 1 && (
                      <div className="w-0.5 flex-1 bg-slate-200 my-2" />
                    )}
                  </div>
                  <div className="flex-1 pb-4">
                    <h3 className="font-semibold">Week {milestone.week}</h3>
                    <p className="text-slate-600 mb-2">{milestone.goal}</p>
                    <ul className="text-sm text-slate-500 space-y-1">
                      {milestone.criteria.map((criterion, i) => (
                        <li key={i} className="flex items-center gap-2">
                          <div className="h-1.5 w-1.5 rounded-full bg-slate-400" />
                          {criterion}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
