"""
Populate `ExerciseLibraryEntry` from the registries that already exist.

Why this is a seed and not a data file
--------------------------------------
Everything here is already known: `exercise_names` owns canonical identity,
`exercise_types` owns the field contract, `exercise_muscles` owns movement
facts. A hand-written library would be a fourth copy of all three and would
drift from them the way `Exercise.MEASUREMENT_TYPE_CHOICES` drifted from
`ALL_TYPES`.

Its job in the plan-mutation flow: a draft naming an exercise with no entry
here is rejected rather than resolved to a near-match. That is the lesson from
image resolution, where a score threshold sent Push-ups to `handstand
push-ups` and Bench Press scored 0.87 against Overhead Press.

What that rule means for SCOPE
------------------------------
Rejection is only safe if the library is the whole movement universe. Seeding
only `CANONICAL_NAMES` looked right and was badly wrong: it is 54 curated
display spellings, and `Push-ups` -- which is in the live plan -- is not among
them. A 54-row library would have rejected most of the user's own plan and
capped every future plan at 54 exercises.

So three sources, deduplicated by canonical key:

  1. `exercise_muscles.json` -- the movement catalogue, and the only source
     with muscle data
  2. `CANONICAL_NAMES` -- curated spellings, which are what we want DISPLAYED
     where a name appears in both
  3. names already stored on `Exercise` rows -- so no existing plan becomes
     uneditable because its exercise predates the library

The library is therefore SELF-EXTENDING, and is meant to be. It runs on every
deploy alongside `upgrade_data`, so any movement that has entered a plan since
the last deploy becomes a library entry. That is what keeps the catalogue
honest over time: 876 records is whatever one upstream dataset happened to
cover, not the set of exercises a person can actually do, and freezing it
there would slowly make the library wrong.

A draft naming something absent is WARNED about, never rejected -- "Weighted
Dips: new, not in your library" -- so a genuinely new movement is surfaced for
confirmation and then becomes part of the library on the next deploy. What
stays banned is silent near-matching: no draft name is ever resolved onto an
existing entry by similarity, which is the failure that sent Push-ups to
`handstand push-ups` and scored Bench Press 0.87 against Overhead Press.

Idempotent. Catalogue rows (user=NULL) are updated in place, so re-running
after a registry change refreshes them without touching per-user rows.

    python manage.py seed_exercise_library
    python manage.py seed_exercise_library --dry-run
"""

from django.core.management.base import BaseCommand

from api import exercise_muscles, exercise_names, exercise_types
from api.models import ExerciseLibraryEntry


def _library_names():
    """
    Every movement the library should contain, best display name winning.

    Keyed by canonical identity so the three sources deduplicate: the KB's
    `barbell bench press` and the registry's `Barbell Bench Press` are one
    entry, spelled the curated way.
    """
    from api.models import Exercise

    best = {}

    def offer(name, rank):
        """Lower rank wins. 0 curated, 1 already in a plan, 2 raw catalogue."""
        key = exercise_names.key(name)
        if not key:
            return
        current = best.get(key)
        if current is None or rank < current[0]:
            best[key] = (rank, name)

    for name in exercise_names.CANONICAL_NAMES:
        offer(name, 0)
    for name in Exercise.objects.values_list('name', flat=True).distinct():
        offer(exercise_names.canonical(name), 1)
    for raw in exercise_muscles.catalogue_names():
        offer(exercise_names.canonical(raw), 2)

    return [name for _rank, name in best.values()]


def canonical_key_for(name):
    """
    `exercise_names.key()` is a frozenset; store it as sorted tokens joined by
    a space so it is unique, greppable in a shell, and stable across spellings.
    """
    return ' '.join(sorted(exercise_names.key(name)))


def entry_fields_for(name):
    """Everything the registries know about one canonical name."""
    profile = exercise_muscles.profile_for(name)
    muscles = {}
    equipment = ''
    if profile:
        muscles = {
            'primary': sorted(profile['primary']),
            'secondary': sorted(profile['secondary']),
            'force': profile['force'],
            'mechanic': profile['mechanic'],
            'kb_key': profile['key'],
        }
        equipment = profile['equipment']
    return {
        'name': name,
        'measurement_type': exercise_types.normalize(None, name),
        'muscles': muscles,
        'equipment': equipment,
    }


class Command(BaseCommand):
    help = 'Derive the exercise library from the canonical registries.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change, write nothing.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        created = updated = unchanged = 0
        no_muscle_data = []

        for name in sorted(_library_names()):
            fields = entry_fields_for(name)
            if not fields['muscles']:
                no_muscle_data.append(name)

            key = canonical_key_for(name)
            existing = ExerciseLibraryEntry.objects.filter(
                canonical_key=key, user__isnull=True).first()

            if existing is None:
                created += 1
                if not dry_run:
                    ExerciseLibraryEntry.objects.create(
                        canonical_key=key, **fields)
                continue

            changes = {f: v for f, v in fields.items()
                       if getattr(existing, f) != v}
            if not changes:
                unchanged += 1
                continue
            updated += 1
            self.stdout.write('  %s: %s' % (name, ', '.join(sorted(changes))))
            if not dry_run:
                for field, value in changes.items():
                    setattr(existing, field, value)
                existing.save(update_fields=list(changes))

        self.stdout.write('')
        self.stdout.write('%s%d created, %d updated, %d unchanged'
                          % ('[dry run] ' if dry_run else '',
                             created, updated, unchanged))

        # Not an error. `profile_for` returns None when the muscle KB has no
        # confident record, and the entry is still valid for identity and the
        # field contract -- it just cannot take part in superset validation.
        if no_muscle_data:
            self.stdout.write(self.style.WARNING(
                '%d entr%s without muscle data (superset validity cannot be '
                'computed for these): %s'
                % (len(no_muscle_data),
                   'y' if len(no_muscle_data) == 1 else 'ies',
                   ', '.join(no_muscle_data))))
