'use client';

import Link from 'next/link';
import { Mic, BookOpen, Target, BarChart3 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

export function QuickActions() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Quick Actions</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        <Link href="/practice">
          <Button className="w-full justify-start gap-2" size="lg">
            <Mic className="h-5 w-5" />
            Start Practice Session
          </Button>
        </Link>
        <Link href="/plan">
          <Button variant="outline" className="w-full justify-start gap-2">
            <Target className="h-5 w-5" />
            View Learning Plan
          </Button>
        </Link>
        <Link href="/reports">
          <Button variant="outline" className="w-full justify-start gap-2">
            <BarChart3 className="h-5 w-5" />
            View Progress Report
          </Button>
        </Link>
        <Link href="/resources">
          <Button variant="outline" className="w-full justify-start gap-2">
            <BookOpen className="h-5 w-5" />
            Study Resources
          </Button>
        </Link>
      </CardContent>
    </Card>
  );
}
