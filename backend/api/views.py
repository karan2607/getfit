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
        sessions = ChatSession.objects.filter(user=request.user)
        return Response(ChatSessionSerializer(sessions, many=True).data)

    session = ChatSession.objects.create(user=request.user)
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
                              body_context: str = '', preferred_unit: str = 'lb') -> tuple[str, str]:
    system = (
        f'You are an expert personal trainer. Generate a structured workout plan as valid JSON only. '
        f'No markdown, no explanation — just the JSON object. '
        f'Express all weights in {preferred_unit}. '
        f'Include specific starting weight recommendations in each exercise\'s "notes" field based on the user\'s level and body context.'
    )
    goal_map = {'lose_fat': 'fat loss', 'build_muscle': 'muscle gain', 'maintain': 'maintenance'}
    goal = goal_map.get(fitness_goal or getattr(profile, 'fitness_goal', '') or '', 'general fitness')
    level = experience_level or getattr(profile, 'experience_level', 'beginner') or 'beginner'
    equip = equipment or 'full gym'

    user = (
        f'Generate a {duration_weeks}-week workout plan for a {level} with the goal of {goal}. '
        f'Train {days_per_week} days per week. '
        f'Available equipment: {equip}. '
        + (f'Additional notes: {notes}. ' if notes else '')
        + (f'Body context: {body_context}. ' if body_context else '')
        + 'Return JSON matching exactly this schema:\n'
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
    return system, user


def _validate_plan_json(data: dict) -> None:
    required = ['title', 'days']
    for key in required:
        if key not in data:
            raise ValueError(f'Missing required field: {key}')
    if not isinstance(data['days'], list) or len(data['days']) == 0:
        raise ValueError('days must be a non-empty list')
    for day in data['days']:
        for field in ['day_number', 'name', 'is_rest_day']:
            if field not in day:
                raise ValueError(f'Day missing field: {field}')
        if not day.get('is_rest_day', False):
            if not isinstance(day.get('exercises', []), list):
                raise ValueError('exercises must be a list')


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

    try:
        profile = request.user.profile
    except Exception:
        profile = None

    preferred_unit = 'lb'
    try:
        preferred_unit = getattr(profile, 'preferred_unit', 'lb') or 'lb'
    except Exception:
        pass

    system, user_prompt = _workout_generate_prompt(
        profile, days_per_week, duration_weeks,
        fitness_goal=fitness_goal, experience_level=experience_level,
        equipment=equipment, notes=notes, body_context=body_context,
        preferred_unit=preferred_unit,
    )

    try:
        plan_data = call_gemini_json(system_prompt=system, user_prompt=user_prompt)
    except GeminiError as e:
        return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    try:
        _validate_plan_json(plan_data)
    except ValueError as e:
        return Response({'detail': f'AI returned invalid plan: {e}'}, status=status.HTTP_502_BAD_GATEWAY)

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
    )
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

    # Resume if there's an incomplete session for this day
    existing = WorkoutSession.objects.filter(
        user=request.user, exercise_day=day, is_completed=False
    ).first()
    if existing:
        return Response(WorkoutSessionSerializer(existing).data)

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


def _commons_search(query: str) -> list:
    """Search Wikimedia Commons file namespace, return up to 2 JPEG/PNG thumbnail URLs."""
    import urllib.request as _req
    import urllib.parse as _parse
    import json as _json
    try:
        params = {
            'action': 'query', 'generator': 'search',
            'gsrnamespace': '6', 'gsrsearch': query, 'gsrlimit': '10',
            'prop': 'imageinfo', 'iiprop': 'url|mime', 'iiurlwidth': '500',
            'format': 'json', 'formatversion': '2',
        }
        r = _req.Request(
            'https://commons.wikimedia.org/w/api.php?' + _parse.urlencode(params),
            headers={'User-Agent': 'getfit/1.0'},
        )
        with _req.urlopen(r, timeout=8) as resp:
            pages = _json.loads(resp.read()).get('query', {}).get('pages', [])
        urls = []
        for page in pages:
            for ii in page.get('imageinfo', []):
                if ii.get('mime') in ('image/jpeg', 'image/png'):
                    thumb = ii.get('thumburl') or ii.get('url')
                    if thumb:
                        urls.append(thumb)
            if len(urls) >= 2:
                break
        return urls[:2]
    except Exception as exc:
        logger.warning('commons_search failed for %r: %s', query, exc)
        return []


