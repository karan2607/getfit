import json
import logging
import re
from datetime import timedelta
from django.contrib.auth import get_user_model, authenticate
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Value, When

logger = logging.getLogger(__name__)
from django.http import StreamingHttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import (
    PasswordResetToken, UserProfile, ChatSession, ChatMessage,
    WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
    MealLog, DietPlan, Meal, FoodScanResult, BodyScanResult,
    ExerciseGuide, MealGuide,
    HealthConnection, HealthDailySummary, HealthWorkout,
    Feedback,
)
from .serializers import (
    RegisterSerializer,
    UserSerializer,
    UserProfileSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    PasswordChangeSerializer,
    UpdateMeSerializer,
    ChatSessionSerializer,
    ChatSessionDetailSerializer,
    ChatMessageSerializer,
    WorkoutPlanSerializer,
    WorkoutPlanDetailSerializer,
    WorkoutSessionSerializer,
    WorkoutSessionListSerializer,
    SetLogSerializer,
    MealLogSerializer,
    DietPlanSerializer,
    DietPlanDetailSerializer,
    FoodScanResultSerializer,
    BodyScanResultSerializer,
    FeedbackSerializer,
)
from .email import send_password_reset_email
from .gemini import stream_gemini_chat, call_gemini_json, GeminiError
from . import exercise_types

User = get_user_model()


