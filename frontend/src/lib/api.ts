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
  /**
   * IANA name, reported by the browser. The server runs on UTC, so without
   * this every "today" was the server's day — at 11pm Eastern the health page
   * asked for tomorrow and showed 0 calories against 5 logged meals.
   */
  timezone: string
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
  /** Read-only. Gates superuser-only UI; the server enforces access itself. */
  is_superuser?: boolean
}

/** Structured extraction of a dietitian's written plan. */
export interface DietDocument {
  is_diet_document: boolean
  title: string | null
  target_calories: number | null
  protein_g: number | null
  carbs_g: number | null
  fat_g: number | null
  meals_per_day: number | null
  foods_to_include: string[]
  foods_to_avoid: string[]
  restrictions: string[]
  meal_timing: string[]
  notes: string[]
  /** Instruction block to pass back as `dietitian_notes` when generating. */
  summary: string
  saved_to_profile?: boolean
}

/** Account listing for superuser tooling. Never includes any password data. */
export interface AdminUser {
  id: string
  email: string
  name: string
  is_superuser: boolean
  is_active: boolean
  last_login: string | null
  created_at: string
}

export interface AdminResetLink {
  email: string
  name: string
  /** Single-use URL to hand to the user. Expires. */
  reset_url: string
  expires_at: string
  expires_in_hours: number
  superseded_links: number
}

/** A long-lived credential for an external integration (phone agent, script). */
export interface ApiKey {
  id: string
  label: string
  /** First 8 chars. The full key is returned ONCE, at creation. */
  prefix: string
  scope: string
  scope_display: string
  created_at: string
  /** Surfaced in the UI — a key that stopped being used is the failure mode. */
  last_used_at: string | null
  revoked_at: string | null
  is_active: boolean
  /** Present only in the create response. */
  key?: string
}

export type FeedbackStatus = 'new' | 'in_progress' | 'done' | 'failed' | 'skipped'

export interface Feedback {
  id: string
  body: string
  page: string
  status: FeedbackStatus
  result_notes: string
  commit_sha: string
  submitted_by: string
  created_at: string
  updated_at: string
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

    // Superuser-only. There is no outbound email configured, so the operator
    // mints a reset link and hands it to the user directly. The server returns
    // 403 for anyone who isn't a superuser.
    adminListUsers: () =>
      request<AdminUser[]>('/api/auth/admin/users/'),

