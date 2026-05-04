import uuid
import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=30, unique=True, null=True, blank=True)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    objects = UserManager()

    def __str__(self):
        return self.email


class UserProfile(models.Model):
    GENDER_CHOICES = [('male', 'Male'), ('female', 'Female'), ('other', 'Other')]
    GOAL_CHOICES = [('lose_fat', 'Lose Fat'), ('build_muscle', 'Build Muscle'), ('maintain', 'Maintain')]
    EXPERIENCE_CHOICES = [('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced')]
    DIET_CHOICES = [('non_veg', 'Non-Vegetarian'), ('vegetarian', 'Vegetarian'), ('vegan', 'Vegan')]
    UNIT_CHOICES = [('lb', 'Pounds (lb)'), ('kg', 'Kilograms (kg)')]
    ACTIVITY_CHOICES = [
        ('sedentary', 'Sedentary'),
        ('lightly_active', 'Lightly Active'),
        ('moderately_active', 'Moderately Active'),
        ('very_active', 'Very Active'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    height_cm = models.FloatField(null=True, blank=True)
    weight_kg = models.FloatField(null=True, blank=True)
    age = models.PositiveIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, null=True, blank=True)
    fitness_goal = models.CharField(max_length=20, choices=GOAL_CHOICES, null=True, blank=True)
    experience_level = models.CharField(max_length=15, choices=EXPERIENCE_CHOICES, null=True, blank=True)
    dietary_preference = models.CharField(max_length=15, choices=DIET_CHOICES, null=True, blank=True)
    activity_level = models.CharField(max_length=20, choices=ACTIVITY_CHOICES, null=True, blank=True)
    preferred_unit = models.CharField(max_length=2, choices=UNIT_CHOICES, default='lb')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Profile of {self.user.email}'

    personal_notes = models.TextField(blank=True)  # injuries, preferences, AI-remembered context

    @property
    def is_complete(self):
        return all([self.fitness_goal, self.experience_level, self.dietary_preference])


class PasswordResetToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_resets')
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=1)
        super().save(*args, **kwargs)

    @property
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_sessions')
    title = models.CharField(max_length=100, default='New Chat')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.title} ({self.user.email})'


class ChatMessage(models.Model):
    ROLE_CHOICES = [('user', 'User'), ('assistant', 'Assistant')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'[{self.role}] {self.content[:50]}'


# ---------------------------------------------------------------------------
# Workout Planner
# ---------------------------------------------------------------------------

class WorkoutPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workout_plans')
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    activated_at = models.DateTimeField(null=True, blank=True)
    generated_by_ai = models.BooleanField(default=True)
    duration_weeks = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} ({self.user.email})'


class WorkoutDay(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name='days')
    day_number = models.PositiveIntegerField()
    name = models.CharField(max_length=100)
    focus = models.CharField(max_length=100, blank=True)
    is_rest_day = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']
        unique_together = ('plan', 'day_number')

    def __str__(self):
        return f'{self.name} (Plan: {self.plan.title})'


class Exercise(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE, related_name='exercises')
    name = models.CharField(max_length=150)
    sets = models.PositiveIntegerField()
    reps = models.CharField(max_length=100)   # "8-12", "AMRAP", "30 sec"
    rest_seconds = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


class WorkoutSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workout_sessions')
    exercise_day = models.ForeignKey(WorkoutDay, on_delete=models.SET_NULL, null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_completed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'Session {self.id} by {self.user.email}'


class SetLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workout_session = models.ForeignKey(WorkoutSession, on_delete=models.CASCADE, related_name='set_logs')
    exercise = models.ForeignKey(Exercise, on_delete=models.SET_NULL, null=True, blank=True)
    exercise_name = models.CharField(max_length=150)   # denormalized — survives plan edits
    set_number = models.PositiveIntegerField()
    reps_completed = models.PositiveIntegerField(null=True, blank=True)
    weight_kg = models.FloatField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['exercise__order', 'set_number']
        unique_together = ('workout_session', 'exercise', 'set_number')

    def __str__(self):
        return f'{self.exercise_name} set {self.set_number}'


# ---------------------------------------------------------------------------
# Diet Planner
# ---------------------------------------------------------------------------

class MealLog(models.Model):
    MEAL_CHOICES = [('breakfast', 'Breakfast'), ('lunch', 'Lunch'), ('dinner', 'Dinner'), ('snack', 'Snack')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meal_logs')
    date = models.DateField()
    meal_type = models.CharField(max_length=15, choices=MEAL_CHOICES)
    food_name = models.CharField(max_length=200)
    calories = models.PositiveIntegerField(default=0)
    protein_g = models.FloatField(default=0)
    carbs_g = models.FloatField(default=0)
    fat_g = models.FloatField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'meal_type', 'created_at']

    def __str__(self):
        return f'{self.food_name} on {self.date} ({self.user.email})'


class DietPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='diet_plans')
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    target_calories = models.PositiveIntegerField()
    protein_g = models.PositiveIntegerField()
    carbs_g = models.PositiveIntegerField()
    fat_g = models.PositiveIntegerField()
    is_active = models.BooleanField(default=False)
    generated_by_ai = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} ({self.user.email})'


class Meal(models.Model):
    MEAL_CHOICES = [
        ('breakfast', 'Breakfast'),
        ('lunch', 'Lunch'),
        ('dinner', 'Dinner'),
        ('snack', 'Snack'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(DietPlan, on_delete=models.CASCADE, related_name='meals')
    day_number = models.PositiveIntegerField(default=1)
    meal_type = models.CharField(max_length=15, choices=MEAL_CHOICES)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    calories = models.PositiveIntegerField()
    protein_g = models.PositiveIntegerField()
    carbs_g = models.PositiveIntegerField()
    fat_g = models.PositiveIntegerField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f'{self.name} ({self.meal_type})'


class FoodScanResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='food_scans')
    food_name = models.CharField(max_length=200)
    serving_size = models.CharField(max_length=100, blank=True)
    calories = models.PositiveIntegerField()
    protein_g = models.FloatField()
    carbs_g = models.FloatField()
    fat_g = models.FloatField()
    fiber_g = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.food_name} ({self.user.email})'


# ---------------------------------------------------------------------------
# Body Scanner
# ---------------------------------------------------------------------------

class BodyScanResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='body_scans')
    body_fat_pct = models.FloatField(null=True, blank=True)
    physique_category = models.CharField(max_length=100, blank=True)
    muscle_mass_note = models.TextField(blank=True)
    posture_notes = models.TextField(blank=True)
    recommendations = models.TextField(blank=True)
    disclaimer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'BodyScan {self.id} ({self.user.email})'


# ---------------------------------------------------------------------------
# Exercise Guide Knowledge Base
# ---------------------------------------------------------------------------

class ExerciseGuide(models.Model):
    name = models.CharField(max_length=255, unique=True, db_index=True)
    data = models.JSONField()  # {steps, muscles, tips, category, images}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
