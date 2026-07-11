'use client';

import { Clock, CheckCircle, Star } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';

interface Activity {
  id: string;
  type: 'practice' | 'achievement' | 'milestone';
  title: string;
  description: string;
  time: string;
}

interface RecentActivityProps {
  activities?: Activity[];
}

const defaultActivities: Activity[] = [
  {
    id: '1',
    type: 'practice',
    title: 'Practice Session',
    description: '15 minutes of conversation practice',
    time: '2 hours ago',
  },
  {
    id: '2',
    type: 'achievement',
    title: 'Achievement Unlocked',
    description: 'Completed 5-day streak!',
    time: 'Yesterday',
  },
  {
    id: '3',
    type: 'milestone',
    title: 'Level Progress',
    description: 'Grammar improved by 5%',
    time: '2 days ago',
  },
];

export function RecentActivity({ activities = defaultActivities }: RecentActivityProps) {
  const getIcon = (type: Activity['type']) => {
    switch (type) {
      case 'practice':
        return <Clock className="h-4 w-4 text-blue-500" />;
      case 'achievement':
        return <Star className="h-4 w-4 text-yellow-500" />;
      case 'milestone':
        return <CheckCircle className="h-4 w-4 text-green-500" />;
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Recent Activity</CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[200px]">
          <div className="space-y-4">
            {activities.map((activity) => (
              <div key={activity.id} className="flex gap-3">
                <div className="mt-0.5">{getIcon(activity.type)}</div>
                <div className="flex-1">
                  <p className="text-sm font-medium">{activity.title}</p>
                  <p className="text-xs text-slate-500">{activity.description}</p>
                  <p className="text-xs text-slate-400">{activity.time}</p>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