@api_view(['GET', 'HEAD'])
@permission_classes([AllowAny])
def health(request):
    return Response({'status': 'ok'})


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    serializer = RegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    user = serializer.save()
    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key, 'user': UserSerializer(user).data}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    email = request.data.get('email', '').lower()
    password = request.data.get('password', '')
    user = authenticate(request, username=email, password=password)
    if user is None:
        return Response({'detail': 'Invalid email or password.'}, status=status.HTTP_400_BAD_REQUEST)
    token, _ = Token.objects.get_or_create(user=user)
    return Response({'token': token.key, 'user': UserSerializer(user).data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    request.user.auth_token.delete()
    return Response({'detail': 'Logged out.'})


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def me(request):
    if request.method == 'PATCH':
        ser = UpdateMeSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        update_fields = []
        if 'name' in ser.validated_data:
            request.user.name = ser.validated_data['name']
            update_fields.append('name')
        if 'email' in ser.validated_data:
            new_email = ser.validated_data['email'].lower()
            if User.objects.filter(email=new_email).exclude(pk=request.user.pk).exists():
                return Response({'email': 'This email is already in use.'}, status=status.HTTP_400_BAD_REQUEST)
            request.user.email = new_email
            update_fields.append('email')
        if update_fields:
            request.user.save(update_fields=update_fields)
        return Response(UserSerializer(request.user).data)
    return Response(UserSerializer(request.user).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def password_change(request):
    ser = PasswordChangeSerializer(data=request.data)
    if not ser.is_valid():
        return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
    if not request.user.check_password(ser.validated_data['current_password']):
        return Response({'detail': 'Current password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
    request.user.set_password(ser.validated_data['new_password'])
    request.user.save()
    Token.objects.filter(user=request.user).delete()
    token = Token.objects.create(user=request.user)
    return Response({'token': token.key})


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    serializer = PasswordResetRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    email = serializer.validated_data['email'].lower()
    frontend_base = request.data.get('frontend_base', 'http://localhost:5173')
    user = User.objects.filter(email=email).first()

    if user:
        token = PasswordResetToken.objects.create(user=user)
        reset_url = f'{frontend_base}/reset-password/{token.token}'
        try:
            send_password_reset_email(to_email=user.email, user_name=user.name, reset_url=reset_url)
        except Exception:
            pass

    return Response({'detail': 'If an account with that email exists, a reset link has been sent.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_confirm(request):
    serializer = PasswordResetConfirmSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    token_str = serializer.validated_data['token']
    new_password = serializer.validated_data['password']

    token = get_object_or_404(PasswordResetToken, token=token_str)
    if not token.is_valid:
        return Response({'detail': 'This reset link has expired or already been used.'}, status=status.HTTP_400_BAD_REQUEST)

    user = token.user
    user.set_password(new_password)
    user.save()

    from django.utils import timezone as tz
    token.used_at = tz.now()
    token.save(update_fields=['used_at'])

    return Response({'detail': 'Password reset successfully. You can now log in.'})


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@api_view(['GET', 'PUT', 'PATCH'])
@permission_classes([IsAuthenticated])
def profile(request):
    prof, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'GET':
        return Response(UserProfileSerializer(prof).data)

    partial = request.method == 'PATCH'
    ser = UserProfileSerializer(prof, data=request.data, partial=partial)
    if not ser.is_valid():
        return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
    ser.save()
    return Response(ser.data)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def _build_system_prompt(user, plan_id=None, plan_context=None, day_id=None, diet_plan_id=None, meal_id=None) -> str:
    preferred_unit = 'lb'
    try:
        p = user.profile
        preferred_unit = getattr(p, 'preferred_unit', 'lb') or 'lb'
        body_weight = None
        if p.weight_kg:
            if preferred_unit == 'lb':
                body_weight = f'{round(p.weight_kg * 2.20462, 1)} lb'
            else:
                body_weight = f'{p.weight_kg} kg'
        profile_lines = [
            f'- Name: {user.name}',
            f'- Height: {p.height_cm}cm' if p.height_cm else None,
            f'- Body weight: {body_weight}' if body_weight else None,
            f'- Age: {p.age}' if p.age else None,
            f'- Gender: {p.gender}' if p.gender else None,
            f'- Fitness goal: {p.fitness_goal}' if p.fitness_goal else None,
            f'- Experience level: {p.experience_level}' if p.experience_level else None,
            f'- Dietary preference: {p.dietary_preference}' if p.dietary_preference else None,
            f'- Activity level: {p.activity_level}' if p.activity_level else None,
            f'- Preferred weight unit: {preferred_unit}',
        ]
        profile_text = '\n'.join(line for line in profile_lines if line)
    except Exception:
        profile_text = f'- Name: {user.name}'

    plan_schema = (
        '{\n'
        '  "title": "string",\n'
        '  "description": "string",\n'
        '  "duration_weeks": number,\n'
        '  "weeks": [\n'
        '    [\n'
        '      {\n'
        '        "day_number": number,\n'
        '        "name": "string",\n'
        '        "focus": "string",\n'
        '        "is_rest_day": boolean,\n'
        '        "exercises": [\n'
        '          {"name": "string", "sets": number, "reps": "string", "rest_seconds": number, "notes": "string"}\n'
        '        ]\n'
        '      }\n'
        '    ]\n'
        '    // one sub-array per week — must equal duration_weeks total\n'
        '  ]\n'
        '}'
    )

    # Personal notes (injuries, preferences)
    notes_text = ''
    try:
        if user.profile.personal_notes:
            notes_text = f'\nPersonal notes / injuries / preferences:\n{user.profile.personal_notes}'
    except Exception:
        pass

    # Today's meal log summary
    meal_text = ''
    try:
        from django.utils import timezone as _tz
        today = _tz.now().date()
        todays_logs = MealLog.objects.filter(user=user, date=today)
        if todays_logs.exists():
            total_cal = sum(m.calories for m in todays_logs)
            total_p = sum(m.protein_g for m in todays_logs)
            total_c = sum(m.carbs_g for m in todays_logs)
            total_f = sum(m.fat_g for m in todays_logs)
            meal_names = ', '.join(m.food_name for m in todays_logs[:5])
            meal_text = (
                f"\nToday's nutrition log: {total_cal} kcal eaten "
                f"({total_p:.0f}g protein, {total_c:.0f}g carbs, {total_f:.0f}g fat). "
                f"Foods: {meal_names}."
            )
    except Exception:
        pass

    # Health data context — last 7 days
    health_text = ''
    try:
        health_rows = HealthDailySummary.objects.filter(user=user).order_by('-date')[:7]
        if health_rows:
            health_lines = []
            for row in health_rows:
                parts = [f"  {row.date}:"]
                if row.steps is not None:
                    parts.append(f"steps={row.steps}")
                if row.active_calories is not None:
                    parts.append(f"active_cal={row.active_calories:.0f}kcal")
                if row.resting_heart_rate is not None:
                    parts.append(f"rhr={row.resting_heart_rate:.0f}bpm")
                health_lines.append(" ".join(parts))
            health_text = "\nRecent health data (last 7 days):\n" + "\n".join(health_lines) + "\n"
    except Exception:
        pass

    # Recent weight history — last 3 completed sessions
    weight_history_text = ''
    try:
        from .models import WorkoutSession, SetLog as _SetLog
        recent_sessions = (
            WorkoutSession.objects
            .filter(user=user, is_completed=True)
            .prefetch_related('set_logs')
            .order_by('-completed_at')[:3]
        )
        session_lines = []
        for ws in recent_sessions:
            date_str = ws.completed_at.strftime('%b %d') if ws.completed_at else 'unknown date'
            sets_by_exercise: dict = {}
            for sl in ws.set_logs.filter(is_completed=True, weight_kg__isnull=False):
                name = sl.exercise_name
                w_kg = sl.weight_kg
                if preferred_unit == 'lb':
                    w_display = f'{round(w_kg * 2.20462, 1)} lb'
                else:
                    w_display = f'{w_kg} kg'
                reps = f'×{sl.reps_completed}' if sl.reps_completed else ''
                sets_by_exercise.setdefault(name, []).append(f'{w_display}{reps}')
            if sets_by_exercise:
                exercises_str = '; '.join(f'{ex}: {", ".join(sets)}' for ex, sets in sets_by_exercise.items())
                session_lines.append(f'  {date_str}: {exercises_str}')
        if session_lines:
            weight_history_text = '\n\nRecent workout weight history (use this to suggest appropriate weights and track progression):\n' + '\n'.join(session_lines)
    except Exception:
        pass

    # Current workout plan context (when chatting from the plan detail or preview page)
    plan_text = ''
    if plan_id:
        try:
            import json as _json2
            from .models import WorkoutPlan
            plan_obj = WorkoutPlan.objects.prefetch_related('days__exercises').get(pk=plan_id, user=user)
            weeks_map: dict = {}
            for day in plan_obj.days.order_by('week_number', 'order').prefetch_related('exercises'):
                wk = day.week_number or 1
                if wk not in weeks_map:
                    weeks_map[wk] = []
                weeks_map[wk].append({
                    'day_number': day.day_number,
                    'name': day.name,
                    'focus': day.focus or '',
                    'is_rest_day': day.is_rest_day,
                    'exercises': [
                        {'name': ex.name, 'sets': ex.sets, 'reps': ex.reps,
                         'rest_seconds': ex.rest_seconds, 'notes': ex.notes or ''}
                        for ex in day.exercises.all()
                    ],
                })
            weeks_list = [weeks_map[wk] for wk in sorted(weeks_map.keys())]
            plan_json = _json2.dumps({
                'title': plan_obj.title,
                'description': plan_obj.description or '',
                'duration_weeks': plan_obj.duration_weeks,
                'weeks': weeks_list,
            }, indent=2)
            day_focus_text = ''
            if day_id:
                try:
                    from .models import WorkoutDay
                    day_obj = WorkoutDay.objects.prefetch_related('exercises').get(pk=day_id, plan=plan_obj)
                    day_exercises = [
                        f'  - {ex.name}: {ex.sets} sets × {ex.reps}'
                        + (f', {ex.rest_seconds}s rest' if ex.rest_seconds else '')
                        + (f' | {ex.notes}' if ex.notes else '')
                        for ex in day_obj.exercises.order_by('order')
                    ]
                    day_focus_text = (
                        f'\n\nThe user clicked "Edit" on Day {day_obj.day_number}: {day_obj.name}'
                        + (f' ({day_obj.focus})' if day_obj.focus else '')
                        + '.\nCurrent exercises for this day:\n'
                        + '\n'.join(day_exercises)
                        + '\n\nFocus your suggestions on this specific day. '
                        'When outputting an updated plan, include ALL days in the workout-plan block but only modify this day unless the user asks otherwise.'
                    )
                except Exception:
                    pass
            plan_text = (
                f'\n\nCURRENT WORKOUT PLAN (the user is viewing this plan and may ask you to modify it):\n'
                f'```json\n{plan_json}\n```\n'
                'When the user asks to modify, adjust, swap exercises, or update this plan, '
                'output the COMPLETE updated plan as a "workout-plan" code block using the weeks[][] format. '
                'Include ALL weeks in the output — never omit weeks or collapse to a single week. '
                'If the user asks to change duration_weeks, adjust the number of sub-arrays in weeks accordingly. '
                'Preserve existing week structure unless the user explicitly asks to change it.'
                f'{day_focus_text}'
            )
        except Exception:
            pass
    elif plan_context:
        plan_text = (
            f'\n\nCURRENT WORKOUT PLAN PREVIEW (not yet saved — the user is reviewing this generated plan):\n'
            f'```json\n{plan_context}\n```\n'
            'The user wants to refine this plan before saving it. '
            'When they describe changes, output the COMPLETE updated plan as a "workout-plan" code block using the weeks[][] format. '
            'Include ALL weeks in the output — never collapse to a single week. '
            'If the user asks to change duration_weeks, update the number of sub-arrays accordingly.'
        )

    # Diet plan context (when chatting from a diet plan detail page)
    diet_plan_text = ''
    if diet_plan_id:
        try:
            import json as _json3
            from .models import DietPlan as _DietPlan
            diet_obj = _DietPlan.objects.prefetch_related('meals').get(pk=diet_plan_id, user=user)
            meals_data = [
                {
                    'day_number': m.day_number,
                    'meal_type': m.meal_type,
                    'name': m.name,
                    'calories': m.calories,
                    'protein_g': m.protein_g,
                    'carbs_g': m.carbs_g,
                    'fat_g': m.fat_g,
                    'description': m.description or '',
                }
                for m in diet_obj.meals.order_by('day_number', 'order')
            ]
            diet_json = _json3.dumps({
                'title': diet_obj.title,
                'description': diet_obj.description or '',
                'target_calories': diet_obj.target_calories,
                'protein_g': diet_obj.protein_g,
                'carbs_g': diet_obj.carbs_g,
                'fat_g': diet_obj.fat_g,
                'meals': meals_data,
            }, indent=2)
            diet_schema = (
                '{\n'
                '  "title": "string",\n'
                '  "description": "string",\n'
                '  "target_calories": number,\n'
                '  "protein_g": number,\n'
                '  "carbs_g": number,\n'
                '  "fat_g": number,\n'
                '  "meals": [\n'
                '    {\n'
                '      "day_number": number,\n'
                '      "meal_type": "breakfast|lunch|dinner|snack",\n'
                '      "name": "string",\n'
                '      "description": "string",\n'
                '      "calories": number,\n'
                '      "protein_g": number,\n'
                '      "carbs_g": number,\n'
                '      "fat_g": number\n'
                '    }\n'
                '  ]\n'
                '}'
            )
            diet_plan_text = (
                f'\n\nCURRENT DIET PLAN (the user is viewing this plan and may ask you to modify it):\n'
                f'```json\n{diet_json}\n```\n'
                'When the user asks to modify, adjust meals, change macros, or update this plan, '
                'output the COMPLETE updated plan as a fenced code block with the language identifier "diet-plan" '
                'followed by valid JSON matching this schema:\n'
                f'```diet-plan\n{diet_schema}\n```\n'
                'Include ALL days and ALL meals. Do not truncate the JSON.'
            )
        except Exception:
            pass

    meal_context_text = ''
    if meal_id and diet_plan_id:
        try:
            meal_obj = Meal.objects.get(pk=meal_id)
            meal_context_text = (
                f'\n\nYOU ARE EDITING A SPECIFIC MEAL: "{meal_obj.name}" '
                f'(Day {meal_obj.day_number}, {meal_obj.meal_type}). '
                f'Current macros: {meal_obj.calories} kcal, {meal_obj.protein_g}g protein, '
                f'{meal_obj.carbs_g}g carbs, {meal_obj.fat_g}g fat. '
                f'Description: {meal_obj.description or "none"}. '
                'Only change this meal. Keep all other meals in the plan identical. '
                'Output the COMPLETE updated diet plan as a diet-plan block.'
            )
        except Exception:
            pass

    return (
        'You are a certified AI personal trainer and fitness coach. '
        'Respond conversationally, helpfully, and concisely. '
        'Use markdown for formatting when helpful (e.g. bullet lists for exercises). '
        'Always recommend consulting a doctor for medical concerns.\n\n'
        f'Always express weights in {preferred_unit}. '
        'When suggesting weights for exercises, put the recommendation in the exercise "notes" field only '
        f'(e.g. "Start at 135 lb. Add 5 lb when all sets are clean."). '
        'The "reps" field should be a rep range or scheme like "8-15", "8-25", "AMRAP", "30 sec" — never include weight in reps.\n\n'
        'WORKOUT PLAN CREATION: When the user asks you to create, generate, or build a workout plan, '
        'respond with a brief intro sentence, then output the complete plan as a fenced code block '
        'with the language identifier "workout-plan" followed by valid JSON matching this schema:\n'
        f'```workout-plan\n{plan_schema}\n```\n'
        'Always include all days (including rest days). Do not truncate the JSON.\n\n'
        f'User profile:\n{profile_text}'
        f'{notes_text}'
        f'{meal_text}'
        f'{health_text}'
        f'{weight_history_text}'
        f'{plan_text}'
        f'{diet_plan_text}'
        f'{meal_context_text}'
    )


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def chat_sessions(request):
    if request.method == 'GET':
        sessions = ChatSession.objects.filter(user=request.user, source=ChatSession.SOURCE_MAIN)
        return Response(ChatSessionSerializer(sessions, many=True).data)

    source = request.data.get('source', ChatSession.SOURCE_MAIN)
    session = ChatSession.objects.create(user=request.user, source=source)
    return Response(ChatSessionSerializer(session).data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def chat_session_detail(request, session_id):
    session = get_object_or_404(ChatSession, pk=session_id, user=request.user)

    if request.method == 'GET':
        return Response(ChatSessionDetailSerializer(session).data)

    if request.method == 'PATCH':
        title = request.data.get('title', '').strip()
        if title:
            session.title = title[:100]
            session.save(update_fields=['title'])
        return Response(ChatSessionSerializer(session).data)

    session.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@permission_classes([IsAuthenticated])
def chat_message_stream(request, session_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])

    from rest_framework.authentication import TokenAuthentication
    from rest_framework.exceptions import AuthenticationFailed

    auth = TokenAuthentication()
    try:
        user_auth_tuple = auth.authenticate(request)
    except AuthenticationFailed:
        from django.http import HttpResponse
        return HttpResponse(status=401)

    if user_auth_tuple is None:
        from django.http import HttpResponse
        return HttpResponse(status=401)

    user, _ = user_auth_tuple

    import json as _json
    try:
        body = _json.loads(request.body)
    except _json.JSONDecodeError:
        from django.http import HttpResponse
        return HttpResponse(status=400)

    content = body.get('content', '').strip()
    if not content:
        from django.http import HttpResponse
        return HttpResponse(status=400)

    plan_id = body.get('plan_id')
    plan_context = body.get('plan_context')  # raw plan JSON for unsaved previews
    day_id = body.get('day_id')
    diet_plan_id = body.get('diet_plan_id')
    meal_id = body.get('meal_id')

    try:
        session = ChatSession.objects.get(pk=session_id, user=user)
    except ChatSession.DoesNotExist:
        from django.http import HttpResponse
        return HttpResponse(status=404)

    ChatMessage.objects.create(session=session, role='user', content=content)

    history = [
        {'role': msg.role if msg.role == 'user' else 'model', 'parts': [{'text': msg.content}]}
        for msg in reversed(list(session.messages.order_by('-created_at')[:20]))
    ]

    system_prompt = _build_system_prompt(user, plan_id=plan_id, plan_context=plan_context, day_id=day_id, diet_plan_id=diet_plan_id, meal_id=meal_id)

    def event_stream():
        full_response = ''
        assistant_msg = None
        try:
            for chunk in stream_gemini_chat(system_prompt=system_prompt, history=history):
                full_response += chunk
                yield f'data: {_json.dumps({"chunk": chunk})}\n\n'
        except GeminiError as e:
            yield f'data: {_json.dumps({"error": str(e)})}\n\n'
            return
        finally:
            if full_response:
                assistant_msg = ChatMessage.objects.create(
                    session=session, role='assistant', content=full_response
                )
                session.title = _auto_title(session, content)
                session.save(update_fields=['title', 'updated_at'])

        if assistant_msg:
            yield f'data: {_json.dumps({"done": True, "message_id": str(assistant_msg.id)})}\n\n'

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['X-Accel-Buffering'] = 'no'
    response['Cache-Control'] = 'no-cache'
    return response


def _auto_title(session: ChatSession, first_user_message: str) -> str:
    if session.title != 'New Chat':
        return session.title
    words = first_user_message.split()
    title = ' '.join(words[:6])
    return (title[:97] + '...') if len(title) > 97 else title or 'New Chat'


# ---------------------------------------------------------------------------
# Workout Planner
# ---------------------------------------------------------------------------

def _workout_generate_prompt(profile, days_per_week: int, duration_weeks: int,
                              fitness_goal: str = '', experience_level: str = '',
                              equipment: str = '', notes: str = '',
                              body_context: str = '', preferred_unit: str = 'lb',
                              specific_goals: list = None, goal_params: dict = None,
                              confirmed_target: dict = None,
                              past_exercises: list = None) -> tuple[str, str]:
    goal_map = {'lose_fat': 'fat loss', 'build_muscle': 'muscle gain', 'maintain': 'maintenance'}
    goal = goal_map.get(fitness_goal or getattr(profile, 'fitness_goal', '') or '', 'general fitness')
    level = experience_level or getattr(profile, 'experience_level', 'beginner') or 'beginner'
    equip = equipment or 'full gym'

    system = (
        f'You are a certified personal trainer, strength and conditioning coach, and sports scientist '
        f'with 15+ years of experience. Design evidence-based programs for clients of all levels. '
        f'Return valid JSON only — no markdown, no explanation outside the JSON object. '
        f'Express all weights in {preferred_unit}.\n\n'

        'YOUR DESIGN PRINCIPLES:\n'
        '- Read every detail of the client profile before designing anything\n'
        '- Apply periodization based on goal + experience — never a generic template\n'
        '- Every exercise choice must serve the goal\n'
        '- Progressive overload is non-negotiable: the program MUST get harder week to week\n\n'

        'PERIODIZATION STYLES:\n'
        'LINEAR (beginners): Same exercises each week, increase weight linearly. '
        'Full-body 3x/week. Simple progression: 3x8-12, add weight when all reps completed.\n\n'

        'BLOCK PERIODIZATION (intermediate/advanced):\n'
        '  Accumulation: high volume, moderate intensity — 4x10-15, 60-75s rest\n'
        '  Intensification: moderate volume, high intensity — 4x5-8, 90-120s rest\n'
        '  Realization/Peak: low volume, max intensity — 3x3-5, 2-3 min rest\n'
        '  Deload (advanced, every 4th week): 50-60% volume reduction, same exercises\n\n'

        'UNDULATING (advanced or hybrid goals):\n'
        '  Vary rep ranges within same week: Mon power (3x5), Wed hypertrophy (4x10), Fri endurance (3x15)\n\n'

        'GOAL-SPECIFIC PROGRAMMING:\n\n'

        'MUSCLE GAIN:\n'
        '- Foundation: squat, deadlift, bench press, barbell row, overhead press\n'
        '- Rep range 6-12 for hypertrophy, 60-90s rest. Volume: 10-20 sets/muscle group/week\n'
        '- Progression within phase: add 1 rep/week until top of range, then add weight and reset\n'
        '- 8-week structure: Wk1-4 Accumulation (4x10-12) → Wk5-7 Intensification (4x5-8) → Wk8 Deload\n\n'

        'FAT LOSS:\n'
        '- Metabolic resistance training: compound movements, supersets, short rest (30-45s)\n'
        '- Rep range 12-20. Full-body circuits for max calorie burn\n'
        '- Include strength work to preserve muscle mass\n'
        '- Add 10-15 min metabolic finisher at end of each session\n'
        '- 8-week structure: Wk1-3 Circuits (15-20 reps, 30s rest) → Wk4-6 Superset strength (10-15) → Wk7-8 Strength maintenance\n\n'

        'HYBRID (e.g. muscle gain + fat loss, strength + running):\n'
        '- 70% training towards primary goal, 30% towards secondary\n'
        '- Muscle + fat loss: heavy strength days with 10-min metabolic finisher each session\n'
        '- Strength + running: lift Mon/Wed/Fri, conditioning Tue/Thu; never heavy lift and long cardio same day\n\n'

        'MAINTAIN:\n'
        '- Balanced upper/lower or full-body. Consistent 3x10-12, 60-90s rest\n'
        '- Rotate exercise variations every 4 weeks to prevent adaptation\n\n'

        'EXPERIENCE RULES:\n'
        '- BEGINNER: Full-body 3x/week, basic compounds only, linear progression, 3x8-12\n'
        '- INTERMEDIATE: Push/pull/legs or upper/lower, block periodization, more variety\n'
        '- ADVANCED: RPE-based loading, undulating periodization, mandatory deload every 4th week\n\n'

        'INJURY PROTOCOL (highest priority — overrides all other decisions):\n'
        '- Any stated injury or limitation is a HARD constraint — never programme the restricted movement\n'
        '- Knee pain/injury → replace squat/lunge with leg press, hamstring curl, step-ups\n'
        '- Lower back issue → replace conventional deadlift with Romanian deadlift or trap bar; no heavy spinal loading\n'
        '- Shoulder injury → replace overhead press with cable/machine alternatives; avoid internal rotation under load\n'
        '- Wrist/elbow → replace barbell with dumbbell or cable equivalents\n'
        '- Always note the substitution reason in the exercise notes field\n\n'

        'WARMUP & MOBILITY (mandatory — first entry in EVERY workout day):\n'
        '- Add "Warmup & Mobility" as the very first exercise on every non-rest day\n'
        '- Use sets: 1, reps: "5-10 min", rest_seconds: 0\n'
        '- In the notes field, list 3-5 specific activation/mobility drills for that day\'s muscle groups:\n'
        '  Push day: "Arm circles 20x, band pull-aparts 15x, shoulder CARs 5x each, cat-cow 10x"\n'
        '  Pull day: "Thoracic rotation 10x, scapular retractions 15x, doorframe chest stretch 30s"\n'
        '  Leg day: "Leg swings 10x each, hip circles 10x, bodyweight squats 10x, ankle rolls 30s"\n'
        '  Full body: "World\'s greatest stretch 5x each, hip flexor stretch 30s each, arm circles 20x"\n'
        '- If the user has injuries, ALSO include targeted mobility for the injured area every session:\n'
        '  Lower back: cat-cow 10x, child\'s pose 30s, hip flexor stretch 30s each\n'
        '  Knee: quad stretch 30s each, clamshells 15x, terminal knee extensions 15x\n'
        '  Shoulder: doorframe stretch 30s, wall slides 10x, thoracic rotation 10x each\n\n'

        'EXERCISE SELECTION (priority order):\n'
        '1. Primary compound: squat/hinge/horizontal push/vertical pull/loaded carry\n'
        '2. Secondary compound: lunge, dip, cable row, incline/decline variation\n'
        '3. Isolation accessory: curl, extension, lateral raise — supplementary only\n\n'

        'EXERCISE VARIATION ACROSS PHASES (critical — not just sets/reps):\n'
        'Exercises MUST change meaningfully between phases, not just intensity parameters:\n'
        '  Accumulation (W1-4): Higher-rep compound variants, more accessory volume\n'
        '    Push → Incline DB press, cable flyes, lateral raises, triceps pushdown\n'
        '    Pull → Lat pulldown, cable row, face pulls, DB curls\n'
        '    Legs → Leg press, leg curl, leg extension, Romanian deadlift\n'
        '  Intensification (W5-7): Heavier barbell compounds replace machines, drop accessories\n'
        '    Push → Barbell bench press, weighted dips, overhead press\n'
        '    Pull → Weighted pull-ups/chin-ups, barbell row, deadlift\n'
        '    Legs → Back squat, front squat, barbell RDL, Bulgarian split squat\n'
        '  Deload (W8): Light versions of the Intensification compounds — same movements, 50% volume\n'
        'This is NON-NEGOTIABLE. If weeks 1 and 5 have identical exercise lists, the plan is wrong.\n\n'

        'PAST EXERCISE CONTEXT:\n'
        'Client\'s previous program exercises are provided for reference. '
        'Repeat them if they are the optimal choice. Vary them if the phase calls for a different stimulus. '
        'Never avoid an exercise just because it appeared before — effectiveness over novelty.\n\n'

        'EXERCISE NAMES: Use exact common names found in standard gym facilities.\n'
        'Cable crossover valid variants: "High-to-Low Cable Crossover", "Low-to-High Cable Crossover", '
        '"Standing Cable Crossover". "Mid-to-High" does not exist — never use it.\n'
        'When uncertain, use the simplest, most recognisable name.\n\n'

        'MEASUREMENT TYPE (mandatory for every exercise):\n'
        'Each exercise must include a "measurement_type" field. Use this reference:\n'
        '  "weight_reps"     → barbell, dumbbell, cable, machine exercises (bench press, squat, row, curl, crossover)\n'
        '  "weight_duration" → loaded carries, pushes and holds (Farmer\'s Carry,\n'
        '                       Sled Push, Prowler Push, Yoke Walk, Farmer\'s Hold)\n'
        '  "duration"        → holds, cardio, treadmill, plank, wall sit, elliptical, bike, rowing machine, stair climber\n'
        '  "warmup"          → any Warmup & Mobility block\n'
        '  "bodyweight_reps" → push-ups, pull-ups, dips, chin-ups, inverted rows\n\n'

        'THE REPS FIELD MUST MATCH THE MEASUREMENT TYPE:\n'
        'The user logs each exercise using the fields its measurement_type\n'
        'allows, so a prescription in the wrong unit cannot be entered at all --\n'
        'they are left guessing which number goes in which box.\n'
        '  [weight_reps], [bodyweight_reps] -> reps.  "8-12", "15", "AMRAP"\n'
        '  [duration], [weight_duration]    -> time.  "40 sec", "5-10 min"\n'
        'Specifically:\n'
        '  - Never prescribe a loaded carry in yards, feet or metres. A carry is\n'
        '    logged as WEIGHT + SECONDS, so "40 yards" is unenterable.\n'
        '  - Never prescribe a rep-based exercise as a duration. "Burpees: 10 min"\n'
        '    is wrong — the user logs burpees as REPS. Write "10-15" instead.\n'
        '  - Never prescribe a hold or cardio block as a rep count.\n\n'

        'REPS FIELD — USE CONVENTIONAL SCHEMES ONLY:\n'
        'Real coaches prescribe reps in standard brackets, never arbitrary\n'
        'numbers. "11", "13", "14", "17" are WRONG — they read as machine output.\n'
        'Use ONLY these:\n'
        '  Strength:    "3", "5", "6", "3-5", "5-8", "6-8"\n'
        '  Hypertrophy: "8", "10", "12", "8-10", "8-12", "10-12", "12-15"\n'
        '  Endurance:   "15", "20", "15-20", "20-25"\n'
        '  Other:       "AMRAP", "to failure", "30 sec", "5-10 min"\n'
        'Add a qualifier when the movement is unilateral: "10-12 per leg".\n'
        'If your intended number is not in that list, widen it to the bracket\n'
        'that contains it — 13 becomes "12-15", not "13".\n\n'

        'NOTES FIELD (mandatory for every exercise) — COACHING ONLY, NO WEIGHTS:\n'
        'The notes field is for things that do not change week to week:\n'
        '  - form cues and common mistakes ("Pause at the chest", "Keep the\n'
        '    elbows tucked", "Drive through the heel")\n'
        '  - why an exercise was substituted for an injury\n'
        '  - for Warmup & Mobility: the specific stretches/drills for that day\n\n'
        'Do NOT put a starting weight or a progression rule in notes. Weight\n'
        'recommendations are generated separately each week from the user\'s own\n'
        'logged history, which is far more accurate than anything you can guess\n'
        'at plan-creation time. A static "Start 95lb" in notes competes with that\n'
        'and goes stale immediately.\n'
        'So: no "Start ...", no "Add ... when all sets clean", no placeholder\n'
        'weights of any kind.\n'
        f'Good:  "Pause 1s at the chest. Keep the elbows at 45 degrees."\n'
        f'Good:  "Swapped from barbell squat — knee pain. Keep depth pain-free."\n'
        f'BAD:   "Start 95{preferred_unit}. Add 5{preferred_unit} when all sets clean."'
    )

    # Build the structured user prompt
    age = getattr(profile, 'age', None) if profile else None
    gender = getattr(profile, 'gender', None) if profile else None
    weight_kg = getattr(profile, 'weight_kg', None) if profile else None
    height = getattr(profile, 'height_cm', None) if profile else None
    personal_notes = getattr(profile, 'personal_notes', '') if profile else ''

    if weight_kg is not None:
        weight_display = f'{round(weight_kg * 2.20462, 1)}lb' if preferred_unit == 'lb' else f'{weight_kg}kg'
    else:
        weight_display = None

    profile_line = f'Client: {age}yo {gender}, {weight_display}, {height}cm' if all([age, gender, weight_display, height]) else ''

    specific_goals_line = ''
    if specific_goals:
        goal_labels = {
            'lose_weight': 'lose weight', 'reduce_belly_fat': 'reduce belly fat',
            'build_muscle': 'build muscle', 'run_distance': 'run a distance goal',
            'get_stronger': 'get stronger', 'improve_stamina': 'improve stamina/energy',
            'better_posture': 'improve posture/mobility',
        }
        goal_strs = [goal_labels.get(g, g) for g in specific_goals]
        if goal_params:
            if 'lose_weight' in goal_params:
                p = goal_params['lose_weight']
                goal_strs = [f"lose {p.get('amount', '')} {p.get('unit', preferred_unit)}" if g == 'lose weight' else g for g in goal_strs]
            if 'run_distance' in goal_params:
                p = goal_params['run_distance']
                goal_strs = [f"run {p.get('distance', '5K')}" if g == 'run a distance goal' else g for g in goal_strs]
        specific_goals_line = f'Specific goals: {", ".join(goal_strs)}'

    target_line = ''
    if confirmed_target:
        target_line = (f'Confirmed program target: {confirmed_target.get("label", "")} = '
                       f'{confirmed_target.get("recommended_value", "")} '
                       f'(current: {confirmed_target.get("current_value", "unknown")})')

    past_line = f'Previous program exercises (for reference): {", ".join(past_exercises[:30])}' if past_exercises else 'Previous exercises: none (first program)'

    phases = '1 phase, linear progression' if duration_weeks <= 4 else ('2-3 phases' if duration_weeks <= 8 else '3-4 phases, deload every 4th week')

    injuries_line = f'INJURIES & LIMITATIONS (HARD CONSTRAINTS — apply every session, no exceptions):\n{notes}' if notes else 'INJURIES & LIMITATIONS: None stated'

    user = '\n'.join(filter(None, [
        profile_line,
        f'Personal notes: {personal_notes}' if personal_notes else '',
        f'Primary goal: {goal} | Experience: {level} | Equipment: {equip}',
        specific_goals_line,
        target_line,
        f'Days/week: {days_per_week} | Duration: {duration_weeks} weeks',
        injuries_line,
        f'Body context: {body_context}' if body_context else '',
        past_line,
        '',
        f'Design the optimal {duration_weeks}-week plan for this specific person using your full trainer expertise.',
        f'Structure into {phases}. Consider ALL stated goals — blend programming for hybrid goals.',
        'Within each phase increase overload week-to-week (sets or reduced rest before adding load).',
        f'CRITICAL: the weeks array MUST contain exactly {duration_weeks} sub-arrays. Fewer weeks = incomplete response.',
        'Schema (show all weeks — example has 2, you must return all {duration_weeks}):'.replace('{duration_weeks}', str(duration_weeks)),
        '{',
        '  "title": "string",',
        '  "description": "string (mention the periodization approach)",',
        f'  "duration_weeks": {duration_weeks},',
        '  "weeks": [',
        '    [  // Week 1: first phase (e.g. Accumulation — 4x10-12, 60s rest)',
        '      {',
        '        "day_number": 1,',
        '        "name": "string",',
        '        "focus": "string",',
        '        "is_rest_day": false,',
        '        "exercises": [',
        '          {"name": "Warmup & Mobility", "sets": 1, "reps": "5-10 min", "rest_seconds": 0, "measurement_type": "warmup", "notes": "specific drills for today"},',
        # The notes example must be a FORM CUE, never a weight. It previously
        # showed a starting-weight sentence, which the model dutifully copied --
        # first as a literal placeholder ("Start Xkg"), then as a static guess
        # that competed with the history-based weekly recommendation.
        '          {"name": "string", "sets": number, "reps": "string", "rest_seconds": number, "measurement_type": "weight_reps", "notes": "form cue for this exercise"}',
        '        ]',
        '      }',
        '    ],',
        '    [  // Week 2: same structure — same exercises, notes show progressive overload (e.g. +1 rep or +weight)',
        '      { "day_number": 1, "name": "string", "focus": "string", "is_rest_day": false, "exercises": [ ... ] }',
        '    ]',
        f'    // ... weeks 3 through {duration_weeks} follow the same pattern, evolving with each phase',
        '  ]',
        '}',
    ]))
    return system, user


def _validate_plan_json(data: dict) -> None:
    if 'title' not in data:
        raise ValueError('Missing required field: title')
    days_list = []
    if 'weeks' in data:
        if not isinstance(data['weeks'], list) or len(data['weeks']) == 0:
            raise ValueError('weeks must be a non-empty list')
        for week in data['weeks']:
            if not isinstance(week, list):
                raise ValueError('each week must be a list of days')
            days_list.extend(week)
    elif 'days' in data:
        if not isinstance(data['days'], list) or len(data['days']) == 0:
            raise ValueError('days must be a non-empty list')
        days_list = data['days']
    else:
        raise ValueError('Missing required field: days or weeks')
    for day in days_list:
        if not isinstance(day, dict):
            raise ValueError('each day must be an object')
        for field in ['day_number', 'name', 'is_rest_day']:
            if field not in day:
                raise ValueError(f'Day missing field: {field}')
        if not day.get('is_rest_day', False):
            exercises = day.get('exercises', [])
            if not isinstance(exercises, list):
                raise ValueError('exercises must be a list')
            # Exercise-level validation. This was previously absent entirely,
            # which made a missing 'name' a KeyError -> 500 during plan creation.
            for idx, ex_data in enumerate(exercises):
                label = f"day '{day.get('name')}' exercise #{idx + 1}"
                if not isinstance(ex_data, dict):
                    raise ValueError(f'{label} must be an object')
                name = ex_data.get('name')
                if not name or not str(name).strip():
                    raise ValueError(f'{label} is missing a name')
                sets = ex_data.get('sets', 3)
                try:
                    if int(sets) < 1:
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError(f'{label} has invalid sets: {sets!r}')
                reps = ex_data.get('reps', '10')
                if reps is None or not str(reps).strip():
                    raise ValueError(f'{label} is missing reps')
                # A prescription in the wrong unit for the exercise's
                # measurement type is unenterable by the user. Log it rather
                # than reject the whole plan -- and never rewrite it, since reps
                # cannot be inferred from "10 min" without inventing
                # programming. The UI surfaces the mismatch instead.
                mtype = exercise_types.normalize(
                    ex_data.get('measurement_type'), name,
                    day_name=day.get('name'), reps=reps)
                ok, expected = exercise_types.prescription_matches_type(mtype, reps)
                if not ok:
                    logger.warning(
                        'plan prescription mismatch: %s [%s] reps=%r (expected %s)',
                        name, mtype, str(reps), expected)


# A placeholder weight the model emitted instead of choosing a number:
# "Xlb", "Ykg", "[weight]", "[increment]".
#
# The letter is matched CASE-SENSITIVELY and only when glued to a unit. A
# case-insensitive bare letter would match the "x" in "3 x 12" and silently
# delete legitimate set notation.
_PLACEHOLDER_TOKEN_RE = re.compile(
    r'\b[XYZ](?i:kg|lbs?|kilos?|pounds?)\b'   # Xlb, Ykg, Zlbs
    r'|\[[^\]]{1,24}\]'                        # [weight], [increment]
)


def _strip_placeholder_weights(text):
    """
    Drop whole sentences that contain a placeholder weight, keep the rest.

    "Focus on form. Start Xlb. Add 5lb when clean." keeps the form cue and the
    real increment, and loses only the useless placeholder sentence. Operating
    per sentence rather than with one big pattern makes the behaviour
    predictable and keeps genuine numbers intact.
    """
    if not text:
        return text or ''
    sentences = re.split(r'(?<=[.!?])\s+', str(text).strip())
    kept = [s for s in sentences if not _PLACEHOLDER_TOKEN_RE.search(s)]
    return re.sub(r'\s{2,}', ' ', ' '.join(kept)).strip()


def _create_exercise(day, ex_data: dict, order: int):
    """
    Single creation path for Exercise rows.

    Every caller must route through here so `measurement_type` is always decided
    by the server. The AI only gets a vote -- exercise_types.normalize() overrides
    it when the name is recognisable and forces 'warmup' on warmup days. This
    replaced four duplicated `.get('measurement_type', 'weight_reps')` call sites
    that silently stamped the worst-case default on anything the AI omitted.
    """
    name = str(ex_data['name']).strip()
    # Snap arbitrary rep counts onto conventional schemes (13 -> "12-15"). The
    # model was emitting numbers like 11/13/17, which read as machine output
    # rather than programming.
    reps = exercise_types.normalize_reps(str(ex_data.get('reps', '10')))
    return Exercise.objects.create(
        day=day,
        name=name,
        sets=ex_data.get('sets', 3),
        reps=reps,
        rest_seconds=ex_data.get('rest_seconds'),
        notes=_strip_placeholder_weights(ex_data.get('notes', '')),
        order=order,
        measurement_type=exercise_types.normalize(
            ex_data.get('measurement_type'),
            name,
            day_name=getattr(day, 'name', None),
            reps=reps,
        ),
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_plan_suggest_target(request):
    """Light Gemini call: given user profile + goals, return a recommended measurable target."""
    from .gemini import call_gemini_json, GeminiError as _GeminiError

    try:
        profile = request.user.profile
    except Exception:
        profile = None

    preferred_unit = getattr(profile, 'preferred_unit', 'lb') or 'lb'
    age = getattr(profile, 'age', None) if profile else None
    gender = getattr(profile, 'gender', None) if profile else None
    weight_kg = getattr(profile, 'weight_kg', None) if profile else None
    height = getattr(profile, 'height_cm', None) if profile else None

    if weight_kg is not None:
        weight_display = f'{round(weight_kg * 2.20462, 1)}lb' if preferred_unit == 'lb' else f'{weight_kg}kg'
    else:
        weight_display = None

    fitness_goal = request.data.get('fitness_goal', '')
    experience_level = request.data.get('experience_level', '')
    days_per_week = request.data.get('days_per_week', 4)
    duration_weeks = request.data.get('duration_weeks', 8)
    specific_goals = request.data.get('specific_goals', [])
    goal_params = request.data.get('goal_params', {})
    notes = request.data.get('notes', '')
    body_context = request.data.get('body_context', '')

    goal_label_map = {
        'lose_weight': 'lose weight', 'reduce_belly_fat': 'reduce belly fat',
        'build_muscle': 'build muscle', 'run_distance': 'run a distance goal',
        'get_stronger': 'get stronger', 'improve_stamina': 'improve stamina/energy',
        'better_posture': 'improve posture/mobility',
    }
    goal_strs = [goal_label_map.get(g, g) for g in specific_goals]
    if goal_params.get('lose_weight'):
        p = goal_params['lose_weight']
        goal_strs = [f"lose {p.get('amount', '')} {p.get('unit', preferred_unit)}" if 'lose weight' in g else g for g in goal_strs]
    if goal_params.get('run_distance'):
        p = goal_params['run_distance']
        goal_strs = [f"run {p.get('distance', '5K')}" if 'distance' in g else g for g in goal_strs]

    system = (
        'You are a certified personal trainer and sports scientist with 15 years of experience. '
        'Analyze the client profile and goals and return a realistic, motivating, personalized '
        'program target recommendation as JSON only. No markdown, no explanation.\n\n'
        'REALISTIC TARGET RATES:\n'
        '- Weight loss: 0.5-1 kg/week (1-2 lb/week) is healthy. Never recommend exceeding this.\n'
        '- Muscle gain: 0.5-1 kg/month for intermediate, 0.25-0.5 kg/month for advanced.\n'
        '- Running: beginner 0→5K in 6-8 wks; 5K→10K in 6-8 wks; 10K→half marathon in 10-12 wks.\n'
        '- Strength: beginner adds 5 lb/week on main lifts; intermediate 5 lb/month.\n'
        '- Body fat reduction: 0.5-1% per month realistic with resistance training.\n\n'
        'If user has multiple specific goals, pick the single most important measurable target. '
        'Mention secondary goals in the message.\n\n'
        'Return exactly:\n'
        '{\n'
        '  "message": "2-3 sentences, warm and direct. Start with your assessment. State the recommendation with reasoning. End with motivation.",\n'
        '  "program_target": {\n'
        '    "metric": "weight_kg|weight_lb|running_km|running_miles|lift_kg|lift_lb|body_fat_pct",\n'
        '    "label": "Human-readable label e.g. Target weight",\n'
        '    "recommended_value": <number>,\n'
        '    "current_value": <number or null>\n'
        '  }\n'
        '}'
    )

    goal_map = {'lose_fat': 'fat loss', 'build_muscle': 'muscle gain', 'maintain': 'maintenance'}
    primary = goal_map.get(fitness_goal, fitness_goal or 'general fitness')

    user_question = request.data.get('user_question', '')
    previous_recommendation = request.data.get('previous_recommendation', '')

    user_parts = [
        f'Client profile: {age}yo {gender}, current weight {weight_display}, height {height}cm' if all([age, gender, weight_display, height]) else 'Client profile: not provided',
        f'Primary goal: {primary} | Experience: {experience_level or "unknown"}',
        f'Duration: {duration_weeks} weeks, {days_per_week} days/week',
        f'Specific goals: {", ".join(goal_strs)}' if goal_strs else 'No specific secondary goals',
        f'Notes / limitations: {notes}' if notes else '',
        f'Body context: {body_context}' if body_context else '',
        f'Previous recommendation: {previous_recommendation}' if previous_recommendation else '',
        f'User follow-up: {user_question}' if user_question else '',
    ]
    user_prompt = '\n'.join(p for p in user_parts if p)

    try:
        result = call_gemini_json(system_prompt=system, user_prompt=user_prompt)
    except _GeminiError as e:
        return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response(result)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_plan_generate(request):
    from .gemini import call_gemini_json, call_gemini_vision_text, GeminiError as _GeminiError
    days_per_week = int(request.data.get('days_per_week', 4))
    duration_weeks = int(request.data.get('duration_weeks', 8))
    days_per_week = max(1, min(days_per_week, 7))
    duration_weeks = max(1, min(duration_weeks, 16))
    fitness_goal = request.data.get('fitness_goal', '')
    experience_level = request.data.get('experience_level', '')
    equipment = request.data.get('equipment', '')
    notes = request.data.get('notes', '')
    body_context = request.data.get('body_context', '')  # pre-built text from latest scan

    # If a new photo is uploaded, run a quick body analysis and append it to notes
    photo_file = request.FILES.get('body_photo')
    if photo_file and not body_context:
        try:
            photo_bytes = photo_file.read()
            mime = photo_file.content_type or 'image/jpeg'
            scan_system = 'You are a fitness assessment AI. Briefly describe the person\'s physique, estimated body fat %, muscle development, and any notes relevant for building a workout plan. Be concise — 3-5 sentences.'
            scan_user = 'Analyze this photo and provide a brief physique assessment for workout planning purposes.'
            analysis = call_gemini_vision_text(
                system_prompt=scan_system,
                user_prompt=scan_user,
                image_bytes=photo_bytes,
                mime_type=mime,
            )
            body_context = f'Body photo analysis: {analysis}'
        except Exception:
            pass

    specific_goals = request.data.get('specific_goals', [])
    goal_params = request.data.get('goal_params', {})
    confirmed_target = request.data.get('confirmed_target', None)

    try:
        profile = request.user.profile
    except Exception:
        profile = None

    preferred_unit = 'lb'
    try:
        preferred_unit = getattr(profile, 'preferred_unit', 'lb') or 'lb'
    except Exception:
        pass

    # Gather past exercises as context for the AI (not a blocklist — AI decides whether to repeat)
    past_exercises = []
    try:
        for pp in WorkoutPlan.objects.filter(user=request.user).prefetch_related(
                'days__exercises').order_by('-created_at')[:2]:
            for day in pp.days.all():
                for ex in day.exercises.all():
                    if ex.name not in past_exercises:
                        past_exercises.append(ex.name)
    except Exception:
        pass

    try:
        system, user_prompt = _workout_generate_prompt(
            profile, days_per_week, duration_weeks,
            fitness_goal=fitness_goal, experience_level=experience_level,
            equipment=equipment, notes=notes, body_context=body_context,
            preferred_unit=preferred_unit,
            specific_goals=specific_goals, goal_params=goal_params,
            confirmed_target=confirmed_target, past_exercises=past_exercises,
        )
    except Exception as e:
        logger.exception('workout_plan_generate: prompt build failed')
        return Response({'detail': f'Failed to build prompt: {e}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    try:
        plan_data = call_gemini_json(system_prompt=system, user_prompt=user_prompt)
    except _GeminiError as e:
        return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    try:
        _validate_plan_json(plan_data)
    except ValueError as e:
        return Response({'detail': f'AI returned invalid plan: {e}'}, status=status.HTTP_502_BAD_GATEWAY)

    # If Gemini returned fewer weeks than requested, retry once with a stronger prompt
    returned_weeks = len(plan_data.get('weeks', []))
    if returned_weeks < duration_weeks:
        retry_user = (
            f'The plan you just returned only has {returned_weeks} week(s) but must have exactly {duration_weeks}. '
            f'Expand it now. Repeat the same exercises across weeks but change sets/reps/rest to show progression '
            f'(e.g. add 1 rep per week, reduce rest, or increase sets). '
            f'Return the COMPLETE plan with ALL {duration_weeks} weeks in the weeks array. '
            f'Do not omit any weeks.\n\n' + user_prompt
        )
        try:
            plan_data2 = call_gemini_json(system_prompt=system, user_prompt=retry_user)
            _validate_plan_json(plan_data2)
            if len(plan_data2.get('weeks', [])) >= returned_weeks:
                plan_data = plan_data2
        except Exception:
            pass  # keep original if retry fails

    # Always use user's requested duration — override whatever the AI returned
    plan_data['duration_weeks'] = duration_weeks
    if 'weeks' in plan_data and isinstance(plan_data['weeks'], list):
        plan_data['weeks'] = plan_data['weeks'][:duration_weeks]

    # Attach the confirmed target so the frontend can save it with the plan
    if confirmed_target:
        plan_data['program_target'] = confirmed_target

    return Response(plan_data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def workout_plans(request):
    if request.method == 'GET':
        plans = WorkoutPlan.objects.filter(user=request.user)
        return Response(WorkoutPlanSerializer(plans, many=True).data)

    # POST — save a plan (from AI preview or manual)
    data = request.data
    try:
        _validate_plan_json(data)
    except ValueError as e:
        return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    plan = WorkoutPlan.objects.create(
        user=request.user,
        title=data['title'],
        description=data.get('description', ''),
        duration_weeks=data.get('duration_weeks'),
        generated_by_ai=data.get('generated_by_ai', True),
        specific_goal=data.get('specific_goal', ''),
        program_target=data.get('program_target'),
    )

    # Support both single-week ('days') and multi-week ('weeks') plan formats
    order_counter = 0
    if 'weeks' in data:
        weeks = data['weeks']
        for week_idx, week_days in enumerate(weeks, start=1):
            for day_data in week_days:
                day = WorkoutDay.objects.create(
                    plan=plan,
                    day_number=day_data['day_number'],
                    week_number=week_idx,
                    name=day_data['name'],
                    focus=day_data.get('focus', ''),
                    is_rest_day=day_data.get('is_rest_day', False),
                    order=order_counter,
                )
                order_counter += 1
                for j, ex_data in enumerate(day_data.get('exercises', [])):
                    _create_exercise(day, ex_data, j)
    else:
        for day_data in data['days']:
            day = WorkoutDay.objects.create(
                plan=plan,
                day_number=day_data['day_number'],
                week_number=day_data.get('week_number', 1),
                name=day_data['name'],
                focus=day_data.get('focus', ''),
                is_rest_day=day_data.get('is_rest_day', False),
                order=order_counter,
            )
            order_counter += 1
            for j, ex_data in enumerate(day_data.get('exercises', [])):
                _create_exercise(day, ex_data, j)

    import threading
    threading.Thread(target=_bg_cache_plan_guides, args=(plan.id,), daemon=True).start()

    return Response(WorkoutPlanDetailSerializer(plan).data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def workout_plan_detail(request, plan_id):
    plan = get_object_or_404(WorkoutPlan, pk=plan_id, user=request.user)

    if request.method == 'GET':
        return Response(WorkoutPlanDetailSerializer(plan).data)

    if request.method == 'PATCH':
        if 'title' in request.data:
            plan.title = request.data['title']
        if 'description' in request.data:
            plan.description = request.data['description']
        plan.save()
        return Response(WorkoutPlanSerializer(plan).data)

    if request.method == 'PUT':
        data = request.data
        try:
            _validate_plan_json(data)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            plan.title = data['title']
            plan.description = data.get('description', '')
            plan.duration_weeks = data.get('duration_weeks', plan.duration_weeks)
            plan.save()

            # Replace all days and exercises — support both weeks[][] and days[] formats
            plan.days.all().delete()
            order_counter = 0
            if 'weeks' in data:
                for week_idx, week_days in enumerate(data['weeks'], start=1):
                    for day_data in week_days:
                        day = WorkoutDay.objects.create(
                            plan=plan,
                            day_number=day_data['day_number'],
                            week_number=week_idx,
                            name=day_data['name'],
                            focus=day_data.get('focus', ''),
                            is_rest_day=day_data.get('is_rest_day', False),
                            order=order_counter,
                        )
                        order_counter += 1
                        for j, ex_data in enumerate(day_data.get('exercises', [])):
                            _create_exercise(day, ex_data, j)
            else:
                for i, day_data in enumerate(data['days']):
                    day = WorkoutDay.objects.create(
                        plan=plan,
                        day_number=day_data['day_number'],
                        week_number=day_data.get('week_number', 1),
                        name=day_data['name'],
                        focus=day_data.get('focus', ''),
                        is_rest_day=day_data.get('is_rest_day', False),
                        order=i,
                    )
                    for j, ex_data in enumerate(day_data.get('exercises', [])):
                        _create_exercise(day, ex_data, j)
        return Response(WorkoutPlanDetailSerializer(plan).data)

    plan.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_plan_activate(request, plan_id):
    plan = get_object_or_404(WorkoutPlan, pk=plan_id, user=request.user)
    from django.utils import timezone as _tz
    WorkoutPlan.objects.filter(user=request.user, is_active=True).update(is_active=False)
    plan.is_active = True
    plan.activated_at = _tz.now()
    plan.save(update_fields=['is_active', 'activated_at'])
    return Response(WorkoutPlanSerializer(plan).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_plan_advance_week(request, plan_id):
    plan = get_object_or_404(WorkoutPlan, pk=plan_id, user=request.user)
    from django.db.models import Max as _Max
    day_max = plan.days.aggregate(m=_Max('week_number'))['m'] or 1
    max_week = max(day_max, plan.duration_weeks or 1)
    if plan.current_week >= max_week:
        # Last week done — flag check-in if not shown
        if not plan.goal_check_in_shown:
            plan.goal_check_in_shown = True
            plan.save(update_fields=['goal_check_in_shown'])
        return Response({'detail': 'Program complete', 'current_week': plan.current_week, 'program_complete': True})
    plan.current_week += 1
    plan.current_day_order = 0
    plan.save(update_fields=['current_week', 'current_day_order'])
    return Response(WorkoutPlanSerializer(plan).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_plan_mark_rest_day(request, plan_id):
    from django.utils import timezone as tz
    plan = get_object_or_404(WorkoutPlan, pk=plan_id, user=request.user)
    day_id = request.data.get('day_id')
    if not day_id:
        return Response({'detail': 'day_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
    day = get_object_or_404(WorkoutDay, pk=day_id, plan=plan)
    day.last_completed_at = tz.now()
    day.save(update_fields=['last_completed_at'])

    week_days = list(WorkoutDay.objects.filter(plan=plan, week_number=plan.current_week).order_by('day_number'))
    current_idx = next((i for i, d in enumerate(week_days) if d.id == day.id), plan.current_day_order)
    next_idx = min(current_idx + 1, len(week_days) - 1)
    plan.current_day_order = next_idx
    plan.save(update_fields=['current_day_order'])
    return Response(WorkoutPlanSerializer(plan).data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def workout_sessions(request):
    if request.method == 'GET':
        sessions = WorkoutSession.objects.filter(user=request.user)[:50]
        return Response(WorkoutSessionListSerializer(sessions, many=True).data)

    day_id = request.data.get('exercise_day_id')
    if not day_id:
        return Response({'detail': 'exercise_day_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

    day = get_object_or_404(WorkoutDay, pk=day_id, plan__user=request.user)
    force = request.data.get('force', False)

    # Resume if there's an incomplete session for this exact day
    same_day = WorkoutSession.objects.filter(
        user=request.user, exercise_day=day, is_completed=False
    ).first()
    if same_day:
        return Response(WorkoutSessionSerializer(same_day).data)

    # Warn if there's an incomplete session for a different day (unless force=true)
    if not force:
        other = WorkoutSession.objects.filter(
            user=request.user, is_completed=False
        ).exclude(exercise_day=day).select_related('exercise_day__plan').first()
        if other:
            # Auto-clean orphaned sessions (day deleted or plan no longer active)
            day_belongs_to_active_plan = (
                other.exercise_day is not None and
                other.exercise_day.plan.user == request.user
            )
            if not day_belongs_to_active_plan:
                other.is_completed = True
                other.save(update_fields=['is_completed'])
            else:
                conflict_name = other.day_name or (
                    other.exercise_day.name if other.exercise_day else 'a previous session'
                )
                data = WorkoutSessionSerializer(other).data
                data['already_active'] = True
                data['conflict_day_name'] = conflict_name
                return Response(data, status=status.HTTP_200_OK)

    session = WorkoutSession.objects.create(user=request.user, exercise_day=day, day_name=day.name)

    # Track current day in plan
    plan = day.plan
    week_days = list(WorkoutDay.objects.filter(plan=plan, week_number=plan.current_week).order_by('day_number'))
    idx = next((i for i, d in enumerate(week_days) if d.id == day.id), 0)
    plan.current_day_order = idx
    plan.save(update_fields=['current_day_order'])

    # Pre-populate set logs from the plan
    for exercise in day.exercises.all():
        for set_num in range(1, exercise.sets + 1):
            SetLog.objects.create(
                workout_session=session,
                exercise=exercise,
                exercise_name=exercise.name,
                set_number=set_num,
            )

    return Response(WorkoutSessionSerializer(session).data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def workout_session_detail(request, session_id):
    session = get_object_or_404(WorkoutSession, pk=session_id, user=request.user)

    if request.method == 'GET':
        return Response(WorkoutSessionSerializer(session).data)

    if 'is_completed' in request.data and request.data['is_completed']:
        from django.utils import timezone as tz
        session.is_completed = True
        session.completed_at = tz.now()
        if session.exercise_day_id:
            session.exercise_day.last_completed_at = tz.now()
            session.exercise_day.save(update_fields=['last_completed_at'])
    if 'notes' in request.data:
        session.notes = request.data['notes']
    session.save()
    return Response(WorkoutSessionSerializer(session).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_session_log_set(request, session_id):
    session = get_object_or_404(WorkoutSession, pk=session_id, user=request.user)
    if session.is_completed:
        return Response({'detail': 'Session already completed.'}, status=status.HTTP_400_BAD_REQUEST)

    exercise_id = request.data.get('exercise_id')
    set_number = request.data.get('set_number')
    if not exercise_id or set_number is None:
        return Response({'detail': 'exercise_id and set_number are required.'}, status=status.HTTP_400_BAD_REQUEST)

    exercise = get_object_or_404(Exercise, pk=exercise_id, day__plan__user=request.user)

    log, _ = SetLog.objects.get_or_create(
        workout_session=session,
        exercise=exercise,
        set_number=set_number,
        defaults={'exercise_name': exercise.name},
    )
    log.exercise_name = exercise.name

    # Enforce the measurement-type field contract on write. This endpoint
    # previously accepted whatever the client sent, which is how seconds ended up
    # in `weight_kg` for duration exercises. The Exercise row is already loaded
    # above, so there is no excuse not to use it.
    #
    # Start from what is already stored so a partial update stays partial, then
    # overlay only the keys the client actually sent, then coerce. Using a
    # sentinel (rather than `is not None`) means a client CAN clear a field by
    # sending an explicit null.
    incoming = {
        'weight_kg': log.weight_kg,
        'reps_completed': log.reps_completed,
        'duration_seconds': log.duration_seconds,
    }
    for field in exercise_types.ALL_SET_FIELDS:
        if field in request.data:
            incoming[field] = request.data[field]

    coerced = exercise_types.coerce_set_fields(exercise.measurement_type, incoming)
    log.weight_kg = coerced['weight_kg']
    log.reps_completed = coerced['reps_completed']
    log.duration_seconds = coerced['duration_seconds']

    if 'is_completed' in request.data:
        log.is_completed = bool(request.data['is_completed'])
    if 'notes' in request.data:
        log.notes = request.data['notes'] or ''
    log.save()
    return Response(SetLogSerializer(log).data)


def _bg_cache_plan_guides(plan_id):
    """Background thread: pre-cache ExerciseGuide for all unique exercises in a plan."""
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        from .gemini import call_gemini_json, GeminiError as _GeminiError
        plan = WorkoutPlan.objects.get(pk=plan_id)
        names = list(
            Exercise.objects.filter(day__plan=plan)
            .values_list('name', flat=True).distinct()
        )
        # Skip exercises that already have a generic (user=None) guide cached.
        names = [
            n for n in names
            if not ExerciseGuide.objects.filter(user=None, name__iexact=n).exists()
        ]
        if not names:
            return
        kb = _load_exercise_kb()
        prompt_lines = [
            'Return a JSON array. Each element corresponds to one exercise.',
            'For each exercise return: steps (4-6 strings), muscles (2-4 strings), '
            'tips (2-3 strings), category (squat/push/pull/hinge/core/cardio/other), '
            'kb_key (best matching image library key or null).',
            'Prefer the most specific kb_key that contains all words from the exercise name.',
            '', 'Exercises:',
        ]
        for i, name in enumerate(names, 1):
            candidates = _get_kb_candidates(name, top_n=8)
            cands_str = ', '.join(candidates[:6]) if candidates else 'none'
            prompt_lines.append(f'{i}. {name} | Image library candidates: {cands_str}')
        guides_list = call_gemini_json(
            system_prompt='You are a certified personal trainer. Provide exercise instructions.',
            user_prompt='\n'.join(prompt_lines),
        )
        if not isinstance(guides_list, list):
            return
        for name, guide in zip(names, guides_list):
            if not isinstance(guide, dict):
                continue
            kb_key = guide.pop('kb_key', None)
            guide['images'] = _images_for_exercise(name, kb_key)
            # These rows are GENERIC (user=None) and shared by every user, so they
            # must never carry personalised advice. Per-user weight/coaching
            # recommendations are written only by workout_plan_prepare_week
            # against the requesting user's own row.
            guide.pop('recommended_weight', None)
            guide.pop('recommended_target', None)
            guide.pop('coaching_note', None)
            try:
                ExerciseGuide.objects.get_or_create(
                    user=None, name=name, defaults={'data': guide})
            except Exception:
                pass
    except Exception as exc:
        _log.warning('_bg_cache_plan_guides failed for plan %s: %s', plan_id, exc)


KG_PER_LB = 0.453592


def _user_unit(user):
    """The user's preferred weight unit, defaulting to 'lb' (the model default)."""
    profile = getattr(user, 'profile', None)
    return getattr(profile, 'preferred_unit', 'lb') or 'lb'


def _to_display_weight(kg, unit):
    """kg (storage) -> the user's preferred display unit."""
    if kg is None:
        return None
    if unit == 'lb':
        return round(kg / KG_PER_LB, 1)
    return round(kg, 1)


def _increment_for(unit, body_part):
    """Sensible progression increment, expressed in the user's unit."""
    if unit == 'lb':
        return 5 if body_part == 'upper' else 10
    return 2.5 if body_part == 'upper' else 5


def _duration_increment(seconds):
    """
    How much time to add to a hold that is ready to progress.

    Proportional, not flat. A flat +15 sec is a 29% jump on a 52-second plank --
    which is what the advice used to recommend, and is far more than anyone adds
    between sessions. 10% rounded to the nearest 5 seconds keeps the jump
    proportionate, with a 5-second floor so short holds still move and a
    15-second ceiling so a 3-minute hold does not get a 20-second demand.
    """
    step = round(seconds * 0.10 / 5) * 5
    return max(5, min(15, int(step)))


def _target_reps_from(reps):
    """
    Pull a numeric rep target out of the free-text `reps` field ("8-12" -> 12).

    Returns None when the field is a time prescription or otherwise unparseable,
    which tells _analyze_progression to use its default.

    Kept as a thin wrapper over _target_rep_range for readability; callers that
    need to reason about hitting the range should use the range directly.
    """
    low, high = _target_rep_range(reps)
    return high


def _target_rep_range(reps):
    """
    Pull the (low, high) rep target out of the free-text `reps` field.

        "10-12"  -> (10, 12)
        "15"     -> (15, 15)
        "30 sec" -> (None, None)

    A range has TWO thresholds and the old code collapsed it to max(): hitting
    10 reps on a prescribed "10-12" was scored as a failure even though 10 is
    inside the range. That is why the advice could not tell "needs to back off"
    from "working within the range":

        >= high  -> topped the range, add weight
        >= low   -> in the range, add reps
        <  low   -> below the range, reduce weight
    """
    if not reps:
        return None, None
    text = str(reps)
    if re.search(r'\b(sec|secs|second|seconds|min|mins|minute|minutes|'
                 r'hour|hours|yard|yards|meter|meters|mile|miles|km)\b',
                 text, re.I):
        return None, None
    nums = [int(n) for n in re.findall(r'\d+', text)]
    if not nums:
        return None, None
    return min(nums), max(nums)


def _format_set_for_prompt(log, measurement_type, unit):
    """
    Render one SetLog for an AI prompt using the right field and unit.

    The old version hardcoded f"{weight_kg}kg x {reps}" for every exercise, so a
    plank was described to the model as "180kg x ?" while the user's profile said
    lb -- the direct cause of nonsensical "start Xlb" advice.
    """
    if measurement_type == exercise_types.DURATION:
        return '%ss' % (log.get('duration_seconds') or '?')
    if measurement_type == exercise_types.WEIGHT_DURATION:
        return '%s%s for %ss' % (
            _to_display_weight(log.get('weight_kg'), unit) or '?',
            unit,
            log.get('duration_seconds') or '?',
        )
    if measurement_type == exercise_types.BODYWEIGHT_REPS:
        return '%s reps (bodyweight)' % (log.get('reps_completed') or '?')
    return '%s%s x %s' % (
        _to_display_weight(log.get('weight_kg'), unit) or '?',
        unit,
        log.get('reps_completed') or '?',
    )


def _session_date(value):
    """
    Date key for a SetLog's session timestamp.

    `.values()` hands back a real datetime, so the previous `value[:10]` raised
    TypeError for every exercise that actually had history -- one of the two bugs
    that made prepare_week 500 on every call.
    """
    if value is None:
        return ''
    if hasattr(value, 'date'):
        return value.date().isoformat()
    return str(value)[:10]


def _analyze_progression(logs, target_reps=None, measurement_type=None, unit='kg'):
    """
    Group SetLog history by session date and detect a progression signal.

    NOT a view -- it is called internally. It previously carried @api_view and
    @permission_classes decorators that belonged to workout_plan_prepare_week,
    which both broke that endpoint and turned this into a DRF view that could not
    be called with a plain list.

    Branches on measurement_type so a plank progresses on time and a pull-up on
    reps, instead of everything being treated as a barbell lift. Summaries are
    written in `unit` so the AI is never handed kg while the user thinks in lb.
    """
    measurement_type = measurement_type or exercise_types.DEFAULT_TYPE
    if measurement_type == exercise_types.WARMUP:
        return {'direction': None, 'summary': None}
    if not logs or len(logs) < 3:
        return {'direction': None, 'summary': None}

    # Which field carries the progression signal for this exercise type.
    if measurement_type in (exercise_types.DURATION, exercise_types.WEIGHT_DURATION):
        metric_field, metric_label = 'duration_seconds', ' sec'
    elif measurement_type == exercise_types.BODYWEIGHT_REPS:
        metric_field, metric_label = 'reps_completed', 'reps'
    else:
        metric_field, metric_label = 'weight_kg', unit

    by_session = {}
    for log in logs:
        by_session.setdefault(_session_date(log['workout_session__started_at']), []).append(log)

    # Look back 8 sessions, not 4. The most useful datum when someone has
    # over-jumped is the weight they last hit the target at, which can easily be
    # several sessions back.
    sessions = sorted(by_session.items(), reverse=True)[:8]
    if len(sessions) < 2:
        return {'direction': None, 'summary': None}

    # A prescribed range has two thresholds. `low` is "did they reach the
    # range at all", `high` is "did they top it and earn more weight".
    # target_reps may be a bare int (legacy callers) or a (low, high) tuple.
    if isinstance(target_reps, (tuple, list)):
        target_low, target_high = target_reps
    else:
        target_low = target_high = target_reps
    target_high = target_high or 10
    target_low = target_low or target_high
    target = target_high          # retained for the summary strings

    results = []
    for _date, sets in sessions:
        values = [s[metric_field] for s in sets if s.get(metric_field)]
        if not values:
            continue
        reps = [s['reps_completed'] or 0 for s in sets]
        best = max(values)
        if metric_field == 'weight_kg':
            best = _to_display_weight(best, unit)
            worked = [r for r in reps if r > 0]
            # Reached the range -> not a failure, even if the top wasn't hit.
            hit_all = bool(worked) and all(r >= target_low for r in worked)
            # Topped the range -> eligible for more weight.
            topped = bool(worked) and all(r >= target_high for r in worked)
        else:
            # For time/rep-driven work the metric IS the target.
            hit_all = topped = True
        results.append({'value': best, 'hit_all': hit_all, 'topped': topped,
                        'reps': reps})

    if len(results) < 2:
        return {'direction': None, 'summary': None}

    recent, prev = results[0], results[1]

    def fmt(value):
        return f'{value}{metric_label}' if metric_label != 'reps' else f'{value} reps'

    if metric_field != 'weight_kg':
        # Time/reps: progress when the latest session matched or beat the previous.
        if recent['value'] > prev['value']:
            direction, summary = 'increase', (
                f"Improved to {fmt(recent['value'])} from {fmt(prev['value'])} "
                f"→ KEEP PROGRESSING")
        elif recent['value'] == prev['value']:
            direction, summary = 'increase', (
                f"Held {fmt(recent['value'])} for 2 sessions → INCREASE TARGET")
        else:
            direction, summary = 'maintain', (
                f"Dropped to {fmt(recent['value'])} from {fmt(prev['value'])} "
                f"→ REBUILD CONSISTENCY")
        return {'direction': direction, 'last_value': recent['value'], 'summary': summary}

    # The heaviest weight at which the target rep count was actually achieved,
    # across all the history we were given. This is the single most useful fact
    # for recovering from an over-ambitious jump, and it was previously ignored.
    proven = [r['value'] for r in results if r['hit_all'] and r['value'] is not None]
    best_at_target = max(proven) if proven else None

    target_str = (f'{target_low}-{target_high}'
                  if target_low != target_high else str(target_high))

    # Double progression: completing EVERY set at the top of the range earns a
    # weight increase next session. One such session is the standard trigger.
    #
    # This previously required two CONSECUTIVE top-of-range sessions at the same
    # weight, which is unusually conservative and gave visibly wrong advice: a
    # user hitting 4x12 at 100lb on an 8-12 target was told to "maintain" because
    # the session before that had been 10 reps -- and had been stuck at the same
    # weight since July, repeatedly topping the range, never once told to add
    # load.
    if recent['topped']:
        streak = ' for 2 sessions' if (
            prev['topped'] and recent['value'] == prev['value']) else ''
        return {
            'direction': 'increase',
            'last_value': recent['value'],
            'best_at_target': best_at_target,
            'summary': (
                f"Completed all sets at {target_high}+ reps with "
                f"{fmt(recent['value'])}{streak} → INCREASE WEIGHT"),
        }

    recent_avg = sum(recent['reps']) / max(len([r for r in recent['reps'] if r]), 1)

    # One badly-missed session is enough.
    #
    # This used to require BOTH of the last two sessions to have failed, so a
    # jump from 50lb (20 reps) to 90lb (8 reps) fell through to "maintain" --
    # recommending the user stay at a weight they had just failed at by half.
    # Falling >25% short of the BOTTOM of the range is unambiguous on its own.
    if not recent['hit_all'] and recent_avg < target_low * 0.75:
        summary = (
            f"Only {recent_avg:.0f} reps at {fmt(recent['value'])} last session "
            f"(target {target_str}) → DECREASE WEIGHT")
        if best_at_target is not None and best_at_target < recent['value']:
            summary += (
                f". Previously hit {target_low}+ reps at {fmt(best_at_target)} "
                f"→ go back to about {fmt(best_at_target)}")
        return {
            'direction': 'decrease',
            'last_value': recent['value'],
            'best_at_target': best_at_target,
            'summary': summary,
        }

    # Inside the range but not topping it: hold the weight and chase reps. This
    # is the case that used to be misreported as a failure.
    if recent['hit_all'] and not recent['topped']:
        return {
            'direction': 'maintain',
            'last_value': recent['value'],
            'best_at_target': best_at_target,
            'summary': (
                f"{recent_avg:.0f} reps at {fmt(recent['value'])} — inside the "
                f"{target_str} range → HOLD WEIGHT, ADD REPS until "
                f"{target_high}"),
        }

    # Two consecutive mild misses also warrant backing off.
    if not recent['hit_all'] and not prev['hit_all']:
        return {
            'direction': 'decrease',
            'last_value': recent['value'],
            'best_at_target': best_at_target,
            'summary': (
                f"Short of the {target_str} range at {fmt(recent['value'])} for "
                f"2 sessions → DECREASE WEIGHT"),
        }

    return {
        'direction': 'maintain',
        'last_value': recent['value'] if recent.get('value') else None,
        'best_at_target': best_at_target,
        'summary': (
            f"Consistent at {fmt(recent['value'])}, maintaining"
            if recent.get('value') else None),
    }


# Bumped when the cached, AI-generated portion of a guide (steps/muscles/tips)
# should be regenerated because the prompt changed. The volatile advice fields
# are NOT cached at all, so they need no version.
ADVICE_VERSION = 2


def _exercise_history(user, exercise_name, measurement_type, limit=30):
    """
    Recent completed sets for one exercise, filtered on the field that matters
    for its measurement type. Shared by prepare_week and the guide endpoint so
    they can never disagree about what the history is.
    """
    metric = exercise_types.CHART_CONTRACT.get(
        measurement_type,
        exercise_types.CHART_CONTRACT[exercise_types.DEFAULT_TYPE],
    )['metric']
    if not metric:
        return [], None
    logs = list(
        SetLog.objects
        .filter(
            workout_session__user=user,
            exercise_name__iexact=exercise_name,
            is_completed=True,
            **{'%s__isnull' % metric: False}
        )
        .order_by('-workout_session__started_at')
        .values(
            'workout_session__started_at', 'set_number',
            'weight_kg', 'reps_completed', 'duration_seconds',
        )[:limit]
    )
    return logs, metric


def _build_exercise_advice(user, exercise_name, measurement_type, reps, unit):
    """
    Progression advice for one exercise, computed from logged history.

    Deliberately deterministic and uncached. The advice used to be AI prose
    baked into the ExerciseGuide row by prepare_week, which meant it was wrong
    the moment a new set was logged and stayed wrong until the next week was
    prepared -- all 31 production guides were still repeating "Maintain 90.0lb"
    days after the logic that produced it had been fixed.

    Computing it per request costs one query and no AI call, and cannot go
    stale. The cached guide keeps only the stable content (steps, muscles, tips,
    images), which genuinely does not change between sessions.

    Returns the three advice fields, any of which may be None.
    """
    blank = {'recommended_weight': None, 'recommended_target': None,
             'coaching_note': None}
    if measurement_type == exercise_types.WARMUP:
        return blank

    logs, metric = _exercise_history(user, exercise_name, measurement_type)
    if not logs:
        return blank

    low, high = _target_rep_range(reps)
    prog = _analyze_progression(
        logs, target_reps=(low, high), measurement_type=measurement_type,
        unit=unit,
    )
    direction = prog.get('direction')
    last = prog.get('last_value')
    proven = prog.get('best_at_target')
    if not direction or last is None:
        return blank

    out = dict(blank)
    out['coaching_note'] = prog.get('summary')

    if measurement_type in (exercise_types.WEIGHT_REPS,
                            exercise_types.WEIGHT_DURATION):
        body = 'lower' if re.search(
            r'\b(squat|deadlift|leg|calf|lunge|hip|glute|hamstring)\b',
            exercise_name, re.I) else 'upper'
        step = _increment_for(unit, body)
        if direction == 'increase':
            # Don't claim a session count here -- the streak is only known to
            # _analyze_progression, and asserting "twice" was wrong whenever a
            # single top-of-range session triggered the increase.
            top = f' ({high} reps)' if high else ''
            out['recommended_weight'] = (
                f'Go up to {round(last + step, 1)}{unit} — you completed every '
                f'set at the top of the range{top} with {last}{unit}.')
        elif direction == 'decrease':
            if proven is not None and proven < last:
                out['recommended_weight'] = (
                    f'Drop to about {proven}{unit} — that is where you last hit '
                    f'the target reps. {last}{unit} was too heavy last session.')
            else:
                out['recommended_weight'] = (
                    f'Drop below {last}{unit} — you could not complete the '
                    f'target reps at it last session.')
        else:
            target_hint = f' Build to {high} reps before adding load.' if high else ''
            out['recommended_weight'] = f'Stay at {last}{unit}.{target_hint}'

    elif measurement_type == exercise_types.DURATION:
        if direction == 'increase':
            step = _duration_increment(int(last))
            out['recommended_target'] = (
                f'Aim for {int(last) + step} sec — you held {int(last)} sec.')
        else:
            out['recommended_target'] = (
                f'Rebuild to {int(last)} sec with clean form.')

    elif measurement_type == exercise_types.BODYWEIGHT_REPS:
        if direction == 'increase':
            out['recommended_target'] = (
                f'Go for {int(last) + 2} reps — you hit {int(last)}.')
        else:
            out['recommended_target'] = (
                f'Rebuild to {int(last)} clean reps.')

    return out


def _exercise_prescription(user, exercise_name, exercise_id=None):
    """
    The measurement type and prescribed reps for one of the user's exercises.

    `exercise_id` identifies the exact row the user is looking at and is the only
    unambiguous answer, because one name legitimately carries DIFFERENT
    prescriptions across weeks and plans. Leg Press, for this user, exists 14
    times at 10, 10-12, 12, 8-10 and 10-15 reps.

    Without an id this used to be an unordered `.first()`, so the advice was
    computed against an arbitrary one of those rows: the guide told the user
    "inside the 10-15 range" off a plan abandoned in June while the card in front
    of them said 10-12. The fallback ordering below is therefore deliberate --
    active plan first, then the week the plan is actually on, then the newest
    plan -- so that even the nameless lookup lands on the row the UI is showing.
    """
    qs = Exercise.objects.filter(day__plan__user=user)

    if exercise_id:
        try:
            # Still scoped to the user: an id from another account must not
            # resolve. A junk id is a 500 from the UUID field, so swallow it and
            # fall through rather than failing the whole guide request.
            row = (qs.filter(pk=exercise_id)
                     .values_list('measurement_type', 'reps')
                     .first())
        except (ValidationError, ValueError, TypeError):
            row = None
        if row:
            return row[0] or exercise_types.classify(exercise_name), row[1]
        # Fall through: a stale id (deleted plan) should degrade, not 404.

    row = (
        qs.filter(name__iexact=exercise_name)
        .annotate(on_current_week=Case(
            When(day__week_number=F('day__plan__current_week'), then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ))
        .order_by('-day__plan__is_active', '-on_current_week',
                  '-day__plan__created_at', 'day__week_number', 'day__day_number')
        .values_list('measurement_type', 'reps')
        .first()
    )
    if row:
        return row[0] or exercise_types.classify(exercise_name), row[1]
    return exercise_types.classify(exercise_name), None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_plan_prepare_week(request, plan_id):
    """
    Bulk-generate per-user ExerciseGuide entries for the plan's current week,
    including progression advice derived from the user's own recent history.

    Previously non-functional: the @api_view/@permission_classes decorators had
    drifted onto _analyze_progression above, so this received a plain HttpRequest
    and returned an unrendered DRF Response (ContentNotRenderedError). Combined
    with a datetime being string-sliced, it 500'd on every single call -- which is
    why the personalised weight advice never appeared no matter how many times it
    was "fixed".
    """
    from .gemini import call_gemini_json, GeminiError as _GeminiError
    plan = get_object_or_404(WorkoutPlan, pk=plan_id, user=request.user)
    current_week = plan.current_week

    unit = _user_unit(request.user)

    # Collect this week's exercises WITH their measurement types; fall back to all
    # weeks for legacy single-week plans.
    rows = list(
        Exercise.objects
        .filter(day__plan=plan, day__week_number=current_week)
        .values_list('name', 'measurement_type', 'reps')
        .distinct()
    )
    if not rows:
        rows = list(
            Exercise.objects
            .filter(day__plan=plan)
            .values_list('name', 'measurement_type', 'reps')
            .distinct()
        )
    if not rows:
        return Response({'detail': 'No exercises found for this week', 'generated': 0})

    # De-duplicate by name, keeping the first measurement type seen.
    type_by_name = {}
    exercise_names = []
    reps_by_name = {}
    for name, mtype, reps in rows:
        if name in type_by_name:
            continue
        type_by_name[name] = mtype or exercise_types.DEFAULT_TYPE
        reps_by_name[name] = reps
        exercise_names.append(name)

    # Build history context. Filter on the field that actually matters for the
    # type rather than blanket-requiring weight_kg, which previously excluded all
    # bodyweight and duration work from the analysis.
    history_ctx = {}
    for ex_name in exercise_names:
        logs, _metric = _exercise_history(
            request.user, ex_name, type_by_name[ex_name])
        if logs:
            history_ctx[ex_name] = logs

    kb = _load_exercise_kb()

    prompt_lines = ['Return a JSON array. Each element corresponds to one exercise in the list below.']
    prompt_lines.append('For each exercise, return an object with:')
    prompt_lines.append('  steps: array of 4-6 clear action strings')
    prompt_lines.append('  muscles: array of 2-4 primary muscle names')
    prompt_lines.append('  tips: array of 2-3 form cues or common mistakes to avoid')
    prompt_lines.append('  category: one of squat/push/press/pull/row/hinge/curl/lunge/core/cardio/other')
    prompt_lines.append('  kb_key: best matching key from the image library, or null')
    prompt_lines.append('')
    prompt_lines.append(
        f'ALL WEIGHTS ARE IN {unit.upper()}. The user thinks in {unit}. Never mention '
        f'any other unit, and never convert. Write every number as "{unit}".'
    )
    prompt_lines.append('')
    prompt_lines.append('')
    prompt_lines.append(
        'The PROGRESSION hint is computed from the user\'s own logged sets and\n'
        'OVERRIDES your own judgement. If it says DECREASE WEIGHT, your advice\n'
        'must lower the weight — do not suggest maintaining or "working on reps"\n'
        'at a load the user demonstrably could not complete the target reps at.\n'
        'If it names a weight the target was previously hit at, recommend that.\n')
    prompt_lines.append('Exercises (the [tag] is authoritative — respect it):')
    for i, name in enumerate(exercise_names, 1):
        mtype = type_by_name[name]
        prescribed = reps_by_name.get(name)
        hist = history_ctx.get(name)
        hist_str = ''
        if hist:
            sets_summary = '; '.join(
                '%s: %s' % (
                    _session_date(s['workout_session__started_at']),
                    _format_set_for_prompt(s, mtype, unit),
                )
                for s in hist[:3]
            )
            progression = _analyze_progression(
                hist,
                target_reps=_target_rep_range(prescribed),
                measurement_type=mtype,
                unit=unit,
            )
            hist_str = f' [Recent: {sets_summary}'
            if progression['summary']:
                hist_str += f' | PROGRESSION: {progression["summary"]}'
            hist_str += ']'
        candidates = _get_kb_candidates(name, top_n=10)
        cands_str = ', '.join(candidates[:5]) if candidates else 'none'
        # The prescribed target is stated explicitly. Without it the model gave
        # advice like "keep working on increasing that rep count" while the plan
        # actually called for 15-20 and the user was managing 8.
        target_str = f' TARGET: {prescribed}' if prescribed else ''
        prompt_lines.append(
            f'{i}. {name} [{mtype}]{target_str}{hist_str} | '
            f'Image library candidates: {cands_str}')

    prompt = '\n'.join(prompt_lines)

    try:
        guides_list = call_gemini_json(
            system_prompt=(
                'You are a certified personal trainer providing exercise '
                f'instructions and progression advice. The user works in {unit}.'
            ),
            user_prompt=prompt,
        )
    except _GeminiError as e:
        return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    if not isinstance(guides_list, list):
        return Response({'detail': 'AI returned unexpected format'}, status=status.HTTP_502_BAD_GATEWAY)

    generated = 0
    for name, guide in zip(exercise_names, guides_list):
        if not isinstance(guide, dict):
            continue
        mtype = type_by_name.get(name, exercise_types.DEFAULT_TYPE)
        kb_key = guide.pop('kb_key', None)
        guide['images'] = _images_for_exercise(name, kb_key)
        guide['measurement_type'] = mtype
        guide['advice_version'] = ADVICE_VERSION

        # Advice is NOT cached. Strip anything advice-shaped the model volunteered
        # so a stale recommendation can never be served again; the guide endpoint
        # computes these fresh from logged history on every request.
        for volatile in ('recommended_weight', 'recommended_target',
                         'coaching_note'):
            guide.pop(volatile, None)

        # Personalised guides are per-user. update_or_create is safe now that
        # uniqueness is (user, name) -- the old code read with name__iexact but
        # created with the exact name, raising IntegrityError on case variants.
        ExerciseGuide.objects.update_or_create(
            user=request.user, name=name, defaults={'data': guide})
        generated += 1

    return Response({'generated': generated, 'exercises': exercise_names, 'unit': unit})


def _measurement_type_for_name(user, exercise_name):
    """
    Best-known measurement type for a logged exercise name.

    SetLog.exercise is SET_NULL, so history rows can outlive their Exercise row.
    Prefer a live Exercise owned by this user; fall back to the deterministic
    classifier so an orphaned row is still typed correctly.
    """
    row = (
        Exercise.objects
        .filter(day__plan__user=user, name__iexact=exercise_name)
        .values_list('measurement_type', flat=True)
        .first()
    )
    return row or exercise_types.classify(exercise_name)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def workout_exercises_list(request):
    """All exercises the user has ever logged, with summary stats."""
    from django.db.models import Max, Count
    rows = list(
        SetLog.objects
        .filter(workout_session__user=request.user, is_completed=True)
        .values('exercise_name')
        .annotate(
            last_session=Max('workout_session__started_at'),
            total_sessions=Count('workout_session', distinct=True),
            best_weight_kg=Max('weight_kg'),
            best_reps=Max('reps_completed'),
            best_duration_seconds=Max('duration_seconds'),
        )
        .order_by('-last_session')
    )
    # Tell the client how to interpret each row. Without this the UI guessed, and
    # `Max('weight_kg')` was shown as "last weight" even for timed exercises.
    for row in rows:
        row['measurement_type'] = _measurement_type_for_name(
            request.user, row['exercise_name'])
    return Response(rows)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def workout_exercise_history(request, exercise_name):
    """
    Full logged history for one exercise.

    Returns `measurement_type` so the caller can pick the right metric and unit
    instead of assuming weight. The old implementation filtered
    `weight_kg__isnull=False` with no type discriminator, which charted plank
    seconds as pounds and made bodyweight history invisible entirely.
    """
    measurement_type = _measurement_type_for_name(request.user, exercise_name)
    metric = exercise_types.CHART_CONTRACT.get(
        measurement_type, exercise_types.CHART_CONTRACT[exercise_types.DEFAULT_TYPE]
    )['metric']

    qs = SetLog.objects.filter(
        workout_session__user=request.user,
        exercise_name__iexact=exercise_name,
        is_completed=True,
    )
    # Only require a value in the field that actually matters for this type.
    if metric:
        qs = qs.filter(**{'%s__isnull' % metric: False})

    logs = list(
        qs.order_by('workout_session__started_at')
        .values(
            'workout_session__started_at', 'set_number',
            'weight_kg', 'reps_completed', 'duration_seconds',
        )
    )
    return Response({
        'exercise_name': exercise_name,
        'measurement_type': measurement_type,
        'metric': metric,
        'logs': logs,
    })


_EXERCISE_KB = None


def _load_exercise_kb() -> dict:
    global _EXERCISE_KB
    if _EXERCISE_KB is None:
        import json as _json
        import os as _os
        kb_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'exercise_kb.json')
        try:
            with open(kb_path) as f:
                _EXERCISE_KB = _json.load(f)
            logger.info('exercise_kb loaded: %d entries', len(_EXERCISE_KB))
        except Exception as exc:
            logger.warning('exercise_kb load failed: %s', exc)
            _EXERCISE_KB = {}
    return _EXERCISE_KB


_MOVEMENT_WORDS = {'curl', 'press', 'row', 'pull', 'push', 'squat', 'deadlift', 'lunge', 'raise',
                   'extension', 'fly', 'flye', 'dip', 'shrug', 'thrust', 'swing', 'clean', 'snatch'}

# Modifiers that change WHICH MUSCLE the exercise trains. If one of these appears
# on only one side of a comparison, the two exercises are different movements no
# matter how many tokens they share -- a "lateral raise" is not a "lying REAR
# lateral raise", and a chest-supported row is not a LYING row.
#
# Token overlap alone could not catch this: the extra word only cost a little
# precision, so "dumbbell lying rear lateral raise" still outscored the correct
# "side lateral raise" for a plain dumbbell lateral raise.
_FLIPPING_MODIFIERS = {
    'reverse', 'rear', 'lying', 'incline', 'decline', 'behind', 'front',
}

# Implements. A key naming different equipment to the query is a different
# exercise: "sled overhead triceps extension" must not illustrate a DUMBBELL
# overhead triceps extension. Both score 0.90 on token overlap alone, so without
# this the winner was decided by JSON key order.
_EQUIPMENT_WORDS = {
    'dumbbell', 'barbell', 'cable', 'machine', 'sled', 'kettlebell', 'smith',
    'band', 'bands', 'lever', 'sledgehammer',
}

_KB_STOP = {'the', 'a', 'an', 'with', 'on', 'in', 'of', 'to', 'and', 'or',
            'for', 'from', 'at', 'by'}


def _kb_stem(word: str) -> str:
    """
    Crude suffix stemmer shared by every KB comparison.

    The previous version tried suffixes in the order ing/ed/es/s and stripped the
    first that matched, which silently broke three common forms:

        'press'   -> 'pres'   but 'presses' -> 'press'   (never matched)
        'raises'  -> 'rais'   but 'raise'   -> 'raise'   (never matched)
        'lunges'  -> 'lung'   but 'lunge'   -> 'lunge'   (never matched)

    'press' also fell out of _MOVEMENT_WORDS, denying every press exercise the
    specificity bonus.

    Now 'es' is only stripped after a sibilant or 'y' ("presses", "crunches",
    "flyes"); every other plural loses just the 's'. Short words are left alone
    so 'swing' and 'lying' survive intact -- 'lying' in particular must stay
    whole, since it is a muscle-flipping modifier.
    """
    if word.endswith('ing') and len(word) > 5:
        return word[:-3]
    if word.endswith('ed') and len(word) > 4:
        return word[:-2]
    # Strip 'es' only when the remaining stem genuinely requires it -- i.e. it
    # ends in a sibilant or 'y'. Testing the WORD's ending instead of the STEM's
    # is what broke 'raises': it ends 'ses', so 'es' was stripped to give 'rais',
    # while 'raise' stayed whole and the two never matched.
    if word.endswith('es') and len(word) > 4 and word[:-2].endswith(
            ('ss', 'x', 'z', 'ch', 'sh', 'y')):
        return word[:-2]
    if word.endswith('s') and not word.endswith('ss') and len(word) > 3:
        return word[:-1]
    return word


def _kb_tokenize(text: str) -> set:
    words = re.sub(r'[^\w\s]', ' ', (text or '').lower()).split()
    return {_kb_stem(w) for w in words if w not in _KB_STOP}


# Stemmed once, so membership tests use the same form the tokenizer produces.
_MOVEMENT_STEMS = {_kb_stem(w) for w in _MOVEMENT_WORDS}
_FLIPPER_STEMS = {_kb_stem(w) for w in _FLIPPING_MODIFIERS}
_EQUIPMENT_STEMS = {_kb_stem(w) for w in _EQUIPMENT_WORDS}


def _kb_score(query_words: set, key_words: set) -> float:
    """
    F1 over stemmed tokens, with a movement-word bonus.

    Two disqualifiers return 0 outright, because a wrong illustration is worse
    than no illustration:
      - a muscle-flipping modifier on only one side (lying/rear/reverse/...)
      - conflicting equipment (query says dumbbell, key says sled)
    """
    overlap = len(query_words & key_words)
    if overlap == 0:
        return 0.0

    if (query_words & _FLIPPER_STEMS) != (key_words & _FLIPPER_STEMS):
        return 0.0

    query_equip = query_words & _EQUIPMENT_STEMS
    key_equip = key_words & _EQUIPMENT_STEMS
    if query_equip and key_equip and not (query_equip & key_equip):
        return 0.0

    recall = overlap / len(query_words)
    precision = overlap / len(key_words)
    f1 = 2 * recall * precision / (recall + precision)
    # Bonus: if both query and key contain the same movement type word, prefer specificity
    shared_movement = bool((query_words & _MOVEMENT_STEMS) & key_words)
    # Tie-break toward matching equipment, so "dumbbell ..." beats "sled ..."
    # for a dumbbell exercise instead of JSON ordering deciding it.
    equip_match = bool(query_equip & key_equip)
    return f1 + (0.15 if shared_movement else 0.0) + (0.05 if equip_match else 0.0)


KB_MATCH_THRESHOLD = 0.5


def _alias_for(name: str):
    """
    Alias lookup that tolerates how the AI actually writes exercise names.

    Tries, in order: the name as written; with any parenthetical qualifier
    removed; with a leading "Label: " prefix removed; with both removed.

    Each pass earns its place. Without paren-stripping, "Lateral Raises
    (Dumbbell)" missed the "lateral raises" alias. Without prefix-stripping,
    every "Metabolic Finisher: X" name diluted its own token overlap enough to
    fall below the match threshold and show no image at all.
    """
    text = re.sub(r'\s+', ' ', (name or '').lower()).strip()
    no_parens = re.sub(r'\s*\([^)]*\)', '', text).strip()
    # Only strip a short leading label, so "squat: low bar" style names survive.
    no_prefix = re.sub(r'^[a-z][a-z\s&-]{0,24}:\s*', '', text).strip()
    no_both = re.sub(r'^[a-z][a-z\s&-]{0,24}:\s*', '', no_parens).strip()

    for candidate in (text, no_parens, no_prefix, no_both):
        if candidate and candidate in _KB_ALIASES:
            return _KB_ALIASES[candidate]
    return None


def _resolve_kb_key(name: str):
    """
    THE single entry point for turning an exercise name into a KB key.

    Alias table first, then best scored match above the threshold.

    Previously the alias table was consulted only by _get_kb_candidates, which
    merely builds the shortlist shown to the AI. The image lookup ran its own
    unaliased copy of the scoring loop, so whenever the AI returned
    `kb_key: null` -- or on the generic-guide path -- every alias was bypassed.
    That is why "Pec Deck Fly Machine" still resolved to "reverse machine flyes"
    after the alias was added: the fix only ever covered half the paths.

    Returns (key, score) with key=None on a miss.
    """
    kb = _load_exercise_kb()

    alias = _alias_for(name)
    if alias and alias in kb:
        return alias, 1.0

    query_words = _kb_tokenize(name)
    if not query_words:
        return None, 0.0

    best_key, best_score = None, 0.0
    for key in kb:
        score = _kb_score(query_words, _kb_tokenize(key))
        if score > best_score:
            best_key, best_score = key, score

    if best_key and best_score >= KB_MATCH_THRESHOLD:
        return best_key, best_score
    return None, best_score


def _kb_key_is_consistent(name: str, key: str) -> bool:
    """
    Guard for a kb_key the AI chose.

    Even with a correct shortlist the model can pick a key whose
    muscle-flipping modifiers contradict the exercise, so re-apply the same
    rule _kb_score uses.
    """
    if not key:
        return False
    alias = _alias_for(name)
    if alias:
        return key == alias
    return (_kb_tokenize(name) & _FLIPPER_STEMS) == (_kb_tokenize(key) & _FLIPPER_STEMS)


def _lookup_exercise_images(name: str) -> list:
    kb = _load_exercise_kb()
    key, score = _resolve_kb_key(name)
    if key:
        logger.info('exercise_kb hit for %r via key %r (score=%.2f)', name, key, score)
        return kb[key][:2]
    logger.info('exercise_kb miss for %r (best score=%.2f)', name, score)
    return []


def _images_for_exercise(name: str, kb_key=None) -> list:
    """
    Images for one exercise, preferring an AI-chosen key but validating it.

    Replaces the `kb[kb_key][:2] if kb_key and kb_key in kb else
    _lookup_exercise_images(name)` expression that was duplicated across the
    guide-generating paths and performed no sanity check on kb_key.
    """
    kb = _load_exercise_kb()
    if kb_key and kb_key in kb and _kb_key_is_consistent(name, kb_key):
        return kb[kb_key][:2]
    if kb_key:
        logger.info('exercise_kb rejected AI key %r for %r', kb_key, name)
    return _lookup_exercise_images(name)


_KB_ALIASES = {
    'bird dog': 'superman',
    'bird-dog': 'superman',
    'birddog': 'superman',
    # NOTE: no 'pallof press' alias -- the KB contains "pallof press" verbatim,
    # so exact matching already finds it. The old alias pointed at
    # "cable pull through", which is not a KB key at all, so it silently did
    # nothing (and would have been the wrong exercise anyway).
    'dead bug': 'superman',
    # The KB has no "pec deck" key. Fuzzy matching scored "reverse machine
    # flyes" highest (shared "machine" + "flye" tokens), so a CHEST exercise was
    # illustrated with REAR-DELT photos. "butterfly" is this machine's standard
    # name in the KB.
    'pec deck': 'butterfly',
    'pec deck fly': 'butterfly',
    'pec deck flye': 'butterfly',
    'pec deck fly machine': 'butterfly',
    'pec deck machine': 'butterfly',
    'pec fly': 'butterfly',
    'pec fly machine': 'butterfly',
    'machine chest fly': 'butterfly',
    'machine chest flye': 'butterfly',
    'chest fly machine': 'butterfly',
    'butterfly machine': 'butterfly',
    # Guard the opposite direction too: rear-delt work must not match the chest
    # machine now that "butterfly" is strongly aliased.
    'rear delt machine': 'reverse machine flyes',
    'rear delt fly machine': 'reverse machine flyes',
    'reverse pec deck': 'reverse machine flyes',
    'reverse fly machine': 'reverse machine flyes',
    'machine reverse fly': 'reverse machine flyes',
    # A standing dumbbell lateral raise was matching "dumbbell lying rear
    # lateral raise" -- the model picked a LYING rear-delt exercise to
    # illustrate a standing side-delt one. "side lateral raise" is the right key.
    'lateral raise': 'side lateral raise',
    'lateral raises': 'side lateral raise',
    'dumbbell lateral raise': 'side lateral raise',
    'dumbbell lateral raises': 'side lateral raise',
    'db lateral raise': 'side lateral raise',
    'side lateral raise': 'side lateral raise',
    'side lateral raises': 'side lateral raise',
    'standing lateral raise': 'side lateral raise',
    'shoulder lateral raise': 'side lateral raise',
    'machine lateral raise': 'seated side lateral raise',
    'lateral raise machine': 'seated side lateral raise',
    'seated lateral raise': 'seated side lateral raise',
    'cable lateral raise': 'cable seated lateral raise',
    'cable lateral raises': 'cable seated lateral raise',
    # ...but a REAR lateral raise genuinely is the lying/bent-over movement.
    'rear lateral raise': 'dumbbell lying rear lateral raise',
    'rear delt raise': 'dumbbell lying rear lateral raise',
    'lying rear lateral raise': 'dumbbell lying rear lateral raise',
    'bent over lateral raise': 'bent over low-pulley side lateral',
    # Conditioning blocks. These arrive as "Metabolic Finisher: X" — the prefix
    # is stripped by _alias_for before lookup. The KB spells several of these
    # differently enough that token scoring never reaches the threshold.
    'battle ropes': 'battling ropes',
    'battle rope': 'battling ropes',
    'treadmill hiit': 'running, treadmill',
    'treadmill sprints': 'running, treadmill',
    'treadmill run': 'running, treadmill',
    'treadmill walk': 'walking, treadmill',
    'incline treadmill walk': 'walking, treadmill',
    'treadmill': 'walking, treadmill',
    'elliptical': 'elliptical trainer',
    'stationary bike': 'bicycling, stationary',
    'rowing machine': 'rowing, stationary',
}


def _get_kb_candidates(name: str, top_n: int = 20) -> list:
    """
    Shortlist of KB keys for Gemini to choose from.

    Shares the tokenizer, stemmer and scoring rules with _resolve_kb_key, so the
    shortlist can never disagree with what the image lookup would pick.
    """
    kb = _load_exercise_kb()

    alias = _alias_for(name)
    if alias and alias in kb:
        return [alias]

    query_words = _kb_tokenize(name)
    if not query_words:
        return []

    scored = []
    for key in kb:
        score = _kb_score(query_words, _kb_tokenize(key))
        if score > 0:
            scored.append((score, key))

    scored.sort(reverse=True)
    return [key for _, key in scored[:top_n]]


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def exercise_guide(request):
    name = request.GET.get('name', '').strip()
    if not name:
        return Response({'error': 'name is required'}, status=status.HTTP_400_BAD_REQUEST)

    unit = _user_unit(request.user)
    # The caller passes the id of the exact exercise row on the card, so the
    # advice is computed against the same prescription the user is reading.
    measurement_type, prescribed = _exercise_prescription(
        request.user, name, exercise_id=request.GET.get('exercise_id') or None)

    # Progression advice is computed fresh on EVERY request, never served from
    # the cache. It is derived from logged history, so a cached copy is wrong the
    # moment a set is logged -- which is exactly how 31 guides ended up still
    # repeating "Maintain 90.0lb" days after that logic was fixed.
    advice = _build_exercise_advice(
        request.user, name, measurement_type, prescribed, unit)

    # The cache holds only stable content: steps, muscles, tips, images.
    # Prefer this user's row, fall back to the shared generic one. `.filter()
    # .first()` rather than `.get()` because name uniqueness is scoped per-user,
    # so a case variant can legitimately match more than one row.
    cached = (
        ExerciseGuide.objects
        .filter(user=request.user, name__iexact=name)
        .first()
        or ExerciseGuide.objects.filter(user=None, name__iexact=name).first()
    )
    if cached is not None and isinstance(cached.data, dict):
        stale = (cached.data.get('advice_version') or 0) < ADVICE_VERSION
        if not stale:
            logger.info('exercise_guide: DB hit for %r (personalised=%s)',
                        name, cached.user_id is not None)
            payload = dict(cached.data)
            payload.update(advice)
            payload['measurement_type'] = measurement_type
            payload['unit'] = unit
            return Response(payload)
        logger.info('exercise_guide: stale cache for %r (v%s < v%s), regenerating',
                    name, cached.data.get('advice_version'), ADVICE_VERSION)

    # Get top KB candidates via F1 matching for Gemini to choose from
    kb_candidates = _get_kb_candidates(name, top_n=20)
    candidates_block = (
        f'\n\nExercise image library keys (pick the best match or null):\n{chr(10).join(kb_candidates)}'
        if kb_candidates else ''
    )

    prompt = f"""Return a JSON object for the exercise "{name}" with exactly these fields:
- "steps": array of 4-6 strings, each a clear action step for performing the exercise correctly
- "muscles": array of 2-4 strings, the primary muscles targeted (short names like "Quadriceps", "Glutes")
- "tips": array of 2-3 strings, common mistakes to avoid or form cues
- "category": exactly one of: squat, push, press, pull, row, hinge, curl, lunge, core, cardio, other
- "kb_key": the single best-matching key from the library below, or null if none is a good match{candidates_block}

Return only valid JSON, no extra text."""

    try:
        guide = call_gemini_json(
            system_prompt='You are a certified personal trainer providing exercise instruction.',
            user_prompt=prompt,
        )
        kb_key = guide.pop('kb_key', None)
        guide['images'] = _images_for_exercise(name, kb_key)
        guide['advice_version'] = ADVICE_VERSION
        # Generic row: stable content only, shared across users. Advice is never
        # written here -- it is computed per request from the caller's history.
        for volatile in ('recommended_weight', 'recommended_target',
                         'coaching_note'):
            guide.pop(volatile, None)
        ExerciseGuide.objects.update_or_create(
            user=None, name=name, defaults={'data': guide})
        payload = dict(guide)
        payload.update(advice)
        payload['measurement_type'] = measurement_type
        payload['unit'] = unit
        return Response(payload)
    except Exception as exc:
        logger.error('exercise_guide failed for %r: %s', name, exc, exc_info=True)
        return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def meal_guide(request):
    name = request.GET.get('name', '').strip()
    if not name:
        return Response({'error': 'name is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        cached = MealGuide.objects.get(name__iexact=name)
        logger.info('meal_guide: DB hit for %r', name)
        return Response(cached.data)
    except MealGuide.DoesNotExist:
        pass

    calories = request.GET.get('calories', '')
    protein = request.GET.get('protein', '')
    carbs = request.GET.get('carbs', '')
    fat = request.GET.get('fat', '')
    macro_hint = f' (target: {calories} kcal, {protein}g protein, {carbs}g carbs, {fat}g fat)' if calories else ''

    prompt = f"""Return a JSON object for the meal "{name}"{macro_hint} with exactly these fields:
- "ingredients": array of 6-10 strings, each an ingredient with quantity (e.g. "200g chicken breast", "1 tbsp olive oil")
- "steps": array of 4-7 strings, each a clear cooking/preparation step
- "prep_time": string, total prep + cook time (e.g. "25 minutes")
- "tips": array of 2-3 strings, tips for making it healthier, tastier, or easier

Return only valid JSON, no extra text."""

    try:
        guide = call_gemini_json(
            system_prompt='You are a professional chef and nutritionist providing healthy meal preparation guidance.',
            user_prompt=prompt,
        )
        MealGuide.objects.create(name=name, data=guide)
        return Response(guide)
    except Exception as exc:
        logger.error('meal_guide failed for %r: %s', name, exc, exc_info=True)
        return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Diet Planner
# ---------------------------------------------------------------------------

def _compute_tdee(profile) -> int:
    if not profile or not all([profile.weight_kg, profile.height_cm, profile.age, profile.gender]):
        return 2000  # fallback

    w = profile.weight_kg
    h = profile.height_cm
    a = profile.age

    if profile.gender == 'male':
        bmr = 10 * w + 6.25 * h - 5 * a + 5
    else:
        bmr = 10 * w + 6.25 * h - 5 * a - 161

    activity_multipliers = {
        'sedentary': 1.2,
        'lightly_active': 1.375,
        'moderately_active': 1.55,
        'very_active': 1.725,
    }
    multiplier = activity_multipliers.get(profile.activity_level or 'sedentary', 1.2)
    tdee = bmr * multiplier

    goal_adjustments = {
        'lose_fat': -500,
        'build_muscle': +300,
        'maintain': 0,
    }
    tdee += goal_adjustments.get(profile.fitness_goal or 'maintain', 0)
    return max(1200, round(tdee))


def _diet_macro_splits(target_calories: int, goal: str) -> tuple[int, int, int]:
    if goal == 'lose_fat':
        protein_pct, fat_pct, carb_pct = 0.35, 0.30, 0.35
    elif goal == 'build_muscle':
        protein_pct, fat_pct, carb_pct = 0.30, 0.25, 0.45
    else:
        protein_pct, fat_pct, carb_pct = 0.25, 0.30, 0.45

    protein_g = round((target_calories * protein_pct) / 4)
    fat_g = round((target_calories * fat_pct) / 9)
    carbs_g = round((target_calories * carb_pct) / 4)
    return protein_g, carbs_g, fat_g


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def diet_plan_generate(request):
    from .gemini import call_gemini_json, GeminiError as _GeminiError

    try:
        profile = request.user.profile
    except Exception:
        profile = None

    target_calories = _compute_tdee(profile)
    goal = getattr(profile, 'fitness_goal', 'maintain') or 'maintain'
    diet_pref = getattr(profile, 'dietary_preference', 'non_veg') or 'non_veg'
    protein_g, carbs_g, fat_g = _diet_macro_splits(target_calories, goal)

    # Cultural/preference fields from request
    country = (request.data.get('country') or '').strip()
    cuisine_preference = (request.data.get('cuisine_preference') or '').strip()
    usual_foods = (request.data.get('usual_foods') or '').strip()
    duration_days = int(request.data.get('duration_days') or 7)
    if duration_days not in (7, 14):
        duration_days = 7

    cultural_lines = []
    if country:
        cultural_lines.append(f'Country/region: {country}')
    if cuisine_preference:
        cultural_lines.append(f'Cuisine preference: {cuisine_preference}')
    if usual_foods:
        cultural_lines.append(f'Usual foods / preferences: {usual_foods}')
    cultural_text = ('\n' + '\n'.join(cultural_lines)) if cultural_lines else ''

    # Instructions extracted from an uploaded dietitian document
    # (see diet_document_parse). These come from a professional who has seen the
    # user, so they outrank the computed targets.
    dietitian_notes = (request.data.get('dietitian_notes') or '').strip()
    dietitian_text = ''
    if dietitian_notes:
        dietitian_text = (
            "\n\nThe user's dietitian has given them these written instructions. "
            'They take PRECEDENCE over the calculated targets above — where they '
            'conflict, follow the dietitian and adjust the macros to match. Do '
            'not contradict any stated restriction:\n'
            + dietitian_notes[:4000]
        )

    system = (
        'You are a certified nutritionist. Generate a structured multi-day diet plan as valid JSON only. '
        'No markdown, no explanation — just the JSON object.'
    )
    user_prompt = (
        f'Generate a {duration_days}-day diet plan with daily targets of {target_calories} kcal, '
        f'{protein_g}g protein, {carbs_g}g carbs, {fat_g}g fat. '
        f'Dietary preference: {diet_pref}. Fitness goal: {goal}.{cultural_text}'
        f'{dietitian_text}\n'
        f'Each day should have 4 meals (breakfast, lunch, dinner, snack). '
        f'Vary meals across days — do not repeat the same meals every day. '
        'Return JSON matching exactly this schema:\n'
        '{\n'
        '  "title": "string",\n'
        '  "description": "string",\n'
        '  "meals": [\n'
        '    {\n'
        '      "day_number": number,\n'
        '      "meal_type": "breakfast|lunch|dinner|snack",\n'
        '      "name": "string",\n'
        '      "description": "string",\n'
        '      "calories": number,\n'
        '      "protein_g": number,\n'
        '      "carbs_g": number,\n'
        '      "fat_g": number\n'
        '    }\n'
        '  ]\n'
        '}\n'
        f'Include meals for all {duration_days} days (day_number 1 through {duration_days}). '
        'Do not truncate the JSON.'
    )

    try:
        plan_data = call_gemini_json(system_prompt=system, user_prompt=user_prompt)
    except _GeminiError as e:
        return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    if 'title' not in plan_data or 'meals' not in plan_data:
        return Response({'detail': 'AI returned invalid diet plan.'}, status=status.HTTP_502_BAD_GATEWAY)

    plan_data['target_calories'] = target_calories
    plan_data['protein_g'] = protein_g
    plan_data['carbs_g'] = carbs_g
    plan_data['fat_g'] = fat_g
    return Response(plan_data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def diet_plans(request):
    if request.method == 'GET':
        plans = DietPlan.objects.filter(user=request.user)
        return Response(DietPlanSerializer(plans, many=True).data)

    data = request.data
    if 'title' not in data or 'meals' not in data:
        return Response({'detail': 'title and meals are required.'}, status=status.HTTP_400_BAD_REQUEST)

    plan = DietPlan.objects.create(
        user=request.user,
        title=data['title'],
        description=data.get('description', ''),
        target_calories=data.get('target_calories', 2000),
        protein_g=data.get('protein_g', 0),
        carbs_g=data.get('carbs_g', 0),
        fat_g=data.get('fat_g', 0),
        generated_by_ai=True,
    )
    for i, meal_data in enumerate(data['meals']):
        Meal.objects.create(
            plan=plan,
            day_number=meal_data.get('day_number', 1),
            meal_type=meal_data.get('meal_type', 'snack'),
            name=meal_data['name'],
            description=meal_data.get('description', ''),
            calories=meal_data.get('calories', 0),
            protein_g=meal_data.get('protein_g', 0),
            carbs_g=meal_data.get('carbs_g', 0),
            fat_g=meal_data.get('fat_g', 0),
            order=i,
        )

    return Response(DietPlanDetailSerializer(plan).data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def diet_plan_detail(request, plan_id):
    plan = get_object_or_404(DietPlan, pk=plan_id, user=request.user)

    if request.method == 'GET':
        return Response(DietPlanDetailSerializer(plan).data)

    plan.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def diet_plan_activate(request, plan_id):
    plan = get_object_or_404(DietPlan, pk=plan_id, user=request.user)
    DietPlan.objects.filter(user=request.user, is_active=True).update(is_active=False)
    plan.is_active = True
    plan.save(update_fields=['is_active'])
    return Response(DietPlanSerializer(plan).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def food_scan(request):
    from .gemini import call_gemini_vision_json, GeminiError as _GeminiError

    image_file = request.FILES.get('image')
    if not image_file:
        return Response({'detail': 'image is required.'}, status=status.HTTP_400_BAD_REQUEST)

    image_bytes = image_file.read()
    mime_type = image_file.content_type or 'image/jpeg'

    system = (
        'You are a nutrition expert. Analyze the food image and return macronutrient data as JSON only. '
        'No markdown, no explanation.'
    )
    user_prompt = (
        'Identify the food(s) in this image and estimate nutritional info for the visible serving. '
        'Return JSON matching exactly this schema:\n'
        '{\n'
        '  "food_name": "string",\n'
        '  "serving_size": "string (e.g. 1 plate, 200g)",\n'
        '  "calories": number,\n'
        '  "protein_g": number,\n'
        '  "carbs_g": number,\n'
        '  "fat_g": number,\n'
        '  "fiber_g": number or null,\n'
        '  "notes": "string"\n'
        '}'
    )

    try:
        result = call_gemini_vision_json(
            system_prompt=system,
            user_prompt=user_prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
    except _GeminiError as e:
        return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    if 'food_name' not in result or 'calories' not in result:
        return Response({'detail': 'AI returned invalid food data.'}, status=status.HTTP_502_BAD_GATEWAY)

    scan = FoodScanResult.objects.create(
        user=request.user,
        food_name=result['food_name'],
        serving_size=result.get('serving_size', ''),
        calories=result.get('calories', 0),
        protein_g=result.get('protein_g', 0),
        carbs_g=result.get('carbs_g', 0),
        fat_g=result.get('fat_g', 0),
        fiber_g=result.get('fiber_g'),
        notes=result.get('notes', ''),
    )
    return Response(FoodScanResultSerializer(scan).data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def food_scan_history(request):
    scans = FoodScanResult.objects.filter(user=request.user)[:50]
    return Response(FoodScanResultSerializer(scans, many=True).data)


# ---------------------------------------------------------------------------
# Body Scanner
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def body_scan(request):
    from .gemini import call_gemini_vision_json, GeminiError as _GeminiError

    image_file = request.FILES.get('image')
    if not image_file:
        return Response({'detail': 'image is required.'}, status=status.HTTP_400_BAD_REQUEST)

    image_bytes = image_file.read()
    mime_type = image_file.content_type or 'image/jpeg'

    system = (
        'You are a certified fitness assessor. Analyze the body image and provide an assessment as JSON only. '
        'Always include a disclaimer that this is an AI estimate, not a medical assessment. '
        'No markdown, no explanation — just the JSON object.'
    )
    user_prompt = (
        'Analyze this body/physique image and provide a fitness assessment. '
        'Return JSON matching exactly this schema:\n'
        '{\n'
        '  "body_fat_pct": number or null,\n'
        '  "physique_category": "string (e.g. Lean, Athletic, Average, Overweight)",\n'
        '  "muscle_mass_note": "string",\n'
        '  "posture_notes": "string",\n'
        '  "recommendations": "string",\n'
        '  "disclaimer": "string"\n'
        '}'
    )

    try:
        result = call_gemini_vision_json(
            system_prompt=system,
            user_prompt=user_prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
    except _GeminiError as e:
        return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    if 'physique_category' not in result:
        return Response({'detail': 'AI returned invalid body scan data.'}, status=status.HTTP_502_BAD_GATEWAY)

    scan = BodyScanResult.objects.create(
        user=request.user,
        body_fat_pct=result.get('body_fat_pct'),
        physique_category=result.get('physique_category', ''),
        muscle_mass_note=result.get('muscle_mass_note', ''),
        posture_notes=result.get('posture_notes', ''),
        recommendations=result.get('recommendations', ''),
        disclaimer=result.get('disclaimer', 'This is an AI estimate, not a medical assessment.'),
    )
    return Response(BodyScanResultSerializer(scan).data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def body_scan_history(request):
    scans = BodyScanResult.objects.filter(user=request.user)[:20]
    return Response(BodyScanResultSerializer(scans, many=True).data)


# ---------------------------------------------------------------------------
# Meal Log
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def meal_logs(request):
    if request.method == 'GET':
        date_str = request.query_params.get('date')
        qs = MealLog.objects.filter(user=request.user)
        if date_str:
            qs = qs.filter(date=date_str)
        else:
            from django.utils import timezone as _tz
            qs = qs.filter(date=_tz.now().date())
        return Response(MealLogSerializer(qs, many=True).data)

    ser = MealLogSerializer(data=request.data)
    if not ser.is_valid():
        return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
    ser.save(user=request.user)
    return Response(ser.data, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def meal_log_detail(request, log_id):
    log = get_object_or_404(MealLog, pk=log_id, user=request.user)
    log.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Health Sync (Apple Shortcuts)
# ---------------------------------------------------------------------------

@api_view(['GET', 'DELETE'])
@permission_classes([IsAuthenticated])
def health_connect(request):
    """GET: returns the user's auth token for use in the Shortcuts setup.
       DELETE: disconnects and wipes all health data."""
    if request.method == 'DELETE':
        try:
            request.user.health_connection.delete()
        except HealthConnection.DoesNotExist:
            pass
        HealthDailySummary.objects.filter(user=request.user).delete()
        HealthWorkout.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # The model mints the token on save; no derivation here. This used to
    # duplicate the md5 scheme, so fixing the model alone would not have been
    # enough.
    conn, _ = HealthConnection.objects.get_or_create(
        user=request.user, defaults={'provider': 'APPLE'})
    return Response({'token': conn.sync_token})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def health_connect_rotate(request):
    """
    Issue a fresh sync token, invalidating the old one.

    Previously impossible: both save() and health_connect only filled a blank
    field, so a leaked token could only be dealt with by DELETE -- which wipes
    every health record the user has. Rotating keeps the data.
    """
    conn, _ = HealthConnection.objects.get_or_create(
        user=request.user, defaults={'provider': 'APPLE'})
    token = conn.rotate_sync_token()
    logger.warning('health sync token rotated for %s', request.user.email)
    return Response({
        'token': token,
        'detail': 'Previous token is now invalid. Update your Shortcut.',
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_status(request):
    try:
        conn = request.user.health_connection
    except HealthConnection.DoesNotExist:
        return Response({'connected': False, 'provider': None,
                         'connected_at': None, 'last_sync_at': None,
                         'has_data': False, 'latest_summary_date': None})

    has_data = (
        HealthDailySummary.objects.filter(user=request.user).exists() or
        HealthWorkout.objects.filter(user=request.user).exists()
    )
    latest = (HealthDailySummary.objects
              .filter(user=request.user).order_by('-date').first())
    # `connected` now means "a connection exists", not "data has arrived".
    # Conflating them made a freshly connected user look disconnected until
    # their first successful sync, which is exactly when they need to see that
    # the connection is live.
    return Response({
        'connected': True,
        'provider': conn.provider,
        'connected_at': conn.connected_at.isoformat(),
        'last_sync_at': conn.last_sync_at.isoformat() if conn.last_sync_at else None,
        'has_data': has_data,
        'latest_summary_date': latest.date.isoformat() if latest else None,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_summary(request):
    summaries = HealthDailySummary.objects.filter(user=request.user).order_by('-date')[:30]
    data = [
        {
            'date': s.date.isoformat(),
            'steps': s.steps,
            'active_calories': s.active_calories,
            'resting_calories': s.resting_calories,
            'resting_heart_rate': s.resting_heart_rate,
            'sleep_hours': s.sleep_hours,
        }
        for s in summaries
    ]
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_series(request):
    """
    Time-bucketed health metrics for the charts.

    ?range=daily|weekly|monthly  (14 days / 12 weeks / 6 months)

    Bucketing lives in api/health_series.py so this endpoint, the UI and the
    MCP tool all agree on what a range means.
    """
    from .health_series import build_series, RANGES, DEFAULT_RANGE

    range_key = (request.GET.get('range') or DEFAULT_RANGE).lower()
    if range_key not in RANGES:
        return Response(
            {'detail': 'range must be one of %s' % sorted(RANGES)},
            status=status.HTTP_400_BAD_REQUEST)
    return Response(build_series(request.user, range_key))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_workouts(request):
    workouts = HealthWorkout.objects.filter(user=request.user).order_by('-start_time')[:20]
    data = [
        {
            'id': str(w.id),
            'activity_type': w.activity_type,
            'start_time': w.start_time.isoformat(),
            'duration_seconds': w.duration_seconds,
            'calories': w.calories,
            'avg_heart_rate': w.avg_heart_rate,
            'distance_meters': w.distance_meters,
        }
        for w in workouts
    ]
    return Response(data)


# One spec instead of five near-identical try/except blocks. (key, caster)
_HEALTH_DAILY_FIELDS = (
    ('steps', lambda v: int(float(v))),
    ('active_calories', float),
    ('resting_calories', float),
    ('resting_heart_rate', float),
    ('sleep_hours', float),
)

_HEALTH_WORKOUT_FIELDS = (
    ('duration_seconds', lambda v: int(float(v))),
    ('calories', float),
    ('avg_heart_rate', float),
    ('distance_meters', float),
)

# Sanity bounds. A sync client sending nonsense is more likely than a human
# genuinely walking four million steps, and a bad value poisons the AI prompts
# and the calorie maths downstream.
_HEALTH_LIMITS = {
    'steps': (0, 200000),
    'active_calories': (0, 20000),
    'resting_calories': (0, 10000),
    'resting_heart_rate': (20, 220),
    'sleep_hours': (0, 24),
}


def _clean_number(value):
    """Shortcuts sends thousands separators and stray whitespace."""
    return str(value).replace(',', '').strip()


def _parse_health_payload(body):
    """
    Parse a sync payload, tolerating the one malformation Shortcuts produces.

    Apple Shortcuts substitutes an empty string when a HealthKit query returns
    no samples, yielding `"steps": ,` -- invalid JSON. The previous code patched
    that with regex over the RAW BODY, which would also corrupt any string value
    containing ": " before a comma or brace (an activity_type of
    "Run: morning, easy" became mangled).

    Strict parse first; only fall back to the narrow repair if that fails, and
    report an error rather than silently returning {}.

    Returns (data, error_message).
    """
    try:
        raw = body.decode('utf-8')
    except (UnicodeDecodeError, AttributeError) as exc:
        return {}, 'body is not valid UTF-8 (%s)' % exc

    if not raw.strip():
        return {}, 'body is empty'

    try:
        data = json.loads(raw)
    except ValueError:
        repaired = re.sub(r':\s*(?=[,}])', ': null', raw)
        try:
            data = json.loads(repaired)
        except ValueError as exc:
            return {}, 'invalid JSON (%s)' % exc

    if not isinstance(data, dict):
        return {}, 'payload must be a JSON object'
    return data, None


# A sync client sending a date this far back is almost certainly sending a
# STATIC date rather than today's. Not an error -- backfilling old days is
# legitimate -- but it must be visible.
HEALTH_STALE_DATE_DAYS = 7


def _parse_health_date(value):
    """
    Validate the summary date. Returns (date, error_message).

    The raw string used to go straight into a DateField lookup, so a malformed
    value was a 500 and a typo'd year silently created a summary in 2206.
    """
    from django.utils import timezone as _tz
    today = _tz.now().date()
    if value in (None, ''):
        return today, None

    parsed = parse_date(str(value).strip())
    if parsed is None:
        return None, 'date must be ISO format (YYYY-MM-DD), got %r' % (value,)
    if parsed > today + timedelta(days=1):
        return None, 'date %s is in the future' % parsed.isoformat()
    if parsed < today - timedelta(days=3650):
        return None, 'date %s is implausibly old' % parsed.isoformat()
    return parsed, None


def _stale_date_warning(supplied, resolved):
    """
    Warn when a client keeps writing to a long-past date.

    A real Apple Shortcut was built with the date captured as a STATIC value, so
    for five months every sync overwrote one row dated the day before setup.
    Nothing was ever filed under today, yet the endpoint reported success each
    time and the app showed a row that kept updating. Silence made that
    invisible; this makes it loud.
    """
    from django.utils import timezone as _tz
    if supplied in (None, '') or resolved is None:
        return None
    age = (_tz.now().date() - resolved).days
    if age < HEALTH_STALE_DATE_DAYS:
        return None
    return (
        'date %s is %d days old — if your client is meant to be syncing TODAY, '
        'it is sending a static date. Omit the date field entirely and the '
        'server will use today.' % (resolved.isoformat(), age)
    )


def _coerce_health_fields(payload):
    """
    Cast and range-check the daily summary fields.

    Returns (stored, problems). Only keys actually present and valid are
    returned, so a partial payload never nulls out previously-synced values.
    """
    stored, problems = {}, []
    for key, cast in _HEALTH_DAILY_FIELDS:
        if payload.get(key) is None:
            continue
        try:
            value = cast(_clean_number(payload[key]))
        except (TypeError, ValueError):
            problems.append('%s: %r is not a number' % (key, payload[key]))
            continue
        low, high = _HEALTH_LIMITS.get(key, (None, None))
        if low is not None and not (low <= value <= high):
            problems.append('%s: %s outside %s-%s' % (key, value, low, high))
            continue
        stored[key] = value
    return stored, problems


def _store_health_workouts(user, workouts):
    """
    Upsert workouts, scoped to the owning user.

    Returns (count_stored, problems). The lookup is filtered by user: with
    global uniqueness and an unscoped update_or_create, a client reusing another
    account's workout id reassigned that account's row to itself.
    """
    stored, problems = 0, []
    if not isinstance(workouts, list):
        return 0, ['workouts must be a list']

    for index, entry in enumerate(workouts):
        if not isinstance(entry, dict):
            problems.append('workouts[%d] is not an object' % index)
            continue
        start_raw = entry.get('start_time') or ''
        start_time = parse_datetime(str(start_raw)) if start_raw else None
        if not start_time:
            problems.append(
                'workouts[%d]: start_time missing or unparseable (%r)'
                % (index, start_raw))
            continue

        external_id = str(entry.get('id') or '%s-%s' % (user.id, start_raw))
        defaults = {
            'activity_type': str(entry.get('activity_type') or 'Workout')[:128],
            'start_time': start_time,
        }
        for key, cast in _HEALTH_WORKOUT_FIELDS:
            if entry.get(key) is None:
                continue
            try:
                defaults[key] = cast(_clean_number(entry[key]))
            except (TypeError, ValueError):
                problems.append('workouts[%d].%s: %r is not a number'
                                % (index, key, entry[key]))

        HealthWorkout.objects.update_or_create(
            user=user, terra_workout_id=external_id, defaults=defaults)
        stored += 1

    return stored, problems


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def health_shortcuts_sync(request):
    """GET: validates the sync token — Shortcut calls this first to confirm connection.
    POST: receives the health data payload.
    Both authenticated via permanent sync token (Authorization: Sync <token>)."""
    from django.utils import timezone as _tz

    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Sync '):
        return Response({'detail': 'Sync token required.'}, status=status.HTTP_401_UNAUTHORIZED)
    sync_token = auth_header[5:].strip()
    try:
        conn = HealthConnection.objects.select_related('user').get(sync_token=sync_token)
    except HealthConnection.DoesNotExist:
        return Response({'detail': 'Invalid sync token.'}, status=status.HTTP_401_UNAUTHORIZED)

    sync_user = conn.user

    if request.method == 'GET':
        return Response({'status': 'ready', 'user': sync_user.email})

    parsed, parse_error = _parse_health_payload(request.body)
    if parse_error:
        # Previously this swallowed every exception and returned 200 with
        # nothing stored, so an agent syncing daily could fail silently forever.
        logger.warning('health sync: unparseable payload for %s: %s',
                       sync_user.email, parse_error)
        return Response({'detail': 'Could not parse payload: %s' % parse_error},
                        status=status.HTTP_400_BAD_REQUEST)

    summary_date, date_error = _parse_health_date(parsed.get('date'))
    if date_error:
        return Response({'detail': date_error},
                        status=status.HTTP_400_BAD_REQUEST)

    stored_fields, field_errors = _coerce_health_fields(parsed)
    stale_warning = _stale_date_warning(parsed.get('date'), summary_date)

    created_summary = False
    if stored_fields:
        _obj, created_summary = HealthDailySummary.objects.update_or_create(
            user=sync_user, date=summary_date, defaults=stored_fields)

    stored_workouts, workout_errors = _store_health_workouts(
        sync_user, parsed.get('workouts') or [])

    conn.last_sync_at = _tz.now()
    conn.save(update_fields=['last_sync_at'])

    problems = field_errors + workout_errors
    if stale_warning:
        problems.append(stale_warning)
    if problems:
        logger.warning('health sync for %s completed with %d problem(s): %s',
                       sync_user.email, len(problems), problems[:5])

    # Report what THIS call did. The old response returned the all-time summary
    # row count, which told a syncing agent nothing about whether it worked.
    return Response({
        'status': 'ok',
        'date': summary_date.isoformat(),
        'summary_created': created_summary,
        'fields_stored': sorted(stored_fields.keys()),
        'workouts_stored': stored_workouts,
        'problems': problems,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_recovery(request):
    rows = list(
        HealthDailySummary.objects
        .filter(user=request.user, resting_heart_rate__isnull=False)
        .order_by('-date')[:7]
    )
    if not rows:
        return Response({'score': None, 'label': 'No data yet', 'today_rhr': None, 'baseline_rhr': None})

    # Use most recent record as "today" — avoids UTC vs local date mismatch
    today_rhr = rows[0].resting_heart_rate
    baseline_rows = [r.resting_heart_rate for r in rows[1:]]

    if not baseline_rows:
        return Response({'score': None, 'label': 'No data yet', 'today_rhr': round(today_rhr, 1), 'baseline_rhr': None})

    baseline = sum(baseline_rows) / len(baseline_rows)
    diff = today_rhr - baseline

    if diff <= 0:
        score, label = 10, 'Optimal'
    elif diff <= 3:
        score, label = round(9 - diff), 'Good'
    elif diff <= 6:
        score, label = round(6 - (diff - 3)), 'Take it easy'
    else:
        score, label = max(1, round(3 - (diff - 6))), 'Rest day'

    return Response({
        'score': max(1, score),
        'label': label,
        'today_rhr': round(today_rhr, 1),
        'baseline_rhr': round(baseline, 1),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_calorie_balance(request):
    from django.utils import timezone as _tz
    today = _tz.now().date()

    todays_logs = MealLog.objects.filter(user=request.user, date=today)
    calories_in = sum(m.calories for m in todays_logs)

    burned = None
    try:
        from django.db.models import Q
        summary = (
            HealthDailySummary.objects
            .filter(user=request.user)
            .filter(Q(active_calories__isnull=False) | Q(resting_calories__isnull=False))
            .order_by('-date')
            .first()
        )
        if summary:
            burned = round((summary.active_calories or 0) + (summary.resting_calories or 0))
    except Exception as e:
        import logging; logging.getLogger(__name__).error('calorie_balance burned error: %s', e)

    net = (calories_in - burned) if burned is not None else None

    target = None
    try:
        active_plan = request.user.diet_plans.filter(is_active=True).first()
        if active_plan:
            target = active_plan.target_calories
    except Exception:
        pass

    if target is None:
        try:
            p = request.user.profile
            GOAL_DELTAS = {'lose_fat': -400, 'build_muscle': 300, 'maintain': 0}
            goal_delta = GOAL_DELTAS.get(p.fitness_goal or '', 0)
            if p.weight_kg and p.height_cm and p.age and p.gender:
                if p.gender == 'male':
                    bmr = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age + 5
                else:
                    bmr = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age - 161
                activity_factor = {
                    'sedentary': 1.2,
                    'lightly_active': 1.375,
                    'moderately_active': 1.55,
                    'very_active': 1.725,
                }.get(p.activity_level or '', 1.375)
                target = round(bmr * activity_factor + goal_delta)
        except Exception:
            pass

    net_goal_delta = 0
    try:
        p = request.user.profile
        GOAL_DELTAS = {'lose_fat': -400, 'build_muscle': 300, 'maintain': 0}
        net_goal_delta = GOAL_DELTAS.get(p.fitness_goal or '', 0)
    except Exception:
        pass

    burn_target = (target - net_goal_delta) if target is not None else None

    remaining_eat = None
    remaining_burn = None
    if target is not None and burned is not None and burn_target is not None:
        burn_overflow = max(0, burned - burn_target)
        eat_overflow  = max(0, calories_in - target)
        remaining_eat  = round((target - calories_in) + burn_overflow)
        remaining_burn = round((burn_target - burned) + eat_overflow)

    return Response({
        'calories_in': calories_in,
        'burned': burned,
        'net': net,
        'target': target,
        'burn_target': burn_target,
        'remaining_eat': remaining_eat,
        'remaining_burn': remaining_burn,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_activity_suggestion(request):
    from django.utils import timezone as _tz
    from datetime import timedelta
    today = _tz.now().date()

    rows = list(
        HealthDailySummary.objects
        .filter(user=request.user, date__gte=today - timedelta(days=14), active_calories__isnull=False)
    )
    if len(rows) < 3:
        return Response({'suggested': None})

    avg_calories = sum(r.active_calories for r in rows) / len(rows)

    if avg_calories < 200:
        suggested = 'sedentary'
    elif avg_calories < 350:
        suggested = 'lightly_active'
    elif avg_calories < 500:
        suggested = 'moderately_active'
    else:
        suggested = 'very_active'

    ACTIVITY_ORDER = ['sedentary', 'lightly_active', 'moderately_active', 'very_active']
    weekly_workouts = (
        request.user.workout_sessions
        .filter(is_completed=True, started_at__date__gte=today - timedelta(days=7))
        .count()
    )
    if weekly_workouts >= 3:
        if ACTIVITY_ORDER.index(suggested) < ACTIVITY_ORDER.index('moderately_active'):
            suggested = 'moderately_active'

    try:
        current = request.user.profile.activity_level or ''
    except Exception:
        current = ''

    if suggested == current:
        return Response({'suggested': None})

    return Response({
        'suggested': suggested,
        'avg_calories': round(avg_calories),
        'weekly_workouts': weekly_workouts,
        'current': current,
    })


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def _require_superuser(request):
    """
    Guard for the feedback queue.

    This is the security boundary for the automated implementation loop: that
    loop reads this table and writes code from it, so the table must only ever
    contain content authored by a trusted operator. Hiding the button in the UI
    is a convenience, not access control -- this check is.

    Returns a Response to return early, or None if the caller may proceed.
    """
    if not request.user.is_superuser:
        return Response(
            {'detail': 'Feedback submission is restricted to administrators.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def feedback(request):
    """
    POST  — queue a feedback item (superuser only).
    GET   — list the queue, newest first (superuser only).
            ?status=new filters by status.
    """
    denied = _require_superuser(request)
    if denied is not None:
        return denied

    if request.method == 'POST':
        serializer = FeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save(user=request.user)
        logger.info('feedback queued by %s: %s', request.user.email,
                    item.body[:120])
        return Response(FeedbackSerializer(item).data,
                        status=status.HTTP_201_CREATED)

    qs = Feedback.objects.all().select_related('user')
    status_filter = request.GET.get('status')
    if status_filter:
        qs = qs.filter(status=status_filter)
    return Response(FeedbackSerializer(qs[:200], many=True).data)


@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def feedback_detail(request, feedback_id):
    """
    Read, update or delete one feedback item (superuser only).

    PATCH is how the automation reports back: it sets `status`, `result_notes`
    and `commit_sha`. Those are read-only on the submit serializer, so they are
    applied explicitly here rather than through it.
    """
    denied = _require_superuser(request)
    if denied is not None:
        return denied

    item = get_object_or_404(Feedback, pk=feedback_id)

    if request.method == 'GET':
        return Response(FeedbackSerializer(item).data)

    if request.method == 'DELETE':
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    new_status = request.data.get('status')
    if new_status is not None:
        valid = [choice for choice, _label in Feedback.STATUS_CHOICES]
        if new_status not in valid:
            return Response(
                {'detail': 'status must be one of %s' % (valid,)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        item.status = new_status
    if 'result_notes' in request.data:
        item.result_notes = request.data['result_notes'] or ''
    if 'commit_sha' in request.data:
        item.commit_sha = (request.data['commit_sha'] or '')[:40]
    item.save()
    return Response(FeedbackSerializer(item).data)


# ---------------------------------------------------------------------------
# Diet document parsing (dietitian PDFs / photos)
# ---------------------------------------------------------------------------

# Gemini accepts PDFs as inline_data, so a dietitian's plan can be parsed
# without any file-persistence infrastructure (this project has no MEDIA_ROOT
# and no object storage). The bytes are read, sent, and discarded -- only the
# extracted text is kept.
DIET_DOC_MIME_TYPES = (
    'application/pdf',
    'image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/heic',
    'image/heif',
)
DIET_DOC_MAX_BYTES = 18 * 1024 * 1024   # under the 20MB upload cap


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def diet_document_parse(request):
    """
    Extract a dietitian's instructions from an uploaded PDF or photo.

    Parse-and-discard: the uploaded bytes are never written to disk. The
    returned `summary` is what should be passed back to diet plan generation as
    `dietitian_notes`, and optionally saved to the user's profile notes.
    """
    from .gemini import call_gemini_vision_json, GeminiError as _GeminiError

    upload = request.FILES.get('document') or request.FILES.get('image')
    if not upload:
        return Response({'detail': 'document is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    mime_type = (upload.content_type or '').lower()
    if mime_type not in DIET_DOC_MIME_TYPES:
        return Response(
            {'detail': 'Unsupported file type %r. Upload a PDF or an image.'
                       % (mime_type or 'unknown')},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if upload.size and upload.size > DIET_DOC_MAX_BYTES:
        return Response(
            {'detail': 'File is too large (max %dMB).'
                       % (DIET_DOC_MAX_BYTES // (1024 * 1024))},
            status=status.HTTP_400_BAD_REQUEST,
        )

    document_bytes = upload.read()
    if not document_bytes:
        return Response({'detail': 'Uploaded file is empty.'},
                        status=status.HTTP_400_BAD_REQUEST)

    unit = _user_unit(request.user)

    system = (
        'You are a registered dietitian reading a colleague\'s written plan for a '
        'client. Extract what it actually says. Return JSON only, no markdown.'
    )
    user_prompt = (
        'This document is a diet plan or set of dietary instructions written for '
        'the user by their dietitian. Extract its contents faithfully.\n\n'
        'Rules:\n'
        '- Transcribe what the document says. Do NOT invent targets, foods or '
        'restrictions that are not present.\n'
        '- If a value is not stated, return null for it rather than guessing.\n'
        f'- Express any body weights in {unit}.\n'
        '- `summary` must be a compact instruction block suitable for handing to '
        'a meal-planning model: targets, must-include foods, foods to avoid, '
        'meal timing, and any medical constraints.\n\n'
        'Return JSON matching exactly this schema:\n'
        '{\n'
        '  "is_diet_document": boolean,\n'
        '  "title": "string or null",\n'
        '  "target_calories": number or null,\n'
        '  "protein_g": number or null,\n'
        '  "carbs_g": number or null,\n'
        '  "fat_g": number or null,\n'
        '  "meals_per_day": number or null,\n'
        '  "foods_to_include": ["string"],\n'
        '  "foods_to_avoid": ["string"],\n'
        '  "restrictions": ["string"],\n'
        '  "meal_timing": ["string"],\n'
        '  "notes": ["string"],\n'
        '  "summary": "string"\n'
        '}'
    )

    try:
        parsed = call_gemini_vision_json(
            system_prompt=system,
            user_prompt=user_prompt,
            image_bytes=document_bytes,
            mime_type='application/pdf' if mime_type == 'application/pdf' else mime_type,
        )
    except _GeminiError as e:
        return Response({'detail': str(e)},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    finally:
        # Drop the reference promptly; nothing is persisted.
        document_bytes = None

    if not isinstance(parsed, dict):
        return Response({'detail': 'AI returned unexpected format.'},
                        status=status.HTTP_502_BAD_GATEWAY)

    if parsed.get('is_diet_document') is False:
        return Response(
            {'detail': "That file doesn't look like a diet plan. "
                       'Upload the instructions from your dietitian.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Optionally remember it on the profile so the AI chat has it too.
    if str(request.data.get('save_to_profile', '')).lower() in ('1', 'true', 'yes'):
        summary = (parsed.get('summary') or '').strip()
        if summary:
            try:
                profile = request.user.profile
                existing = (profile.personal_notes or '').strip()
                marker = 'Dietitian plan:'
                # Replace a previous block rather than appending forever.
                if marker in existing:
                    existing = existing.split(marker)[0].strip()
                profile.personal_notes = (
                    (existing + '\n\n' if existing else '') + marker + ' ' + summary
                )[:4000]
                profile.save(update_fields=['personal_notes'])
                parsed['saved_to_profile'] = True
            except Exception:
                logger.exception('diet_document_parse: failed to save to profile')
                parsed['saved_to_profile'] = False

    return Response(parsed)


# ---------------------------------------------------------------------------
# MCP (Model Context Protocol) endpoint
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mcp_endpoint(request):
    """
    JSON-RPC 2.0 endpoint implementing the MCP server methods.

    Authenticates with the same `Authorization: Token <key>` header as the rest
    of the API, so an agent can be pointed at this URL with an existing token.
    Every tool scopes its queries to request.user.

    Supported methods: initialize, notifications/initialized, ping, tools/list,
    tools/call.
    """
    from . import mcp

    body = request.data if isinstance(request.data, dict) else {}
    rpc_id = body.get('id')
    method = body.get('method')

    def ok(result):
        return Response({'jsonrpc': '2.0', 'id': rpc_id, 'result': result})

    def err(code, message):
        return Response({
            'jsonrpc': '2.0', 'id': rpc_id,
            'error': {'code': code, 'message': message},
        })

    if not method:
        return err(mcp.INVALID_REQUEST, 'Missing "method".')

    if method == 'initialize':
        return ok({
            'protocolVersion': mcp.PROTOCOL_VERSION,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': mcp.SERVER_NAME, 'version': mcp.SERVER_VERSION},
        })

    # Notifications carry no id and expect no result.
    if method.startswith('notifications/'):
        return Response({'jsonrpc': '2.0', 'id': rpc_id, 'result': {}})

    if method == 'ping':
        return ok({})

    if method == 'tools/list':
        return ok({'tools': mcp.tool_definitions()})

    if method == 'tools/call':
        params = body.get('params') or {}
        name = params.get('name')
        if not name:
            return err(mcp.INVALID_PARAMS, 'params.name is required.')
        try:
            result = mcp.call_tool(request.user, name, params.get('arguments'))
        except mcp.McpError as exc:
            # An unknown tool name is a protocol-level mistake, so it gets a real
            # JSON-RPC error. Everything else (bad arguments, missing object) is
            # a tool-execution failure and comes back as isError content, which
            # is what the MCP spec expects and what lets an agent recover.
            if exc.code == mcp.METHOD_NOT_FOUND:
                return err(exc.code, exc.message)
            return ok({
                'content': [{'type': 'text', 'text': exc.message}],
                'isError': True,
            })
        except Http404 as exc:
            return ok({
                'content': [{'type': 'text',
                             'text': str(exc) or 'Not found, or not yours.'}],
                'isError': True,
            })
        except Exception as exc:
            logger.exception('mcp tools/call failed: %s', name)
            return ok({
                'content': [{'type': 'text', 'text': 'Tool failed: %s' % exc}],
                'isError': True,
            })

        return ok({
            'content': [{'type': 'text', 'text': json.dumps(result, default=str)}],
            'structuredContent': result,
            'isError': False,
        })

    return err(mcp.METHOD_NOT_FOUND, 'Unknown method %r.' % method)


# ---------------------------------------------------------------------------
# Admin: superuser-issued password resets
# ---------------------------------------------------------------------------
#
# This project has no outbound email configured (RESEND_API_KEY is unset), so
# the normal self-service reset flow cannot deliver its link. Rather than
# dropping the verification step -- a page that sets a new password from just an
# email address would let anyone who knows a user's address take over their
# account -- the operator issues the link and delivers it out of band.
#
# The identity check moves from "can you read this inbox" to "the operator
# vouches for you". Every issued link is logged with who issued it.

# Admin-issued links are delivered by hand (WhatsApp, in person), so the 1-hour
# default on the model is impractical. Still bounded, and single-use.
ADMIN_RESET_DEFAULT_HOURS = 24
ADMIN_RESET_MAX_HOURS = 72


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def admin_users(request):
    """List accounts so the operator can pick one. Superuser only."""
    denied = _require_superuser(request)
    if denied is not None:
        return denied

    users = User.objects.all().order_by('email')
    return Response([{
        'id': str(u.id),
        'email': u.email,
        'name': u.name,
        'is_superuser': u.is_superuser,
        'is_active': u.is_active,
        'last_login': u.last_login.isoformat() if u.last_login else None,
        'created_at': u.created_at.isoformat(),
    } for u in users])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def admin_issue_reset_link(request):
    """
    Mint a password-reset link for another account. Superuser only.

    Returns the URL for the operator to pass to the user directly. The link is
    single-use and expires; using it goes through the existing
    password_reset_confirm endpoint, so the reset page needs no changes.

    Note this deliberately does NOT reveal the user's existing password -- it
    cannot, since passwords are stored only as hashes. It grants a one-time
    opportunity to set a new one.
    """
    denied = _require_superuser(request)
    if denied is not None:
        return denied

    email = (request.data.get('email') or '').strip().lower()
    if not email:
        return Response({'detail': 'email is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        # Safe to be specific: the caller is already a trusted operator who can
        # list every account anyway.
        return Response({'detail': 'No account with that email.'},
                        status=status.HTTP_404_NOT_FOUND)

    # Absent or unparseable -> default. A supplied number is clamped, so an
    # explicit 0 becomes 1 hour rather than silently becoming the default
    # (`0 or DEFAULT` would evaluate to DEFAULT, which is surprising).
    raw_hours = request.data.get('hours')
    if raw_hours in (None, ''):
        hours = ADMIN_RESET_DEFAULT_HOURS
    else:
        try:
            hours = int(raw_hours)
        except (TypeError, ValueError):
            hours = ADMIN_RESET_DEFAULT_HOURS
        hours = max(1, min(hours, ADMIN_RESET_MAX_HOURS))

    from django.utils import timezone as _tz
    from datetime import timedelta as _timedelta

    # Invalidate any outstanding link so only one is ever live per user.
    superseded = PasswordResetToken.objects.filter(
        user=user, used_at__isnull=True, expires_at__gt=_tz.now()
    ).update(used_at=_tz.now())

    token = PasswordResetToken.objects.create(
        user=user, expires_at=_tz.now() + _timedelta(hours=hours))

    frontend_base = (request.data.get('frontend_base') or '').rstrip('/')
    if not frontend_base:
        frontend_base = request.build_absolute_uri('/').rstrip('/')
    reset_url = '%s/reset-password/%s' % (frontend_base, token.token)

    # Audit trail: one operator resetting another person's credentials is
    # exactly the kind of action that should be reconstructable later.
    logger.warning(
        'ADMIN PASSWORD RESET issued by %s for %s (expires %s, superseded %d)',
        request.user.email, user.email, token.expires_at.isoformat(), superseded,
    )

    return Response({
        'email': user.email,
        'name': user.name,
        'reset_url': reset_url,
        'expires_at': token.expires_at.isoformat(),
        'expires_in_hours': hours,
        'superseded_links': superseded,
    }, status=status.HTTP_201_CREATED)