    adminIssueResetLink: (email: string, hours?: number) =>
      request<AdminResetLink>('/api/auth/admin/reset-link/', {
        method: 'POST',
        body: JSON.stringify({
          email,
          hours,
          frontend_base: window.location.origin,
        }),
      }),
  },

  profile: {
    get: () =>
      request<UserProfile>('/api/profile/'),

    update: (data: Partial<UserProfile>) =>
      request<UserProfile>('/api/profile/', { method: 'PATCH', body: JSON.stringify(data) }),

    listApiKeys: () =>
      request<ApiKey[]>('/api/profile/api-keys/'),

    /** The response is the only time the full key is returned. */
    createApiKey: (label: string, scope = 'logging') =>
      request<ApiKey>('/api/profile/api-keys/', {
        method: 'POST', body: JSON.stringify({ label, scope }),
      }),

    revokeApiKey: (id: string) =>
      request<ApiKey>(`/api/profile/api-keys/${id}/`, { method: 'DELETE' }),
  },

  chat: {
    listSessions: () =>
      request<ChatSession[]>('/api/chat/sessions/'),

    createSession: (opts?: { source?: string }) =>
      request<ChatSession>('/api/chat/sessions/', { method: 'POST', body: JSON.stringify(opts ?? {}) }),

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
    suggestTarget: (data: {
      fitness_goal?: string
      experience_level?: string
      days_per_week: number
      duration_weeks: number
      equipment?: string
      specific_goals?: string[]
      goal_params?: Record<string, unknown>
      notes?: string
      body_context?: string
      user_question?: string
      previous_recommendation?: string
    }) =>
      request<{ message: string; program_target: WorkoutPlan['program_target'] }>(
        '/api/workouts/plans/suggest-target/', { method: 'POST', body: JSON.stringify(data) }
      ),

    generatePlan: (data: {
      days_per_week: number
      duration_weeks: number
      fitness_goal?: string
      experience_level?: string
      equipment?: string
      notes?: string
      body_context?: string
      body_photo?: File
      specific_goals?: string[]
      goal_params?: Record<string, unknown>
      confirmed_target?: WorkoutPlan['program_target']
    }) => {
      if (data.body_photo) {
        const fd = new FormData()
        Object.entries(data).forEach(([k, v]) => {
          if (k === 'body_photo') fd.append('body_photo', v as File)
          else if (k === 'specific_goals' || k === 'goal_params' || k === 'confirmed_target') fd.append(k, JSON.stringify(v))
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

    advanceWeek: (id: string) =>
      request<WorkoutPlan & { program_complete?: boolean; detail?: string }>(`/api/workouts/plans/${id}/advance-week/`, { method: 'POST' }),

    prepareWeek: (id: string) =>
      request<{ generated: number; exercises: string[] }>(`/api/workouts/plans/${id}/prepare-week/`, { method: 'POST' }),

    markRestDayDone: (planId: string, dayId: string) =>
      request<WorkoutPlan>(`/api/workouts/plans/${planId}/mark-rest-day/`, {
        method: 'POST',
        body: JSON.stringify({ day_id: dayId }),
      }),

    listSessions: () =>
      request<WorkoutSessionSummary[]>('/api/workouts/sessions/'),

    startSession: (exercise_day_id: string, force = false) =>
      request<WorkoutSessionDetail>('/api/workouts/sessions/', {
        method: 'POST',
        body: JSON.stringify({ exercise_day_id, ...(force ? { force: true } : {}) }),
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
      // null explicitly clears a field; omitting a key leaves it untouched.
      reps_completed?: number | null
      weight_kg?: number | null
      duration_seconds?: number | null
      is_completed?: boolean
      notes?: string
    }) =>
      request<SetLog>(`/api/workouts/sessions/${sessionId}/sets/`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),

    /**
     * Start generating one week in the background. 202 when it started,
     * 200 when the week already exists (a race with the auto-trigger),
     * 409 when another week is mid-flight.
     */
    prepareNextWeek: (planId: string, week: number, notes?: string) =>
      request<{
        detail: string; started: boolean
        preparing_week: number | null; weeks_ready: number
      }>(`/api/workouts/plans/${planId}/weeks/${week}/prepare/`, {
        method: 'POST', body: JSON.stringify({ notes: notes ?? '' }),
      }),

    /** Replace a day's superset grouping atomically. Empty list clears it. */
    setDaySupersets: (dayId: string, groups: string[][]) =>
      request<WorkoutDay>(`/api/workouts/days/${dayId}/supersets/`, {
        method: 'POST', body: JSON.stringify({ groups }),
      }),

    /** Only pairings the server's validator accepts, each with a reason. */
    getSupersetOptions: (dayId: string) =>
      request<{
        max_groups: number
        max_members: number
        exercises: Array<{
          id: string; name: string
          partners: SupersetPartner[]
          /**
           * Pairings the server REFUSES, with the reason. Returned rather
           * than omitted: the user can see the exercise is on the day, so an
           * absence reads as a bug. "Both push, both chest" is an answer.
           */
          rejected: SupersetPartner[]
        }>
      }>(`/api/workouts/days/${dayId}/superset-options/`),

    listExercises: () =>
      request<ExerciseSummary[]>('/api/workouts/exercises/'),

    getExerciseHistory: (exerciseName: string) =>
      request<ExerciseHistory>(`/api/workouts/exercises/${encodeURIComponent(exerciseName)}/history/`),

    /**
     * `exerciseId` pins the advice to the exact prescription on the card. One
     * name carries different rep targets across weeks and plans, so without it
     * the server has to guess which row to coach against.
     */
    getExerciseGuide: (exerciseName: string, exerciseId?: string) =>
      request<{ steps: string[]; muscles: string[]; tips: string[]; category: string; images: string[] }>(
        `/api/workouts/exercises/guide/?name=${encodeURIComponent(exerciseName)}`
        + (exerciseId ? `&exercise_id=${encodeURIComponent(exerciseId)}` : '')
      ),
  },

  diet: {
    generatePlan: (data?: { country?: string; cuisine_preference?: string; usual_foods?: string; duration_days?: number; dietitian_notes?: string }) =>
      request<DietPlanPreview>('/api/diet/plans/generate/', { method: 'POST', body: JSON.stringify(data ?? {}) }),

    /**
     * Extract a dietitian's written plan from a PDF or photo.
     * Parse-and-discard: the file is never stored server-side.
     */
    parseDocument: (file: File, saveToProfile = false) => {
      const form = new FormData()
      form.append('document', file)
      if (saveToProfile) form.append('save_to_profile', 'true')
      return request<DietDocument>('/api/diet/documents/parse/', {
        method: 'POST',
        body: form,
      })
    },

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
      request<{ connected: boolean; provider: string | null; connected_at: string | null; last_sync_at: string | null; has_data?: boolean; latest_summary_date: string | null }>('/api/health/status/'),

    getSummary: () =>
      request<HealthDailySummary[]>('/api/health/summary/'),

    /** Bucketed trends. Server-side aggregation, shared with the MCP tool. */
    getSeries: (range: HealthRange) =>
      request<HealthSeries>(`/api/health/series/?range=${range}`),

    rotateSyncToken: () =>
      request<{ token: string; detail: string }>('/api/health/connect/rotate/', {
        method: 'POST',
      }),

    getWorkouts: () =>
      request<HealthWorkout[]>('/api/health/workouts/'),

    /**
     * Correct one day. Send `null` for a field to CLEAR it; omit the key to
     * leave it alone. The sync endpoint deliberately does the opposite and
     * ignores nulls, so a partial Shortcut payload can never wipe data.
     */
    updateSummary: (date: string, fields: Partial<Record<
      'steps' | 'active_calories' | 'resting_calories'
      | 'resting_heart_rate' | 'sleep_hours', number | null>>) =>
      request<HealthDailySummary & { cleared: string[] }>(
        `/api/health/summary/${date}/`,
        { method: 'PATCH', body: JSON.stringify(fields) }),

    deleteSummary: (date: string) =>
      request<{ detail: string; date: string }>(
        `/api/health/summary/${date}/`, { method: 'DELETE' }),

    deleteWorkout: (id: string) =>
      request<{ detail: string; id: string }>(
        `/api/health/workouts/${id}/`, { method: 'DELETE' }),

    disconnect: () =>
      request<void>('/api/health/connect/', { method: 'DELETE' }),

    getRecovery: () =>
      request<HealthRecovery>('/api/health/recovery/'),

    getCalorieBalance: () =>
      request<HealthCalorieBalance>('/api/health/calorie-balance/'),

    getActivitySuggestion: () =>
      request<HealthActivitySuggestion>('/api/health/activity-suggestion/'),
  },

  // Superuser-only. The server returns 403 for everyone else; the UI hides
  // these affordances as a convenience, not as the access control.
  feedback: {
    submit: (data: { body: string; page?: string }) =>
      request<Feedback>('/api/feedback/', {
        method: 'POST',
        body: JSON.stringify(data),
      }),

    list: (status?: FeedbackStatus) =>
      request<Feedback[]>(
        `/api/feedback/${status ? `?status=${status}` : ''}`
      ),

    update: (id: string, data: {
      status?: FeedbackStatus
      result_notes?: string
      commit_sha?: string
    }) =>
      request<Feedback>(`/api/feedback/${id}/`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),

    remove: (id: string) =>
      request<void>(`/api/feedback/${id}/`, { method: 'DELETE' }),
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

export type MeasurementType = 'weight_reps' | 'weight_duration' | 'duration' | 'warmup' | 'bodyweight_reps'

export interface Exercise {
  id: string
  name: string
  sets: number
  reps: string
  rest_seconds: number | null
  notes: string
  order: number
  measurement_type: MeasurementType
  /**
   * Exercises on the same day sharing a group are performed back to back.
   * null for almost everything. Members are contiguous in `order` and
   * server-validated, so the UI can group consecutive matches safely.
   */
  /**
   * The composite (superset/triset) this exercise belongs to, or null.
   * Replaced `superset_group`: the client needs a shared identifier AND the
   * group's rest, and a bare integer could only supply the first.
   */
  composite_id: string | null
  composite_rest_seconds: number | null
}

export interface WorkoutDay {
  id: string
  day_number: number
  week_number: number
  name: string
  focus: string
  is_rest_day: boolean
  order: number
  exercises: Exercise[]
  last_completed_at: string | null
}

export interface ProgramPhase {
  start_week: number
  end_week: number
  name: string
  focus: string
}

export interface WorkoutPlan {
  id: string
  title: string
  description: string
  is_active: boolean
  activated_at: string | null
  generated_by_ai: boolean
  duration_weeks: number | null
  current_week: number
  current_day_order: number
  specific_goal: string
  // `unit` is explicit so a "lose 2 kg" goal cannot be re-read as 2 lb. It used
  // to survive only inside the free-text `label`, which the AI then guessed at.
  program_target: {
    metric: string
    label: string
    recommended_value: number
    current_value?: number
    unit?: string
  } | null
  goal_check_in_shown: boolean
  /** 'bodyweight' | 'home gym' | 'full gym'; '' when unknown (older plans). */
  equipment?: string
  data_version?: number
  /**
   * Phase structure for the whole program. Lets the UI show the shape of
   * weeks that have not been generated yet. Ranges are integers and
   * server-validated to tile 1..duration_weeks exactly.
   */
  program_outline: ProgramPhase[] | null
  /**
   * Highest week that actually has days — DERIVED server-side from the days
   * that exist, so it cannot drift. Plans created before per-week generation
   * report their full duration.
   */
  weeks_ready: number
  /** Week currently being generated in the background, if any. */
  preparing_week: number | null
  /** True only while a preparation is live (a stale lock reads false). */
  is_preparing: boolean
  prepare_error: string
  created_at: string
  updated_at: string
}

export interface WorkoutPlanDetail extends WorkoutPlan {
  days: WorkoutDay[]
}

export type PreviewDay = {
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
    // Must round-trip. Plan edits go through replacePlan (a full PUT), so
    // omitting this field here is what used to strip measurement types back to
    // the default on every AI edit. The server re-classifies when it is absent,
    // but sending it preserves any deliberate value.
    measurement_type?: MeasurementType
  }>
}

export interface SupersetPartner {
  id: string
  name: string
  /** An accepted kind (antagonist/non_competing/exhaust) or a rejection code. */
  kind: string
  reason: string
  same_sets: boolean
}

export interface WorkoutPlanPreview {
  title: string
  description?: string
  duration_weeks?: number
  specific_goal?: string
  program_target?: WorkoutPlan['program_target']
  generated_by_ai?: boolean
  /** Week 1, from per-week generation. The shape new plans arrive in. */
  week?: PreviewDay[]
  /** Phase structure for the whole program; weeks 2+ are generated later. */
  program_outline?: ProgramPhase[]
  /** Notes keyed by exercise name, emitted once instead of per week. */
  exercise_notes?: Record<string, string>
  days?: PreviewDay[]
  /** Legacy whole-plan shape. Still accepted on save. */
  weeks?: PreviewDay[][]
  /**
   * The gym type chosen before generating. Echoed back by the generate
   * endpoint so that saving the preview PERSISTS it -- it used to be sent,
   * used once for week 1, and then thrown away, leaving every later week
   * generated with no idea what equipment existed.
   */
  equipment?: string
}

export interface SetLog {
  id: string
  exercise_id: string | null
  exercise_name: string
  set_number: number
  // Which of these three is meaningful depends on measurement_type. The server
  // forces the others to null, so never infer from "has a value".
  reps_completed: number | null
  weight_kg: number | null          // always kilograms
  duration_seconds: number | null
  measurement_type: MeasurementType | null
  is_completed: boolean
  notes: string
}

export interface WorkoutSessionDetail {
  id: string
  plan_id: string | null
  day_name: string
  exercise_day: WorkoutDay | null
  started_at: string
  completed_at: string | null
  notes: string
  is_completed: boolean
  set_logs: SetLog[]
  already_active?: boolean
  conflict_day_name?: string
}

export interface WorkoutSessionSummary {
  id: string
  day_name: string
  exercise_day_id: string | null
  started_at: string
  completed_at: string | null
  is_completed: boolean
}

export interface ExerciseSummary {
  exercise_name: string
  last_session: string
  total_sessions: number
  measurement_type: MeasurementType
  best_weight_kg: number | null
  best_reps: number | null
  best_duration_seconds: number | null
}

export interface ExerciseHistoryPoint {
  workout_session__started_at: string
  set_number: number
  weight_kg: number | null
  reps_completed: number | null
  duration_seconds: number | null
}

/**
 * History response. Carries the measurement type and the field that drives the
 * chart, so the caller never has to guess whether a number is kg or seconds.
 */
export interface ExerciseHistory {
  exercise_name: string
  measurement_type: MeasurementType
  metric: 'weight_kg' | 'reps_completed' | 'duration_seconds' | null
  logs: ExerciseHistoryPoint[]
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

export type HealthRange = 'daily' | 'weekly' | 'monthly'

/** One time bucket. Metrics are PER-DAY AVERAGES within the bucket. */
export interface HealthBucket {
  start: string
  end: string
  label: string
  /** How many days in this bucket actually had data — a partial week is not a slump. */
  days_with_data: number
  steps: number | null
  active_calories: number | null
  resting_calories: number | null
  resting_heart_rate: number | null
  sleep_hours: number | null
}

export interface HealthMetricSummary {
  latest: number | null
  previous: number | null
  change: number | null
  change_pct: number | null
  points?: number
}

export interface HealthSeries {
  range: HealthRange
  bucket: 'day' | 'week' | 'month'
  from: string
  to: string
  buckets: HealthBucket[]
  summary: Record<string, HealthMetricSummary>
  has_data: boolean
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
  burn_target: number | null
  remaining_eat: number | null
  remaining_burn: number | null
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
