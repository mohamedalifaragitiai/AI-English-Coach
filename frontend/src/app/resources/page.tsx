'use client';

import { BookOpen, Headphones, PenTool, Video } from 'lucide-react';
import { DashboardLayout } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const resources = [
  {
    title: 'Reading Materials',
    description: 'Articles and stories at your level',
    icon: BookOpen,
    color: 'bg-blue-100 text-blue-600',
  },
  {
    title: 'Listening Practice',
    description: 'Podcasts and audio lessons',
    icon: Headphones,
    color: 'bg-purple-100 text-purple-600',
  },
  {
    title: 'Writing Exercises',
    description: 'Grammar and writing practice',
    icon: PenTool,
    color: 'bg-green-100 text-green-600',
  },
  {
    title: 'Video Lessons',
    description: 'Video tutorials and explanations',
    icon: Video,
    color: 'bg-orange-100 text-orange-600',
  },
];

export default function ResourcesPage() {
  return (
    <DashboardLayout userName="Demo User" streakDays={5} level="A2">
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold">Study Resources</h1>
          <p className="text-slate-500">Materials to supplement your practice</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {resources.map((resource) => (
            <Card key={resource.title} className="cursor-pointer hover:shadow-md transition-shadow">
              <CardContent className="pt-6">
                <div className="flex items-start gap-4">
                  <div className={`rounded-lg p-3 ${resource.color}`}>
                    <resource.icon className="h-6 w-6" />
                  </div>
                  <div>
                    <h3 className="font-semibold">{resource.title}</h3>
                    <p className="text-sm text-slate-500">{resource.description}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Coming Soon</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-slate-500">
              Additional learning resources and curated content will be added here.
            </p>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  );
}