def _fetch_commons_images(name: str) -> list:
    """Return up to 2 exercise image URLs from Wikimedia Commons, trying name variations."""
    import re as _re
    candidates = [name]
    stripped = name
    for pat in [
        r'^(Barbell|Dumbbell|Cable|Machine|EZ[- ]?Bar|Smith[- ]Machine|Kettlebell|Resistance[- ]Band)\s+',
        r'^(Incline|Decline|Flat|Seated|Standing|Lying|Single[- ]Arm|One[- ]Arm|Close[- ]Grip|Wide[- ]Grip|Reverse[- ]Grip)\s+',
    ]:
        s = _re.sub(pat, '', stripped, flags=_re.IGNORECASE).strip()
        if s and s != stripped:
            candidates.append(s)
            stripped = s

    for term in candidates:
        urls = _commons_search(term + ' exercise')
        if urls:
            logger.info('commons images for %r via term %r: %s', name, term, urls)
            return urls

    logger.info('no commons images found for %r (tried: %s)', name, candidates)
    return []


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

    prompt = f"""Return a JSON object for the exercise "{name}" with exactly these fields:
- "steps": array of 4-6 strings, each a clear action step for performing the exercise correctly
- "muscles": array of 2-4 strings, the primary muscles targeted (short names like "Quadriceps", "Glutes")
- "tips": array of 2-3 strings, common mistakes to avoid or form cues
- "category": exactly one of: squat, push, press, pull, row, hinge, curl, lunge, core, cardio, other

Return only valid JSON, no extra text."""

    try:
        guide = call_gemini_json(
            system_prompt='You are a certified personal trainer providing exercise instruction.',
            user_prompt=prompt,
        )
        guide['images'] = _fetch_commons_images(name)
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

    token, _ = Token.objects.get_or_create(user=request.user)
    return Response({'token': token.key})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_status(request):
    try:
        conn = request.user.health_connection
        return Response({
            'connected': True,
            'provider': conn.provider,
            'connected_at': conn.connected_at.isoformat(),
        })
    except HealthConnection.DoesNotExist:
        return Response({'connected': False, 'provider': None, 'connected_at': None})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_summary(request):
    summaries = HealthDailySummary.objects.filter(user=request.user)[:30]
    data = [
        {
            'date': s.date.isoformat(),
            'steps': s.steps,
            'active_calories': s.active_calories,
            'resting_heart_rate': s.resting_heart_rate,
            'sleep_hours': s.sleep_hours,
        }
        for s in summaries
    ]
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health_workouts(request):
    workouts = HealthWorkout.objects.filter(user=request.user)[:20]
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


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def health_shortcuts_sync(request):
    """Receives a health data payload posted from an Apple Shortcut."""
    from django.utils.dateparse import parse_datetime
    from django.utils import timezone as _tz

    data = request.data

    # Mark as connected on first sync
    HealthConnection.objects.update_or_create(
        user=request.user,
        defaults={'provider': 'APPLE'},
    )

    # Upsert today's daily summary
    date_str = data.get('date') or _tz.now().date().isoformat()
    defaults = {}
    def _clean(val):
        return str(val).replace(',', '').strip()

    if data.get('steps') is not None:
        try:
            defaults['steps'] = int(_clean(data['steps']))
        except (TypeError, ValueError):
            pass
    if data.get('active_calories') is not None:
        try:
            defaults['active_calories'] = float(_clean(data['active_calories']))
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
            user=request.user,
            date=date_str,
            defaults=defaults,
        )

    # Upsert workouts
    for w in data.get('workouts', []):
        start_str = w.get('start_time', '')
        start_time = parse_datetime(start_str) if start_str else None
        if not start_time:
            continue
        workout_id = w.get('id') or f"{request.user.id}-{start_str}"
        w_defaults = {
            'user': request.user,
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

    return Response({'status': 'ok'})


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

    calories_out = None
    try:
        # Use most recent summary with active_calories data (within last 2 days)
        from datetime import timedelta
        summary = (
            HealthDailySummary.objects
            .filter(user=request.user, date__gte=today - timedelta(days=1), active_calories__isnull=False)
            .order_by('-date')
            .first()
        )
        if summary:
            calories_out = round(summary.active_calories)
    except Exception:
        pass

    net = (calories_in - calories_out) if calories_out is not None else None

    target = None
    try:
        active_plan = request.user.diet_plans.filter(is_active=True).first()
        if active_plan:
            target = active_plan.target_calories
    except Exception:
        pass

    return Response({
        'calories_in': calories_in,
        'calories_out': calories_out,
        'net': net,
        'target': target,
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
