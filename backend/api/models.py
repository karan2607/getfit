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
    # IANA name ("America/New_York"), set by the client from
    # Intl.DateTimeFormat().resolvedOptions().timeZone.
    #
    # The server runs on UTC, so without this every "today" was the server's
    # date: meals logged at 11pm local were stored under the correct local date
    # and then read back against the next UTC day, reporting an empty day.
    # Blank means UTC, which is the previous behaviour exactly.
    timezone = models.CharField(max_length=64, blank=True, default='')
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


class ApiKey(models.Model):
    """
    A long-lived credential for an external integration.

    Why this is not just the DRF token
    ----------------------------------
    DRF models `Token` as ONE ROW PER USER, so a browser session and every
    external integration share a single credential -- and `logout()` deletes
    it. The web app auto-logs-out after 30 minutes of inactivity and calls that
    endpoint, so leaving a tab open silently revoked the phone agent's access.
    The symptom was "my token to send health metrics has expired"; DRF tokens
    have no expiry, the row was being deleted and a different one minted.

    An ApiKey is independent of sessions by construction: nothing in login,
    logout or password-change touches it. It is also scoped, so a key living on
    a phone all day cannot change the account password or reach admin
    endpoints.
    """

    SCOPE_LOGGING = 'logging'
    SCOPE_FULL = 'full'
    SCOPE_CHOICES = [
        (SCOPE_LOGGING, 'Health, diet and workout logging'),
        (SCOPE_FULL, 'Full account access'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='api_keys')
    label = models.CharField(max_length=64)
    key = models.CharField(max_length=64, unique=True, db_index=True)
    scope = models.CharField(max_length=32, choices=SCOPE_CHOICES,
                             default=SCOPE_LOGGING)
    created_at = models.DateTimeField(auto_now_add=True)
    # Surfaced in the UI. A key that silently stopped being used is exactly the
    # failure this model exists to prevent, and "last used 3 days ago" makes it
    # obvious at a glance.
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.new_key()
        super().save(*args, **kwargs)

    @staticmethod
    def new_key():
        """
        43 urlsafe characters from `secrets`, the same generator
        PasswordResetToken and HealthConnection.new_sync_token use.

        Deliberately not derived from anything about the user: the original
        sync token was md5(user_id + "getFit") in a public repo, which
        migration 0034 had to replace.
        """
        return secrets.token_urlsafe(32)

    @property
    def is_active(self):
        return self.revoked_at is None

    @property
    def prefix(self):
        """First 8 characters, for identifying a key without revealing it."""
        return self.key[:8]

    def touch(self, now=None, min_interval=timedelta(hours=1)):
        """
        Record use, at most once per `min_interval`.

        An agent polling all day would otherwise write this row on every
        request for no benefit -- hourly resolution is plenty to answer "is
        this key still alive".
        """
        now = now or timezone.now()
        if self.last_used_at and (now - self.last_used_at) < min_interval:
            return False
        # update() rather than save() so this never races with a concurrent
        # request rewriting other fields.
        ApiKey.objects.filter(pk=self.pk).update(last_used_at=now)
        self.last_used_at = now
        return True

    def __str__(self):
        return '%s (%s…)' % (self.label, self.prefix)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatSession(models.Model):
    SOURCE_MAIN = 'main'
    SOURCE_EMBEDDED = 'embedded'
    SOURCE_CHOICES = [(SOURCE_MAIN, 'Main'), (SOURCE_EMBEDDED, 'Embedded')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_sessions')
    title = models.CharField(max_length=100, default='New Chat')
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_MAIN)
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
    current_week = models.PositiveIntegerField(default=1)
    current_day_order = models.PositiveIntegerField(default=0)
    specific_goal = models.TextField(blank=True)
    program_target = models.JSONField(null=True, blank=True)
    goal_check_in_shown = models.BooleanField(default=False)

    # ---- per-week generation -------------------------------------------
    # Plans are generated ONE WEEK AT A TIME. An 8-week plan emitted in a
    # single call is ~25,000 output tokens, and a sweep of four models hit 2
    # unparseable responses in 14 attempts (~14%) at that size -- truncation
    # at the model's output ceiling leaves JSON that cannot parse. Week-sized
    # calls are ~3,000 tokens.
    #
    # The bigger win is quality: weeks 2+ used to be written before the user
    # had trained a single session. Now each week is generated with the
    # previous weeks' logged performance in the prompt.
    #
    # The phase structure (name, focus, week range) for the whole program, so
    # the UI can show the shape of weeks that do not exist yet. Ranges are
    # integers and validated to tile 1..duration_weeks exactly.
    program_outline = models.JSONField(null=True, blank=True)
    # Set while a week is being generated in a background thread. Doubles as
    # the concurrency guard -- one generation per plan at a time.
    preparing_week = models.PositiveIntegerField(null=True, blank=True)
    prepare_started_at = models.DateTimeField(null=True, blank=True)
    prepare_error = models.TextField(blank=True)
    # Highest upgrade step from api/data_upgrades.py already applied to this plan.
    # Lets a major update repair existing plans in place instead of requiring the
    # user to recreate them. See `manage.py upgrade_data`.
    data_version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # A generation that died mid-run (dyno restart, thread killed) would
    # otherwise leave `preparing_week` set forever and wedge the plan. Any
    # lock older than this is treated as failed and can be retried.
    PREPARE_LOCK_TIMEOUT = timedelta(minutes=5)

    @property
    def weeks_ready(self):
        """
        Highest week number that actually has days.

        Derived rather than stored so it cannot drift from reality. Plans
        created before per-week generation have every week materialised, so
        this returns their full duration and they need no special-casing.
        """
        from django.db.models import Max
        return self.days.aggregate(m=Max('week_number'))['m'] or 0

    @property
    def prepare_lock_is_stale(self):
        if self.preparing_week is None or self.prepare_started_at is None:
            return False
        return timezone.now() - self.prepare_started_at > self.PREPARE_LOCK_TIMEOUT

    @property
    def is_preparing(self):
        """Preparing, and the lock has not expired."""
        return self.preparing_week is not None and not self.prepare_lock_is_stale
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.title} ({self.user.email})'


