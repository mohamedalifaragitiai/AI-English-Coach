'use client';

import { DashboardLayout } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export default function ProgressPage() {
  return (
    <DashboardLayout userName="Demo User" streakDays={5} level="A2">
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold">Progress Tracking</h1>
          <p className="text-slate-500">Detailed view of your learning progress</p>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Coming Soon</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-slate-500">
              Detailed progress charts and historical data will be available here.
            </p>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
