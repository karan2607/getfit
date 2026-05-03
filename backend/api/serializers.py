from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import (
    UserProfile, ChatSession, ChatMessage,
    WorkoutPlan, WorkoutDay, Exercise, WorkoutSession, SetLog,
    MealLog, DietPlan, Meal, FoodScanResult, BodyScanResult,
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
    class Meta:
        model = UserProfile
        fields = [
            'height_cm', 'weight_kg', 'age', 'gender',
            'fitness_goal', 'experience_level', 'dietary_preference', 'activity_level',
            'personal_notes', 'preferred_unit',
        ]


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'name', 'email', 'created_at', 'profile']
        read_only_fields = ['id', 'created_at']


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
    class Meta:
        model = Exercise
        fields = ['id', 'name', 'sets', 'reps', 'rest_seconds', 'notes', 'order']
        read_only_fields = ['id']


class WorkoutDaySerializer(serializers.ModelSerializer):
    exercises = ExerciseSerializer(many=True, read_only=True)

    class Meta:
        model = WorkoutDay
        fields = ['id', 'day_number', 'name', 'focus', 'is_rest_day', 'order', 'exercises']
        read_only_fields = ['id']


class WorkoutPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkoutPlan
        fields = ['id', 'title', 'description', 'is_active', 'activated_at', 'generated_by_ai', 'duration_weeks', 'created_at', 'updated_at']
        read_only_fields = ['id', 'generated_by_ai', 'created_at', 'updated_at']


class WorkoutPlanDetailSerializer(serializers.ModelSerializer):
    days = WorkoutDaySerializer(many=True, read_only=True)

    class Meta:
        model = WorkoutPlan
        fields = ['id', 'title', 'description', 'is_active', 'activated_at', 'generated_by_ai', 'duration_weeks', 'days', 'created_at', 'updated_at']
        read_only_fields = ['id', 'generated_by_ai', 'created_at', 'updated_at']


class SetLogSerializer(serializers.ModelSerializer):
    exercise_id = serializers.UUIDField(source='exercise.id', read_only=True, default=None)

    class Meta:
        model = SetLog
        fields = ['id', 'exercise_id', 'exercise_name', 'set_number', 'reps_completed', 'weight_kg', 'is_completed', 'notes']
        read_only_fields = ['id', 'exercise_id']


class WorkoutSessionSerializer(serializers.ModelSerializer):
    exercise_day = WorkoutDaySerializer(read_only=True)
    set_logs = SetLogSerializer(many=True, read_only=True)

    class Meta:
        model = WorkoutSession
        fields = ['id', 'exercise_day', 'started_at', 'completed_at', 'notes', 'is_completed', 'set_logs']
        read_only_fields = ['id', 'started_at']


class WorkoutSessionListSerializer(serializers.ModelSerializer):
    day_name = serializers.CharField(source='exercise_day.name', read_only=True, default='')

    class Meta:
        model = WorkoutSession
        fields = ['id', 'day_name', 'started_at', 'completed_at', 'is_completed']
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
        fields = ['id', 'meal_type', 'name', 'description', 'calories', 'protein_g', 'carbs_g', 'fat_g', 'order']
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