class WorkoutDay(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name='days')
    day_number = models.PositiveIntegerField()
    week_number = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=100)
    focus = models.CharField(max_length=100, blank=True)
    is_rest_day = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    last_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['week_number', 'order']
        unique_together = ('plan', 'week_number', 'day_number')

    def __str__(self):
        return f'{self.name} (Plan: {self.plan.title})'


class Exercise(models.Model):
    MEASUREMENT_TYPE_CHOICES = [
        ('weight_reps', 'Weight + Reps'),
        ('weight_duration', 'Weight + Duration'),
        ('duration', 'Duration only'),
        ('warmup', 'Warmup/Mobility'),
        ('bodyweight_reps', 'Bodyweight + Reps'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE, related_name='exercises')
    name = models.CharField(max_length=150)
    sets = models.PositiveIntegerField()
    reps = models.CharField(max_length=100)   # "8-12", "AMRAP", "30 sec"
    rest_seconds = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    measurement_type = models.CharField(max_length=20, choices=MEASUREMENT_TYPE_CHOICES, default='weight_reps')
    # Exercises on the same day sharing a group are performed back to back.
    # null = a standalone exercise, which is almost everything.
    #
    # Invariants enforced server-side (see views._validate_superset_groups):
    # >=2 members, contiguous in `order`, no warmup member, equal `sets`, and
    # every pair accepted by exercise_muscles.superset_verdict -- so a bad
    # pairing is not reachable by the AI or by hand.
    #
    # SetLog is deliberately untouched: each member still logs its own sets,
    # so the field contract, coerce_set_fields, progression and history all
    # keep working unchanged.
    superset_group = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


class WorkoutSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='workout_sessions')
    exercise_day = models.ForeignKey(WorkoutDay, on_delete=models.SET_NULL, null=True, blank=True)
    day_name = models.CharField(max_length=255, blank=True)  # denormalized — survives day deletion
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
    # Which of these three carry meaning depends on the parent Exercise's
    # measurement_type. See api/exercise_types.FIELD_CONTRACT -- it is enforced on
    # write by coerce_set_fields(), so fields that do not apply are always NULL.
    reps_completed = models.PositiveIntegerField(null=True, blank=True)
    weight_kg = models.FloatField(null=True, blank=True)   # always kilograms
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['exercise__order', 'set_number']
        unique_together = ('workout_session', 'exercise', 'set_number')

    def __str__(self):
        return f'{self.exercise_name} set {self.set_number}'


