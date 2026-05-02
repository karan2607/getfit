from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from . import views

urlpatterns = [
    path('health/', views.health, name='health'),
    # Auth
    path('auth/signup/', views.signup, name='signup'),
    path('auth/login/', views.login, name='login'),
    path('auth/logout/', views.logout, name='logout'),
    path('auth/me/', views.me, name='me'),
    path('auth/password-change/', views.password_change, name='password-change'),
    path('auth/password-reset/', views.password_reset_request, name='password-reset-request'),
    path('auth/password-reset/confirm/', views.password_reset_confirm, name='password-reset-confirm'),
    # Profile
    path('profile/', views.profile, name='profile'),
    # Chat
    path('chat/sessions/', views.chat_sessions, name='chat-sessions'),
    path('chat/sessions/<uuid:session_id>/', views.chat_session_detail, name='chat-session-detail'),
    path('chat/sessions/<uuid:session_id>/messages/', csrf_exempt(views.chat_message_stream), name='chat-message-stream'),
    # Workout Planner
    path('workouts/plans/generate/', views.workout_plan_generate, name='workout-plan-generate'),
    path('workouts/plans/', views.workout_plans, name='workout-plans'),
    path('workouts/plans/<uuid:plan_id>/', views.workout_plan_detail, name='workout-plan-detail'),
    path('workouts/plans/<uuid:plan_id>/activate/', views.workout_plan_activate, name='workout-plan-activate'),
    path('workouts/sessions/', views.workout_sessions, name='workout-sessions'),
    path('workouts/sessions/<uuid:session_id>/', views.workout_session_detail, name='workout-session-detail'),
    path('workouts/sessions/<uuid:session_id>/sets/', views.workout_session_log_set, name='workout-session-log-set'),
    path('workouts/exercises/<str:exercise_name>/history/', views.workout_exercise_history, name='workout-exercise-history'),
    # Diet Planner
    path('diet/plans/generate/', views.diet_plan_generate, name='diet-plan-generate'),
    path('diet/plans/', views.diet_plans, name='diet-plans'),
    path('diet/plans/<uuid:plan_id>/', views.diet_plan_detail, name='diet-plan-detail'),
    path('diet/plans/<uuid:plan_id>/activate/', views.diet_plan_activate, name='diet-plan-activate'),
    path('diet/food-scan/', views.food_scan, name='food-scan'),
    path('diet/food-scan/history/', views.food_scan_history, name='food-scan-history'),
    # Body Scanner
    path('body/scan/', views.body_scan, name='body-scan'),
    path('body/scan/history/', views.body_scan_history, name='body-scan-history'),
    # Meal Log
    path('diet/meal-logs/', views.meal_logs, name='meal-logs'),
    path('diet/meal-logs/<uuid:log_id>/', views.meal_log_detail, name='meal-log-detail'),
]
