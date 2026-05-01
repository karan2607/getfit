from django.contrib.auth import get_user_model, authenticate
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
    DietPlan, Meal, FoodScanResult, BodyScanResult,
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
    DietPlanSerializer,
    DietPlanDetailSerializer,
    FoodScanResultSerializer,
    BodyScanResultSerializer,
)
from .email import send_password_reset_email
from .gemini import stream_gemini_chat, GeminiError

User = get_user_model()


@api_view(['GET'])
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

def _build_system_prompt(user) -> str:
    try:
        p = user.profile
        profile_lines = [
            f'- Name: {user.name}',
            f'- Height: {p.height_cm}cm' if p.height_cm else None,
            f'- Weight: {p.weight_kg}kg' if p.weight_kg else None,
            f'- Age: {p.age}' if p.age else None,
            f'- Gender: {p.gender}' if p.gender else None,
            f'- Fitness goal: {p.fitness_goal}' if p.fitness_goal else None,
            f'- Experience level: {p.experience_level}' if p.experience_level else None,
            f'- Dietary preference: {p.dietary_preference}' if p.dietary_preference else None,
            f'- Activity level: {p.activity_level}' if p.activity_level else None,
        ]
        profile_text = '\n'.join(line for line in profile_lines if line)
    except Exception:
        profile_text = f'- Name: {user.name}'

    return (
        'You are a certified AI personal trainer and fitness coach. '
        'Respond conversationally, helpfully, and concisely. '
        'Use markdown for formatting when helpful (e.g. bullet lists for exercises). '
        'Always recommend consulting a doctor for medical concerns.\n\n'
        f'User profile:\n{profile_text}'
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

    try:
        session = ChatSession.objects.get(pk=session_id, user=user)
    except ChatSession.DoesNotExist:
        from django.http import HttpResponse
        return HttpResponse(status=404)

    ChatMessage.objects.create(session=session, role='user', content=content)

    history = [
        {'role': msg.role if msg.role == 'user' else 'model', 'parts': [{'text': msg.content}]}
        for msg in session.messages.order_by('created_at')[-20:]
    ]

    system_prompt = _build_system_prompt(user)

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

def _workout_generate_prompt(profile, days_per_week: int, duration_weeks: int) -> tuple[str, str]:
    system = (
        'You are an expert personal trainer. Generate a structured workout plan as valid JSON only. '
        'No markdown, no explanation — just the JSON object.'
    )
    goal_map = {'lose_fat': 'fat loss', 'build_muscle': 'muscle gain', 'maintain': 'maintenance'}
    goal = goal_map.get(getattr(profile, 'fitness_goal', '') or '', 'general fitness')
    level = getattr(profile, 'experience_level', 'beginner') or 'beginner'

    user = (
        f'Generate a {duration_weeks}-week workout plan for a {level} with the goal of {goal}. '
        f'Train {days_per_week} days per week. '
        'Return JSON matching exactly this schema:\n'
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
    from .gemini import call_gemini_json, GeminiError as _GeminiError
    days_per_week = int(request.data.get('days_per_week', 4))
    duration_weeks = int(request.data.get('duration_weeks', 8))
    days_per_week = max(1, min(days_per_week, 7))
    duration_weeks = max(1, min(duration_weeks, 16))

    try:
        profile = request.user.profile
    except Exception:
        profile = None

    system, user_prompt = _workout_generate_prompt(profile, days_per_week, duration_weeks)

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


@api_view(['GET', 'PATCH', 'DELETE'])
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

    plan.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def workout_plan_activate(request, plan_id):
    plan = get_object_or_404(WorkoutPlan, pk=plan_id, user=request.user)
    WorkoutPlan.objects.filter(user=request.user, is_active=True).update(is_active=False)
    plan.is_active = True
    plan.save(update_fields=['is_active'])
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

    system = (
        'You are a certified nutritionist. Generate a structured daily diet plan as valid JSON only. '
        'No markdown, no explanation — just the JSON object.'
    )
    user_prompt = (
        f'Generate a daily diet plan with target {target_calories} kcal, '
        f'{protein_g}g protein, {carbs_g}g carbs, {fat_g}g fat. '
        f'Dietary preference: {diet_pref}. Fitness goal: {goal}. '
        'Include 4-5 meals (breakfast, lunch, dinner, snacks). '
        'Return JSON matching exactly this schema:\n'
        '{\n'
        '  "title": "string",\n'
        '  "description": "string",\n'
        '  "meals": [\n'
        '    {\n'
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