class ProgramCheckin(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='program_checkins')
    plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name='checkins')
    metric = models.CharField(max_length=100)
    start_value = models.FloatField(null=True, blank=True)
    end_value = models.FloatField(null=True, blank=True)
    ai_summary = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


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
    # user=NULL is the shared generic guide (form steps, muscles, images).
    # A row with a user is that user's personalised copy, carrying their weight
    # recommendation and coaching note. Previously `name` was globally unique with
    # no user FK, so one user's personalised advice was served to everyone.
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='exercise_guides',
        null=True, blank=True,
    )
    name = models.CharField(max_length=255, db_index=True)
    data = models.JSONField()  # {steps, muscles, tips, category, images}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        unique_together = ('user', 'name')

    def __str__(self):
        return self.name


class MealGuide(models.Model):
    name = models.CharField(max_length=255, unique=True, db_index=True)
    data = models.JSONField()  # {ingredients, steps, prep_time, tips}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Health Sync (Terra API)
# ---------------------------------------------------------------------------

class HealthConnection(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='health_connection')
    provider = models.CharField(max_length=64, default='APPLE')
    # A write credential: whoever holds it can post health data to this account.
    sync_token = models.CharField(max_length=64, unique=True, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def new_sync_token():
        """
        Unguessable sync token, matching how PasswordResetToken mints its own.

        This was previously md5(user_id + "getFit"), which is three separate
        problems: the derivation and its salt are readable in a PUBLIC repo, the
        value is therefore computable by anyone who learns the account's UUID,
        and because save() only filled a blank field there was no way to rotate
        it short of DELETE -- which wipes all of the user's health data.
        """
        return secrets.token_urlsafe(32)

    def rotate_sync_token(self):
        """Issue a fresh token, invalidating the previous one immediately."""
        self.sync_token = self.new_sync_token()
        self.save(update_fields=['sync_token'])
        return self.sync_token

    def save(self, *args, **kwargs):
        if not self.sync_token:
            self.sync_token = self.new_sync_token()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.provider} connection for {self.user.email}'


class HealthDailySummary(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='health_daily')
    date = models.DateField()
    steps = models.IntegerField(null=True, blank=True)
    active_calories = models.FloatField(null=True, blank=True)
    resting_calories = models.FloatField(null=True, blank=True)
    resting_heart_rate = models.FloatField(null=True, blank=True)
    sleep_hours = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = ['user', 'date']
        ordering = ['-date']

    def __str__(self):
        return f'{self.user.email} — {self.date}'


class HealthWorkout(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='health_workouts')
    # The client's own id for this workout. Named terra_workout_id for historical
    # reasons -- the Terra integration it was added for has been removed.
    #
    # Uniqueness is scoped to the user. It used to be globally unique, and the
    # sync did update_or_create(terra_workout_id=...) WITHOUT filtering by user,
    # so a client sending an id another account had already used would silently
    # reassign that account's workout row to itself.
    terra_workout_id = models.CharField(max_length=255)
    activity_type = models.CharField(max_length=128)
    start_time = models.DateTimeField()
    duration_seconds = models.IntegerField(null=True, blank=True)
    calories = models.FloatField(null=True, blank=True)
    avg_heart_rate = models.FloatField(null=True, blank=True)
    distance_meters = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ['-start_time']
        unique_together = ('user', 'terra_workout_id')

    def __str__(self):
        return f'{self.activity_type} at {self.start_time} ({self.user.email})'


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

class Feedback(models.Model):
    """
    In-app feedback queued for automated implementation.

    Submission is restricted to superusers (enforced in the view, not just by
    hiding the button). That restriction is the security boundary: an agent loop
    that implements and pushes code from this table is only safe while the only
    author of its contents is a trusted operator. Opening submission to all
    users would turn this into a prompt-injection surface that writes to the
    repository.
    """

    STATUS_CHOICES = [
        ('new', 'New'),
        ('in_progress', 'In progress'),
        ('done', 'Done'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='feedback')
    body = models.TextField()
    # Where in the app it was submitted from, for context.
    page = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new',
                              db_index=True)
    # Filled in by the automation: what it did, and the commit it shipped.
    result_notes = models.TextField(blank=True)
    commit_sha = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.status}] {self.body[:60]}'
