const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export interface UserProfile {
  height_cm: number | null
  weight_kg: number | null
  age: number | null
  gender: 'male' | 'female' | 'other' | null
  fitness_goal: 'lose_fat' | 'build_muscle' | 'maintain' | null
  experience_level: 'beginner' | 'intermediate' | 'advanced' | null
  dietary_preference: 'non_veg' | 'vegetarian' | 'vegan' | null
  activity_level: 'sedentary' | 'lightly_active' | 'moderately_active' | 'very_active' | null
  personal_notes: string
  preferred_unit: 'lb' | 'kg'
}

export interface MealLog {
  id: string
  date: string
  meal_type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  food_name: string
  calories: number
  protein_g: number
  carbs_g: number
  fat_g: number
  notes: string
  created_at: string
}

export interface User {
  id: string
  name: string
  email: string
  created_at: string
  profile: UserProfile | null
}

export interface AuthResponse {
  token: string
  user: User
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = localStorage.getItem('token')
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  }
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  if (token) headers['Authorization'] = `Token ${token}`

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const message =
      body.detail ||
      Object.values(body).flat().join(' ') ||
      `Request failed (${res.status})`
    throw new ApiError(res.status, message as string)
  }

  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return undefined as T
  }
  return res.json()
}

export const api = {
  auth: {
    signup: (data: { name: string; email: string; password: string }) =>
      request<AuthResponse>('/api/auth/signup/', { method: 'POST', body: JSON.stringify(data) }),

    login: (data: { email: string; password: string }) =>
      request<AuthResponse>('/api/auth/login/', { method: 'POST', body: JSON.stringify(data) }),

    logout: () =>
      request<{ detail: string }>('/api/auth/logout/', { method: 'POST' }),

    me: () =>
      request<User>('/api/auth/me/'),

    updateMe: (data: { name?: string; email?: string }) =>
      request<User>('/api/auth/me/', { method: 'PATCH', body: JSON.stringify(data) }),

    forgotPassword: (email: string) =>
      request<{ detail: string }>('/api/auth/password-reset/', {
        method: 'POST',
        body: JSON.stringify({ email, frontend_base: window.location.origin }),
      }),

    resetPassword: (token: string, password: string) =>
      request<{ detail: string }>('/api/auth/password-reset/confirm/', {
        method: 'POST',
        body: JSON.stringify({ token, password }),
      }),

    changePassword: (current_password: string, new_password: string) =>
      request<{ token: string }>('/api/auth/password-change/', {
        method: 'POST',
        body: JSON.stringify({ current_password, new_password }),
      }),
  },

  profile: {
    get: () =>
      request<UserProfile>('/api/profile/'),

    update: (data: Partial<UserProfile>) =>
      request<UserProfile>('/api/profile/', { method: 'PATCH', body: JSON.stringify(data) }),
  },

  chat: {
    listSessions: () =>
      request<ChatSession[]>('/api/chat/sessions/'),

    createSession: () =>
      request<ChatSession>('/api/chat/sessions/', { method: 'POST' }),

    getSession: (id: string) =>
      request<ChatSessionDetail>(`/api/chat/sessions/${id}/`),

    renameSession: (id: string, title: string) =>
      request<ChatSession>(`/api/chat/sessions/${id}/`, {
        method: 'PATCH',
        body: JSON.stringify({ title }),
      }),

    deleteSession: (id: string) =>
      request<void>(`/api/chat/sessions/${id}/`, { method: 'DELETE' }),
  },

  workouts: {
    generatePlan: (data: {
      days_per_week: number
      duration_weeks: number
      fitness_goal?: string
      experience_level?: string
      equipment?: string
      notes?: string
      body_context?: string
      body_photo?: File
    }) => {
      if (data.body_photo) {
        const fd = new FormData()
        Object.entries(data).forEach(([k, v]) => {
          if (k === 'body_photo') fd.append('body_photo', v as File)
          else if (v !== undefined) fd.append(k, String(v))
        })
        return request<WorkoutPlanPreview>('/api/workouts/plans/generate/', { method: 'POST', body: fd })
      }
      return request<WorkoutPlanPreview>('/api/workouts/plans/generate/', { method: 'POST', body: JSON.stringify(data) })
    },

    savePlan: (data: WorkoutPlanPreview) =>
      request<WorkoutPlanDetail>('/api/workouts/plans/', {
        method: 'POST',
        body: JSON.stringify(data),
      }),

    listPlans: () =>
      request<WorkoutPlan[]>('/api/workouts/plans/'),

    getPlan: (id: string) =>
      request<WorkoutPlanDetail>(`/api/workouts/plans/${id}/`),

    updatePlan: (id: string, data: { title?: string; description?: string }) =>
      request<WorkoutPlan>(`/api/workouts/plans/${id}/`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),

    replacePlan: (id: string, data: WorkoutPlanPreview) =>
      request<WorkoutPlanDetail>(`/api/workouts/plans/${id}/`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),

    deletePlan: (id: string) =>
      request<void>(`/api/workouts/plans/${id}/`, { method: 'DELETE' }),

    activatePlan: (id: string) =>
      request<WorkoutPlan>(`/api/workouts/plans/${id}/activate/`, { method: 'POST' }),

    listSessions: () =>
      request<WorkoutSessionSummary[]>('/api/workouts/sessions/'),

    startSession: (exercise_day_id: string) =>
      request<WorkoutSessionDetail>('/api/workouts/sessions/', {
        method: 'POST',
        body: JSON.stringify({ exercise_day_id }),
      }),

    getSession: (id: string) =>
      request<WorkoutSessionDetail>(`/api/workouts/sessions/${id}/`),

    completeSession: (id: string, notes?: string) =>
      request<WorkoutSessionDetail>(`/api/workouts/sessions/${id}/`, {
        method: 'PATCH',
        body: JSON.stringify({ is_completed: true, notes: notes ?? '' }),
      }),

    logSet: (sessionId: string, data: {
      exercise_id: string
      set_number: number
      reps_completed?: number
      weight_kg?: number
      is_completed?: boolean
      notes?: string
    }) =>
      request<SetLog>(`/api/workouts/sessions/${sessionId}/sets/`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),

    getExerciseHistory: (exerciseName: string) =>
      request<ExerciseHistoryPoint[]>(`/api/workouts/exercises/${encodeURIComponent(exerciseName)}/history/`),

    getExerciseGuide: (exerciseName: string) =>
      request<{ steps: string[]; muscles: string[]; tips: string[]; category: string; images: string[] }>(
        `/api/workouts/exercises/guide/?name=${encodeURIComponent(exerciseName)}`
      ),
  },

  diet: {
    generatePlan: (data?: { country?: string; cuisine_preference?: string; usual_foods?: string; duration_days?: number }) =>
      request<DietPlanPreview>('/api/diet/plans/generate/', { method: 'POST', body: JSON.stringify(data ?? {}) }),

    getMealGuide: (meal: { name: string; calories: number; protein_g: number; carbs_g: number; fat_g: number }) =>
      request<{ ingredients: string[]; steps: string[]; prep_time: string; tips: string[] }>(
        `/api/diet/meals/guide/?name=${encodeURIComponent(meal.name)}&calories=${meal.calories}&protein=${meal.protein_g}&carbs=${meal.carbs_g}&fat=${meal.fat_g}`
      ),

    savePlan: (data: DietPlanPreview) =>
      request<DietPlanDetail>('/api/diet/plans/', { method: 'POST', body: JSON.stringify(data) }),

    listPlans: () =>
      request<DietPlan[]>('/api/diet/plans/'),

    getPlan: (id: string) =>
      request<DietPlanDetail>(`/api/diet/plans/${id}/`),

    deletePlan: (id: string) =>
      request<void>(`/api/diet/plans/${id}/`, { method: 'DELETE' }),

    activatePlan: (id: string) =>
      request<DietPlan>(`/api/diet/plans/${id}/activate/`, { method: 'POST' }),

    scanFood: (imageFile: File) => {
      const form = new FormData()
      form.append('image', imageFile)
      return request<FoodScanResult>('/api/diet/food-scan/', { method: 'POST', body: form })
    },

    foodScanHistory: () =>
      request<FoodScanResult[]>('/api/diet/food-scan/history/'),

    getMealLogs: (date?: string) =>
      request<MealLog[]>(`/api/diet/meal-logs/${date ? `?date=${date}` : ''}`),

    addMealLog: (data: Omit<MealLog, 'id' | 'created_at'>) =>
      request<MealLog>('/api/diet/meal-logs/', { method: 'POST', body: JSON.stringify(data) }),

    deleteMealLog: (id: string) =>
      request<void>(`/api/diet/meal-logs/${id}/`, { method: 'DELETE' }),
  },

  body: {
    scan: (imageFile: File) => {
      const form = new FormData()
      form.append('image', imageFile)
      return request<BodyScanResult>('/api/body/scan/', { method: 'POST', body: form })
    },

    history: () =>
      request<BodyScanResult[]>('/api/body/scan/history/'),
  },

  health: {
    getSetupToken: () =>
      request<{ token: string }>('/api/health/connect/'),

    getStatus: () =>
      request<{ connected: boolean; provider: string | null; connected_at: string | null }>('/api/health/status/'),

    getSummary: () =>
      request<HealthDailySummary[]>('/api/health/summary/'),

    getWorkouts: () =>
      request<HealthWorkout[]>('/api/health/workouts/'),

    disconnect: () =>
      request<void>('/api/health/connect/', { method: 'DELETE' }),

    getRecovery: () =>
      request<HealthRecovery>('/api/health/recovery/'),

    getCalorieBalance: () =>
      request<HealthCalorieBalance>('/api/health/calorie-balance/'),

    getActivitySuggestion: () =>
      request<HealthActivitySuggestion>('/api/health/activity-suggestion/'),
  },
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface ChatSession {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ChatSessionDetail extends ChatSession {
  messages: ChatMessage[]
}

export interface Exercise {
  id: string
  name: string
  sets: number
  reps: string
  rest_seconds: number | null
  notes: string
  order: number
}

export interface WorkoutDay {
  id: string
  day_number: number
  name: string
  focus: string
  is_rest_day: boolean
  order: number
  exercises: Exercise[]
}

export interface WorkoutPlan {
  id: string
  title: string
  description: string
  is_active: boolean
  activated_at: string | null
  generated_by_ai: boolean
  duration_weeks: number | null
  created_at: string
  updated_at: string
}

export interface WorkoutPlanDetail extends WorkoutPlan {
  days: WorkoutDay[]
}

export interface WorkoutPlanPreview {
  title: string
  description?: string
  duration_weeks?: number
  days: Array<{
    day_number: number
    name: string
    focus?: string
    is_rest_day: boolean
    exercises: Array<{
      name: string
      sets: number
      reps: string
      rest_seconds?: number
      notes?: string
    }>
  }>
}

export interface SetLog {
  id: string
  exercise_id: string | null
  exercise_name: string
  set_number: number
  reps_completed: number | null
  weight_kg: number | null
  is_completed: boolean
  notes: string
}

export interface WorkoutSessionDetail {
  id: string
  exercise_day: WorkoutDay | null
  started_at: string
  completed_at: string | null
  notes: string
  is_completed: boolean
  set_logs: SetLog[]
}

export interface WorkoutSessionSummary {
  id: string
  day_name: string
  exercise_day_id: string | null
  started_at: string
  completed_at: string | null
  is_completed: boolean
}

export interface ExerciseHistoryPoint {
  workout_session__started_at: string
  set_number: number
  weight_kg: number
  reps_completed: number | null
}

export interface Meal {
  id: string
  day_number: number
  meal_type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  description: string
  calories: number
  protein_g: number
  carbs_g: number
  fat_g: number
  order: number
}

export interface DietPlan {
  id: string
  title: string
  description: string
  target_calories: number
  protein_g: number
  carbs_g: number
  fat_g: number
  is_active: boolean
  generated_by_ai: boolean
  created_at: string
  updated_at: string
}

export interface DietPlanDetail extends DietPlan {
  meals: Meal[]
}

export interface DietPlanPreview {
  title: string
  description?: string
  target_calories: number
  protein_g: number
  carbs_g: number
  fat_g: number
  meals: Array<{
    day_number?: number
    meal_type: string
    name: string
    description?: string
    calories: number
    protein_g: number
    carbs_g: number
    fat_g: number
  }>
}

export interface FoodScanResult {
  id: string
  food_name: string
  serving_size: string
  calories: number
  protein_g: number
  carbs_g: number
  fat_g: number
  fiber_g: number | null
  notes: string
  created_at: string
}

export interface HealthDailySummary {
  date: string
  steps: number | null
  active_calories: number | null
  resting_calories: number | null
  resting_heart_rate: number | null
  sleep_hours: number | null
}

export interface HealthWorkout {
  id: string
  activity_type: string
  start_time: string
  duration_seconds: number | null
  calories: number | null
  avg_heart_rate: number | null
  distance_meters: number | null
}

export interface HealthRecovery {
  score: number | null
  label: string
  today_rhr: number | null
  baseline_rhr: number | null
}

export interface HealthCalorieBalance {
  calories_in: number
  burned: number | null
  net: number | null
  target: number | null
  remaining: number | null
}

export interface HealthActivitySuggestion {
  suggested: string | null
  avg_calories?: number
  weekly_workouts?: number
  current?: string
}

export interface BodyScanResult {
  id: string
  body_fat_pct: number | null
  physique_category: string
  muscle_mass_note: string
  posture_notes: string
  recommendations: string
  disclaimer: string
  created_at: string
}
