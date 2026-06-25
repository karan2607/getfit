import logging
from django.contrib.auth import get_user_model, authenticate

logger = logging.getLogger(__name__)
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
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
)
from .email import send_password_reset_email
from .gemini import stream_gemini_chat, call_gemini_json, GeminiError

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
        '  "days": [\n'
        '    {\n'
        '      "day_number": number,\n'
        '      "name": "string",\n'
        '      "focus": "string",\n'
        '      "is_rest_day": boolean,\n'
        '      "exercises": [\n'
        '        {"name": "string", "sets": number, "reps": "string", "rest_seconds": number, "notes": "string"}\n'
        '      ]\n'
        '    }\n'
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
            days_data = []
            for day in plan_obj.days.order_by('day_number').prefetch_related('exercises'):
                exercises_data = [
                    {'name': ex.name, 'sets': ex.sets, 'reps': ex.reps,
                     'rest_seconds': ex.rest_seconds, 'notes': ex.notes or ''}
                    for ex in day.exercises.all()
                ]
                days_data.append({
                    'day_number': day.day_number,
                    'name': day.name,
                    'focus': day.focus or '',
                    'is_rest_day': day.is_rest_day,
                    'exercises': exercises_data,
                })
            plan_json = _json2.dumps({
                'title': plan_obj.title,
                'description': plan_obj.description or '',
                'duration_weeks': plan_obj.duration_weeks,
                'days': days_data,
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
                'output the COMPLETE updated plan as a "workout-plan" code block (not just the changes). '
                'Preserve the same structure, duration_weeks, and number of days unless the user asks to change them.'
                f'{day_focus_text}'
            )
        except Exception:
            pass
    elif plan_context:
        plan_text = (
            f'\n\nCURRENT WORKOUT PLAN PREVIEW (not yet saved — the user is reviewing this generated plan):\n'
            f'```json\n{plan_context}\n```\n'
            'The user wants to refine this plan before saving it. '
            'When they describe changes, output the COMPLETE updated plan as a "workout-plan" code block. '
            'Preserve the same structure, duration_weeks, and number of days unless the user asks to change them.'
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

        'EXERCISE SELECTION (priority order):\n'
        '1. Primary compound: squat/hinge/horizontal push/vertical pull/loaded carry\n'
        '2. Secondary compound: lunge, dip, cable row, incline/decline variation\n'
        '3. Isolation accessory: curl, extension, lateral raise — supplementary only\n\n'

        'PAST EXERCISE CONTEXT:\n'
        'Client\'s previous program exercises are provided for reference. '
        'Repeat them if they are the optimal choice. Vary them if the phase calls for a different stimulus. '
        'Never avoid an exercise just because it appeared before — effectiveness over novelty.\n\n'

        'NOTES FIELD (mandatory for every exercise):\n'
        f'Format: "Start [weight]{preferred_unit}. Add [increment] when all sets completed with good form."\n'
        f'Example: "Start 135{preferred_unit}. Add 5{preferred_unit} when all sets are clean."'
    )

    # Build the structured user prompt
    age = getattr(profile, 'age', None) if profile else None
    gender = getattr(profile, 'gender', None) if profile else None
    weight = getattr(profile, 'weight_kg', None) if profile else None
    height = getattr(profile, 'height_cm', None) if profile else None
    personal_notes = getattr(profile, 'personal_notes', '') if profile else ''

    profile_line = f'Client: {age}yo {gender}, {weight}{preferred_unit}, {height}cm' if all([age, gender, weight, height]) else ''

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

    user = '\n'.join(filter(None, [
        profile_line,
        f'Personal notes/limitations: {personal_notes}' if personal_notes else '',
        f'Primary goal: {goal} | Experience: {level} | Equipment: {equip}',
        specific_goals_line,
        target_line,
        f'Days/week: {days_per_week} | Duration: {duration_weeks} weeks',
        f'Extra notes: {notes}' if notes else '',
        f'Body context: {body_context}' if body_context else '',
        past_line,
        '',
        f'Design the optimal {duration_weeks}-week plan for this specific person using your full trainer expertise.',
        f'Structure into {phases}. Consider ALL stated goals — blend programming for hybrid goals.',
        'Within each phase increase overload week-to-week (sets or reduced rest before adding load).',
        f'Return ALL {duration_weeks} weeks in the weeks array.',
        'Schema:',
        '{',
        '  "title": "string",',
        '  "description": "string",',
        '  "duration_weeks": number,',
        '  "weeks": [',
        '    [',
        '      {',
        '        "day_number": number,',
        '        "name": "string",',
        '        "focus": "string",',
        '        "is_rest_day": boolean,',
        '        "exercises": [',
        '          {"name": "string", "sets": number, "reps": "string", "rest_seconds": number, "notes": "string"}',
        '        ]',
        '      }',
        '    ]',
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
        for field in ['day_number', 'name', 'is_rest_day']:
            if field not in day:
                raise ValueError(f'Day missing field: {field}')
        if not day.get('is_rest_day', False):
            if not isinstance(day.get('exercises', []), list):
                raise ValueError('exercises must be a list')


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
    weight = getattr(profile, 'weight_kg', None) if profile else None
    height = getattr(profile, 'height_cm', None) if profile else None

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

    user_parts = [
        f'Client profile: {age}yo {gender}, current weight {weight}{preferred_unit}, height {height}cm' if all([age, gender, weight, height]) else 'Client profile: not provided',
        f'Primary goal: {primary} | Experience: {experience_level or "unknown"}',
        f'Duration: {duration_weeks} weeks, {days_per_week} days/week',
        f'Specific goals: {", ".join(goal_strs)}' if goal_strs else 'No specific secondary goals',
        f'Notes / limitations: {notes}' if notes else '',
        f'Body context: {body_context}' if body_context else '',
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

    system, user_prompt = _workout_generate_prompt(
        profile, days_per_week, duration_weeks,
        fitness_goal=fitness_goal, experience_level=experience_level,
        equipment=equipment, notes=notes, body_context=body_context,
        preferred_unit=preferred_unit,
        specific_goals=specific_goals, goal_params=goal_params,
        confirmed_target=confirmed_target, past_exercises=past_exercises,
    )

    try:
        plan_data = call_gemini_json(system_prompt=system, user_prompt=user_prompt)
    except GeminiError as e:
        return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    try:
        _validate_plan_json(plan_data)
    except ValueError as e:
        return Response({'detail': f'AI returned invalid plan: {e}'}, status=status.HTTP_502_BAD_GATEWAY)

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
                    Exercise.objects.create(
                        day=day,
                        name=ex_data['name'],
                        sets=ex_data.get('sets', 3),
                        reps=str(ex_data.get('reps', '10')),
                        rest_seconds=ex_data.get('rest_seconds'),
                        notes=ex_data.get('notes', ''),
                        order=j,
                    )
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
                Exercise.objects.create(
                    day=day,
                    name=ex_data['name'],
                    sets=ex_data.get('sets', 3),
                    reps=str(ex_data.get('reps', '10')),
                    rest_seconds=ex_data.get('rest_seconds'),
                    notes=ex_data.get('notes', ''),
                    order=j,
                )

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

        plan.title = data['title']
        plan.description = data.get('description', '')
        plan.duration_weeks = data.get('duration_weeks', plan.duration_weeks)
        plan.save()

        # Replace all days and exercises
        plan.days.all().delete()
        for i, day_data in enumerate(data['days']):
            day = WorkoutDay.objects.create(
                plan=plan,
                day_number=day_data['day_number'],
                name=day_data['name'],
                focus=day_data.get('focus', ''),
                is_rest_day=day_data.get('is_rest_day', False),
                order=i,
            )
            for j, ex_data in enumerate(day_data.get('exercises', [])):
                Exercise.objects.create(
                    day=day,
                    name=ex_data['name'],
                    sets=ex_data.get('sets', 3),
                    reps=str(ex_data.get('reps', '10')),
                    rest_seconds=ex_data.get('rest_seconds'),
                    notes=ex_data.get('notes', ''),
                    order=j,
                )
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
    plan.save(update_fields=['current_week'])
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
        ).exclude(exercise_day=day).select_related('exercise_day').first()
        if other:
            day_name = other.exercise_day.name if other.exercise_day else 'another day'
            data = WorkoutSessionSerializer(other).data
            data['already_active'] = True
            data['conflict_day_name'] = day_name
            return Response(data, status=status.HTTP_200_OK)

    session = WorkoutSession.objects.create(user=request.user, exercise_day=day)

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
    if 'reps_completed' in request.data:
        log.reps_completed = request.data['reps_completed']
    if 'weight_kg' in request.data:
        log.weight_kg = request.data['weight_kg']
    if 'is_completed' in request.data:
        log.is_completed = request.data['is_completed']
    if 'notes' in request.data:
        log.notes = request.data['notes']
    log.save()
    return Response(SetLogSerializer(log).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_plan_prepare_week(request, plan_id):
    """
    Bulk-generate ExerciseGuide entries for all exercises in the plan's current week.
    Also includes weight recommendations based on user's recent history.
    """
    from .gemini import call_gemini_json, GeminiError as _GeminiError
    plan = get_object_or_404(WorkoutPlan, pk=plan_id, user=request.user)
    current_week = plan.current_week

    # Collect unique exercise names for this week; fall back to week 1 for legacy single-week plans
    exercise_names = list(
        Exercise.objects
        .filter(day__plan=plan, day__week_number=current_week)
        .values_list('name', flat=True)
        .distinct()
    )
    if not exercise_names:
        exercise_names = list(
            Exercise.objects
            .filter(day__plan=plan)
            .values_list('name', flat=True)
            .distinct()
        )
    if not exercise_names:
        return Response({'detail': 'No exercises found for this week', 'generated': 0})

    # Build history context for weight recommendations
    history_ctx = {}
    for ex_name in exercise_names:
        logs = (
            SetLog.objects
            .filter(
                workout_session__user=request.user,
                exercise_name__iexact=ex_name,
                is_completed=True,
                weight_kg__isnull=False,
            )
            .order_by('-workout_session__started_at')
            .values('workout_session__started_at', 'set_number', 'weight_kg', 'reps_completed')[:9]
        )
        if logs:
            history_ctx[ex_name] = list(logs)

    kb = _load_exercise_kb()

    prompt_lines = ['Return a JSON array. Each element corresponds to one exercise in the list below.']
    prompt_lines.append('For each exercise, return an object with:')
    prompt_lines.append('  steps: array of 4-6 clear action strings')
    prompt_lines.append('  muscles: array of 2-4 primary muscle names')
    prompt_lines.append('  tips: array of 2-3 form cues or common mistakes to avoid')
    prompt_lines.append('  category: one of squat/push/press/pull/row/hinge/curl/lunge/core/cardio/other')
    prompt_lines.append('  kb_key: best matching key from the image library, or null')
    prompt_lines.append('  recommended_weight: a short string like "Start with 40kg (3x8)" or null if no history')
    prompt_lines.append('')
    prompt_lines.append('Exercises:')
    for i, name in enumerate(exercise_names, 1):
        hist = history_ctx.get(name)
        hist_str = ''
        if hist:
            sets_summary = '; '.join(
                f"{s['workout_session__started_at'][:10]}: {s['weight_kg']}kg x {s['reps_completed'] or '?'}"
                for s in hist[:3]
            )
            hist_str = f' [Recent: {sets_summary}]'
        candidates = _get_kb_candidates(name, top_n=10)
        cands_str = ', '.join(candidates[:5]) if candidates else 'none'
        prompt_lines.append(f'{i}. {name}{hist_str} | Image library candidates: {cands_str}')

    prompt = '\n'.join(prompt_lines)

    try:
        guides_list = call_gemini_json(
            system_prompt='You are a certified personal trainer providing exercise instructions and weight recommendations.',
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
        kb_key = guide.pop('kb_key', None)
        if kb_key and kb_key in kb:
            guide['images'] = kb[kb_key][:2]
        else:
            guide['images'] = _lookup_exercise_images(name)
        try:
            eg = ExerciseGuide.objects.get(name__iexact=name)
            eg.data = guide
            eg.save(update_fields=['data'])
        except ExerciseGuide.DoesNotExist:
            ExerciseGuide.objects.create(name=name, data=guide)
        generated += 1

    return Response({'generated': generated, 'exercises': exercise_names})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def workout_exercises_list(request):
    """All exercises the user has ever logged, with summary stats."""
    from django.db.models import Max, Count
    rows = (
        SetLog.objects
        .filter(workout_session__user=request.user, is_completed=True)
        .values('exercise_name')
        .annotate(
            last_session=Max('workout_session__started_at'),
            total_sessions=Count('workout_session', distinct=True),
            last_weight_kg=Max('weight_kg'),
        )
        .order_by('-last_session')
    )
    return Response(list(rows))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def workout_exercise_history(request, exercise_name):
    logs = (
        SetLog.objects
        .filter(
            workout_session__user=request.user,
            exercise_name__iexact=exercise_name,
            weight_kg__isnull=False,
            is_completed=True,
        )
        .order_by('workout_session__started_at')
        .values('workout_session__started_at', 'set_number', 'weight_kg', 'reps_completed')
    )
    return Response(list(logs))


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


def _lookup_exercise_images(name: str) -> list:
    import re as _re
    kb = _load_exercise_kb()

    _stop = {'the', 'a', 'an', 'with', 'on', 'in', 'of', 'to', 'and', 'or', 'for', 'from', 'at', 'by'}

    def _stem(w):
        for suffix in ('ing', 'ed', 'es', 's'):
            if w.endswith(suffix) and len(w) > len(suffix) + 2:
                return w[:-len(suffix)]
        return w

    def _tokenize(s):
        words = _re.sub(r'[^\w\s]', ' ', s.lower()).split()
        return {_stem(w) for w in words if w not in _stop}

    query_words = _tokenize(name)
    if not query_words:
        return []

    best_key, best_score = None, -1
    for key, urls in kb.items():
        key_words = _tokenize(key)
        overlap = len(query_words & key_words)
        if overlap == 0:
            continue
        recall = overlap / len(query_words)
        precision = overlap / len(key_words)
        f1 = 2 * recall * precision / (recall + precision)
        if f1 > best_score:
            best_score = f1
            best_key = key

    if best_key and best_score >= 0.5:
        logger.info('exercise_kb hit for %r via key %r (f1=%.2f)', name, best_key, best_score)
        return kb[best_key][:2]

    logger.info('exercise_kb miss for %r (best f1=%.2f)', name, best_score)
    return []


def _get_kb_candidates(name: str, top_n: int = 20) -> list:
    """Return the top N KB keys by F1 score for Gemini to choose from."""
    import re as _re
    kb = _load_exercise_kb()

    _stop = {'the', 'a', 'an', 'with', 'on', 'in', 'of', 'to', 'and', 'or', 'for', 'from', 'at', 'by'}

    def _stem(w):
        for suffix in ('ing', 'ed', 'es', 's'):
            if w.endswith(suffix) and len(w) > len(suffix) + 2:
                return w[:-len(suffix)]
        return w

    def _tokenize(s):
        words = _re.sub(r'[^\w\s]', ' ', s.lower()).split()
        return {_stem(w) for w in words if w not in _stop}

    query_words = _tokenize(name)
    if not query_words:
        return []

    scored = []
    for key in kb:
        key_words = _tokenize(key)
        overlap = len(query_words & key_words)
        if overlap == 0:
            continue
        recall = overlap / len(query_words)
        precision = overlap / len(key_words)
        f1 = 2 * recall * precision / (recall + precision)
        scored.append((f1, key))

    scored.sort(reverse=True)
    return [key for _, key in scored[:top_n]]


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def exercise_guide(request):
    name = request.GET.get('name', '').strip()
    if not name:
        return Response({'error': 'name is required'}, status=status.HTTP_400_BAD_REQUEST)

    # DB cache — survives server restarts
    try:
        cached = ExerciseGuide.objects.get(name__iexact=name)
        logger.info('exercise_guide: DB hit for %r', name)
        return Response(cached.data)
    except ExerciseGuide.DoesNotExist:
        pass

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
        kb = _load_exercise_kb()
        if kb_key and kb_key in kb:
            guide['images'] = kb[kb_key][:2]
            logger.info('exercise_kb gemini pick for %r: %r', name, kb_key)
        else:
            guide['images'] = _lookup_exercise_images(name)
        ExerciseGuide.objects.create(name=name, data=guide)
        return Response(guide)
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

    system = (
        'You are a certified nutritionist. Generate a structured multi-day diet plan as valid JSON only. '
        'No markdown, no explanation — just the JSON object.'
    )
    user_prompt = (
        f'Generate a {duration_days}-day diet plan with daily targets of {target_calories} kcal, '
        f'{protein_g}g protein, {carbs_g}g carbs, {fat_g}g fat. '
        f'Dietary preference: {diet_pref}. Fitness goal: {goal}.{cultural_text}\n'
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

    conn, _ = HealthConnection.objects.get_or_create(user=request.user, defaults={'provider': 'APPLE'})
    if not conn.sync_token:
        import hashlib
        conn.sync_token = hashlib.md5(f"{conn.user_id}getFit".encode()).hexdigest()
        conn.save(update_fields=['sync_token'])
    return Response({'token': conn.sync_token})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_status(request):
    try:
        conn = request.user.health_connection
        has_data = (
            HealthDailySummary.objects.filter(user=request.user).exists() or
            HealthWorkout.objects.filter(user=request.user).exists()
        )
        latest = HealthDailySummary.objects.filter(user=request.user).order_by('-date').first()
        return Response({
            'connected': has_data,
            'provider': conn.provider if has_data else None,
            'connected_at': conn.connected_at.isoformat() if has_data else None,
            'last_sync_at': conn.last_sync_at.isoformat() if conn.last_sync_at else None,
            'latest_summary_date': latest.date.isoformat() if latest else None,
        })
    except HealthConnection.DoesNotExist:
        return Response({'connected': False, 'provider': None, 'connected_at': None})


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


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def health_shortcuts_sync(request):
    """GET: validates the sync token — Shortcut calls this first to confirm connection.
    POST: receives the health data payload.
    Both authenticated via permanent sync token (Authorization: Sync <token>)."""
    from django.utils.dateparse import parse_datetime
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

    import json as _json, re as _re
    try:
        raw = request.body.decode('utf-8')
        # Shortcuts sends empty string when a HealthKit query has no results,
        # producing malformed JSON like  "steps": ,  — replace with null.
        raw = _re.sub(r':\s*,', ': null,', raw)
        raw = _re.sub(r':\s*\}', ': null}', raw)
        data = _json.loads(raw)
    except Exception:
        data = {}

    # Upsert today's daily summary
    date_str = data.get('date') or _tz.now().date().isoformat()
    defaults = {}
    def _clean(val):
        return str(val).replace(',', '').strip()

    if data.get('steps') is not None:
        try:
            defaults['steps'] = int(float(_clean(data['steps'])))
        except (TypeError, ValueError):
            pass
    if data.get('active_calories') is not None:
        try:
            defaults['active_calories'] = float(_clean(data['active_calories']))
        except (TypeError, ValueError):
            pass
    if data.get('resting_calories') is not None:
        try:
            defaults['resting_calories'] = float(_clean(data['resting_calories']))
        except (TypeError, ValueError):
            pass
    if data.get('resting_heart_rate') is not None:
        try:
            defaults['resting_heart_rate'] = float(_clean(data['resting_heart_rate']))
        except (TypeError, ValueError):
            pass
    if data.get('sleep_hours') is not None:
        try:
            defaults['sleep_hours'] = float(_clean(data['sleep_hours']))
        except (TypeError, ValueError):
            pass
    if defaults:
        HealthDailySummary.objects.update_or_create(
            user=sync_user,
            date=date_str,
            defaults=defaults,
        )

    # Upsert workouts
    for w in data.get('workouts', []):
        start_str = w.get('start_time', '')
        start_time = parse_datetime(start_str) if start_str else None
        if not start_time:
            continue
        workout_id = w.get('id') or f"{sync_user.id}-{start_str}"
        w_defaults = {
            'user': sync_user,
            'activity_type': str(w.get('activity_type') or 'Workout'),
            'start_time': start_time,
        }
        for field in ('duration_seconds', 'calories', 'avg_heart_rate', 'distance_meters'):
            if w.get(field) is not None:
                try:
                    w_defaults[field] = float(w[field])
                except (TypeError, ValueError):
                    pass
        HealthWorkout.objects.update_or_create(
            terra_workout_id=str(workout_id),
            defaults=w_defaults,
        )

    conn.last_sync_at = _tz.now()
    conn.save(update_fields=['last_sync_at'])

    summary_count = HealthDailySummary.objects.filter(user=sync_user).count()
    return Response({'status': 'ok', 'summaries_stored': summary_count})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_recovery(request):
    from django.utils import timezone as _tz
    today = _tz.now().date()
    rows = list(
        HealthDailySummary.objects
        .filter(user=request.user, resting_heart_rate__isnull=False)
        .order_by('-date')[:7]
    )
    today_rhr = None
    baseline_rows = []
    for r in rows:
        if r.date == today:
            today_rhr = r.resting_heart_rate
        else:
            baseline_rows.append(r.resting_heart_rate)

    if today_rhr is None or not baseline_rows:
        return Response({'score': None, 'label': 'No data yet', 'today_rhr': today_rhr, 'baseline_rhr': None})

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
