from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import (
    UserProfile, ChatSession, ChatMessage,
    WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
    MealLog, DietPlan, Meal, FoodScanResult, BodyScanResult,
    Feedback,
)

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(required=True, max_length=255)
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value.lower()

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password'],
            name=validated_data['name'],
        )
        UserProfile.objects.create(user=user)
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    # Which planning inputs are unset, so the client can PROMPT for them
    # instead of silently generating a plan against a blank client. Existing
    # users predate the onboarding requirement, so locking them out would be
    # the wrong trade -- they get asked, not blocked.
    missing_for_planning = serializers.ListField(read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            'height_cm', 'weight_kg', 'age', 'gender',
            'fitness_goal', 'experience_level', 'dietary_preference', 'activity_level',
            'personal_notes', 'preferred_unit', 'timezone',
            'missing_for_planning',
        ]
        read_only_fields = ['missing_for_planning']

    def validate_timezone(self, value):
        """
        Accept only a resolvable IANA name, so a junk value cannot silently
        shift every date the user sees.
        """
        from .user_time import DEFAULT_TIMEZONE, resolve_zone
        name = (value or '').strip()
        if not name:
            return ''
        if str(resolve_zone(name)) == 'UTC' and name.upper() != DEFAULT_TIMEZONE:
            raise serializers.ValidationError('Unknown timezone %r' % name)
        return name


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)

    class Meta:
        model = User
        # is_superuser is exposed read-only so the client can hide
        # superuser-only affordances. It is NOT the access control -- the
        # endpoints enforce it server-side.
        fields = ['id', 'name', 'email', 'created_at', 'profile', 'is_superuser']
        read_only_fields = ['id', 'created_at', 'is_superuser']


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)

    def validate_password(self, value):
        validate_password(value)
        return value


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class UpdateMeSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=255)
    email = serializers.EmailField(required=False)


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = ['id', 'role', 'content', 'created_at']
        read_only_fields = ['id', 'created_at']


class ChatSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = ['id', 'title', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class ChatSessionDetailSerializer(serializers.ModelSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta:
        model = ChatSession
        fields = ['id', 'title', 'messages', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Workout
# ---------------------------------------------------------------------------

class ExerciseSerializer(serializers.ModelSerializer):
    """
    A plan exercise.

    `composite_id` and `composite_rest_seconds` replaced `superset_group`.
    The client needs exactly two things to render a group -- an identifier
    shared by its members, and the rest that follows a complete round -- and a
    bare integer could only ever supply the first. The card previously read the
    LAST member's `rest_seconds` and hoped; now the group's own value is sent.

    Flat rather than a nested object on purpose: the members are already
    siblings in the `exercises` list, so nesting the container would repeat it
    once per member.
    """

    composite_id = serializers.UUIDField(read_only=True, allow_null=True)
    composite_rest_seconds = serializers.IntegerField(
        source='composite.rest_seconds', read_only=True, allow_null=True,
        default=None)

    class Meta:
        model = Exercise
        fields = ['id', 'name', 'sets', 'reps', 'rest_seconds', 'notes',
                  'order', 'measurement_type',
                  'composite_id', 'composite_rest_seconds']
        read_only_fields = ['id']


class WorkoutDaySerializer(serializers.ModelSerializer):
    exercises = ExerciseSerializer(many=True, read_only=True)

    class Meta:
        model = WorkoutDay
        fields = ['id', 'day_number', 'week_number', 'name', 'focus', 'is_rest_day', 'order', 'exercises', 'last_completed_at']
        read_only_fields = ['id']


class WorkoutPlanSerializer(serializers.ModelSerializer):
    # Per-week generation state. `weeks_ready` is derived from the days that
    # actually exist, so it cannot drift -- and plans created before per-week
    # generation report their full duration and need no special-casing.
    weeks_ready = serializers.IntegerField(read_only=True)
    is_preparing = serializers.BooleanField(read_only=True)

    class Meta:
        model = WorkoutPlan
        fields = ['id', 'title', 'description', 'is_active', 'activated_at', 'generated_by_ai', 'duration_weeks', 'current_week', 'current_day_order', 'specific_goal', 'program_target', 'goal_check_in_shown', 'data_version', 'created_at', 'updated_at', 'program_outline', 'weeks_ready', 'preparing_week', 'is_preparing', 'prepare_error', 'equipment']
        read_only_fields = ['id', 'generated_by_ai', 'created_at', 'updated_at', 'weeks_ready', 'preparing_week', 'is_preparing', 'prepare_error']


class WorkoutPlanDetailSerializer(serializers.ModelSerializer):
    days = WorkoutDaySerializer(many=True, read_only=True)
    weeks_ready = serializers.IntegerField(read_only=True)
    is_preparing = serializers.BooleanField(read_only=True)

    class Meta:
        model = WorkoutPlan
        fields = ['id', 'title', 'description', 'is_active', 'activated_at', 'generated_by_ai', 'duration_weeks', 'current_week', 'current_day_order', 'specific_goal', 'program_target', 'goal_check_in_shown', 'data_version', 'days', 'created_at', 'updated_at', 'program_outline', 'weeks_ready', 'preparing_week', 'is_preparing', 'prepare_error', 'equipment']
        read_only_fields = ['id', 'generated_by_ai', 'created_at', 'updated_at', 'weeks_ready', 'preparing_week', 'is_preparing', 'prepare_error']


class SetLogSerializer(serializers.ModelSerializer):
    exercise_id = serializers.UUIDField(source='exercise.id', read_only=True, default=None)
    # The client needs the type to know which of the three value fields to render.
    # Falls back to the parent Exercise; None when the Exercise was SET_NULL'd.
    measurement_type = serializers.CharField(
        source='exercise.measurement_type', read_only=True, default=None)

    class Meta:
        model = SetLog
        fields = [
            'id', 'exercise_id', 'exercise_name', 'set_number',
            'reps_completed', 'weight_kg', 'duration_seconds',
            'speed_kmh', 'incline_pct', 'distance_m',
            'measurement_type', 'is_completed', 'notes',
        ]
        read_only_fields = ['id', 'exercise_id', 'measurement_type']


class WorkoutSessionSerializer(serializers.ModelSerializer):
    exercise_day = WorkoutDaySerializer(read_only=True)
    set_logs = SetLogSerializer(many=True, read_only=True)
    plan_id = serializers.SerializerMethodField()
    day_name = serializers.SerializerMethodField()

    def get_plan_id(self, obj):
        return str(obj.exercise_day.plan_id) if obj.exercise_day_id else None

    def get_day_name(self, obj):
        if obj.exercise_day:
            return obj.exercise_day.name
        return obj.day_name or ''

    class Meta:
        model = WorkoutSession
        fields = ['id', 'plan_id', 'day_name', 'exercise_day', 'started_at', 'completed_at', 'notes', 'is_completed', 'set_logs']
        read_only_fields = ['id', 'started_at']


class WorkoutSessionListSerializer(serializers.ModelSerializer):
    day_name = serializers.CharField(source='exercise_day.name', read_only=True, default='')
    exercise_day_id = serializers.UUIDField(source='exercise_day.id', read_only=True, default=None)

    class Meta:
        model = WorkoutSession
        fields = ['id', 'day_name', 'exercise_day_id', 'started_at', 'completed_at', 'is_completed']
        read_only_fields = ['id', 'started_at']


# ---------------------------------------------------------------------------
# Meal Log
# ---------------------------------------------------------------------------

class MealLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = MealLog
        fields = ['id', 'date', 'meal_type', 'food_name', 'calories', 'protein_g', 'carbs_g', 'fat_g', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']


# ---------------------------------------------------------------------------
# Diet
# ---------------------------------------------------------------------------

class MealSerializer(serializers.ModelSerializer):
    class Meta:
        model = Meal
        fields = ['id', 'day_number', 'meal_type', 'name', 'description', 'calories', 'protein_g', 'carbs_g', 'fat_g', 'order']
        read_only_fields = ['id']


class DietPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = DietPlan
        fields = ['id', 'title', 'description', 'target_calories', 'protein_g', 'carbs_g', 'fat_g',
                  'is_active', 'generated_by_ai', 'created_at', 'updated_at']
        read_only_fields = ['id', 'generated_by_ai', 'created_at', 'updated_at']


class DietPlanDetailSerializer(serializers.ModelSerializer):
    meals = MealSerializer(many=True, read_only=True)

    class Meta:
        model = DietPlan
        fields = ['id', 'title', 'description', 'target_calories', 'protein_g', 'carbs_g', 'fat_g',
                  'is_active', 'generated_by_ai', 'meals', 'created_at', 'updated_at']
        read_only_fields = ['id', 'generated_by_ai', 'created_at', 'updated_at']


class FoodScanResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = FoodScanResult
        fields = ['id', 'food_name', 'serving_size', 'calories', 'protein_g', 'carbs_g', 'fat_g', 'fiber_g', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']


# ---------------------------------------------------------------------------
# Body Scanner
# ---------------------------------------------------------------------------

class BodyScanResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = BodyScanResult
        fields = ['id', 'body_fat_pct', 'physique_category', 'muscle_mass_note', 'posture_notes',
                  'recommendations', 'disclaimer', 'created_at']
        read_only_fields = ['id', 'created_at']


class FeedbackSerializer(serializers.ModelSerializer):
    submitted_by = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = Feedback
        fields = [
            'id', 'body', 'page', 'status', 'result_notes', 'commit_sha',
            'submitted_by', 'created_at', 'updated_at',
        ]
        # The client may only ever set body/page. status, result_notes and
        # commit_sha are written by the automation, never by the submitter.
        read_only_fields = [
            'id', 'status', 'result_notes', 'commit_sha', 'submitted_by',
            'created_at', 'updated_at',
        ]

    def validate_body(self, value):
        text = (value or '').strip()
        if not text:
            raise serializers.ValidationError('Feedback cannot be empty.')
        if len(text) > 5000:
            raise serializers.ValidationError('Feedback is too long (max 5000).')
        return text
