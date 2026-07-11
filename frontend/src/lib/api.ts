/**
 * API client for English Coach backend
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface User {
  user_id: string;
  display_name: string;
  created_at: string;
  current_level: number;
  streak_days: number;
  settings: Record<string, unknown>;
}

export interface ProgressSummary {
  user_id: string;
  current_level: number;
  level_name: string;
  streak_days: number;
  total_sessions: number;
  total_assessments: number;
  latest_overall: number | null;
  skills: Record<string, number | null>;
  gaps: Record<string, number> | null;
  time_to_next_level: string | null;
}

export interface SkillTrendPoint {
  timestamp: string;
  score: number;
}

export interface SkillTrend {
  user_id: string;
  skill: string;
  points: SkillTrendPoint[];
}

export interface GapAnalysis {
  user_id: string;
  timestamp: string;
  overall_score: number;
  overall_level: number;
  overall_level_name: string;
  gaps: Array<{
    skill: string;
    current_score: number;
    target_score: number;
    gap_size: number;
    trend: number;
    priority: number;
    level: number;
    level_name: string;
    assessment_count: number;
  }>;
  priority_skills: string[];
}

export interface LearningPlan {
  user_id: string;
  created_at: string;
  valid_until: string;
  current_level: number;
  current_level_name: string;
  target_level: number;
  target_level_name: string;
  focus_skills: string[];
  daily_goal_minutes: number;
  weekly_goal_sessions: number;
  items: Array<{
    skill: string;
    exercise_type: string;
    description: string;
    duration_minutes: number;
    frequency: string;
    goal: string;
    tips: string[];
  }>;
  milestones: Array<{
    week: number;
    goal: string;
    criteria: string[];
  }>;
}

export interface ProgressReport {
  user_id: string;
  user_name: string;
  generated_at: string;
  period: { start: string; end: string };
  overall: {
    level: number;
    level_name: string;
    score: number;
    level_progress: number;
  };
  skill_trends: Array<{
    skill: string;
    current_score: number;
    previous_score: number;
    change: number;
    trend_direction: string;
    data_points: Array<{ date: string; score: number }>;
  }>;
  practice_stats: {
    total_sessions: number;
    total_utterances: number;
    total_practice_minutes: number;
    sessions_this_week: number;
    sessions_last_week: number;
    current_streak: number;
    longest_streak: number;
    average_session_minutes: number;
    most_active_day: string;
  };
  achievements: Array<{
    id: string;
    title: string;
    description: string;
    earned_at: string | null;
    progress: number;
    earned: boolean;
  }>;
  recommendations: string[];
  highlights: string[];
}

export interface HealthStatus {
  status: string;
  resource_guard: {
    running: boolean;
    gpu_available: boolean;
    degradation_level: string;
  };
  event_bus: {
    running: boolean;
    queue_size: number;
  };
  cold_path: {
    running: boolean;
    evaluators: number;
    skills: string[];
  };
  models: Record<string, unknown> | null;
  resources: Record<string, unknown> | null;
}

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE) {
    this.baseUrl = baseUrl;
  }

  private async request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Request failed' }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json();
  }

  // Health
  async getHealth(): Promise<HealthStatus> {
    return this.request<HealthStatus>('/health');
  }

  // Users
  async getUsers(): Promise<User[]> {
    return this.request<User[]>('/users');
  }

  async getUser(userId: string): Promise<User> {
    return this.request<User>(`/users/${userId}`);
  }

  async createUser(userId: string, displayName: string): Promise<User> {
    return this.request<User>('/users', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, display_name: displayName }),
    });
  }

  // Progress
  async getProgress(userId: string): Promise<ProgressSummary> {
    return this.request<ProgressSummary>(`/users/${userId}/progress`);
  }

  async getSkillTrend(userId: string, skill: string, days = 30): Promise<SkillTrend> {
    return this.request<SkillTrend>(`/users/${userId}/skills/${skill}/trend?days=${days}`);
  }

  async updateStreak(userId: string): Promise<{ streak_days: number }> {
    return this.request<{ streak_days: number }>(`/users/${userId}/streak/update`, {
      method: 'POST',
    });
  }

  // Reports
  async getGapAnalysis(userId: string, targetLevel = -1): Promise<GapAnalysis> {
    return this.request<GapAnalysis>(
      `/users/${userId}/reports/gaps?target_level=${targetLevel}`
    );
  }

  async getLearningPlan(
    userId: string,
    durationDays = 14,
    dailyMinutes = 30
  ): Promise<LearningPlan> {
    return this.request<LearningPlan>(
      `/users/${userId}/reports/plan?duration_days=${durationDays}&daily_minutes=${dailyMinutes}`
    );
  }

  async getProgressReport(userId: string, days = 30): Promise<ProgressReport> {
    return this.request<ProgressReport>(`/users/${userId}/reports/progress?days=${days}`);
  }
}

export const api = new ApiClient();
export default api;
