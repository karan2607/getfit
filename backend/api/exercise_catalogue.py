"""
The exercise catalogue: what the AI may choose from when it writes a plan.

Distinct from three neighbours it is easy to confuse:

  exercise_kb.json        name -> photo URLs.      "what does this look like"
  exercise_photos.json    name -> VERIFIED photo.  "is that photo actually it"
  exercise_muscles.json   movement facts.          "what does it train"
  ExerciseLibraryEntry    a DB table.              "is this a real movement"

This module holds NO data of its own. It is a view over
`exercise_muscles.json`, which already carries all 876 upstream records --
a second copy would be the fourth of the same dataset, which is what the
exercise-library seeder's own docstring warns against.

The DB-backed ExerciseLibraryEntry is a different thing again: it is what a
plan draft is checked against, seeded from the registries. This is what the
MODEL READS when writing a plan.

Why this exists
---------------
Plan generation never had a catalogue. `_workout_system_prompt` handed the
model a JSON schema and a profile, and the model named exercises out of its
own head; the 873-entry photo library was consulted only afterwards, to find
a picture for whatever name came back. A model reaches for the same
gym-standard vocabulary every time -- 423 distinct names across every plan
we have captured -- and those collapse onto 107 photo folders. The other 766
were unreachable by construction, and 98 of the names the model did write had
no photo at all.

So the fix is not in the photo lookup. It is here: name the exercises from a
catalogue whose names are known to resolve, and the lookup stops guessing.

What this module refuses to do
------------------------------
It does not rank, score or fuzzy-match. `catalogue_text` filters on the
upstream facets (equipment, muscle, category) and nothing else, because a
scorer here would be a second opinion competing with `_resolve_kb_key` and
the two would drift -- which is exactly the bug that made `_get_kb_candidates`
and the image lookup disagree for months.
"""
import logging

from . import exercise_muscles

logger = logging.getLogger(__name__)

#: Upstream equipment values are inconsistent about number and wording. These
#: are display-only: the stored facet is never rewritten, so a filter still
#: matches on the upstream spelling.
_EQUIPMENT_LABELS = {
    'body only': 'bodyweight',
    'kettlebells': 'kettlebell',
    'e-z curl bar': 'ez-bar',
    'exercise ball': 'ball',
    'medicine ball': 'med ball',
    'foam roll': 'foam roller',
    'bands': 'band',
}

#: Categories a strength plan should not be built out of. Stretching is not
#: junk -- it is simply not what "give me 4 training days" is asking for, and
#: 123 stretches in the prompt crowd out the movements that are.
_PLAN_CATEGORIES = (
    'strength', 'powerlifting', 'olympic weightlifting', 'plyometrics', 'cardio',
)


def _load() -> dict:
    """
    The catalogue, keyed by upstream display name.

    Built from `exercise_muscles.records()`, which is keyed by the lowercased
    name. Records written before `display` was added fall back to the key, so
    a stale data file degrades to ugly names rather than an empty catalogue.
    """
    out = {}
    for key, rec in exercise_muscles.records().items():
        out[rec.get('display') or key] = rec
    return out


def all_names() -> tuple:
    """Every catalogue name, in upstream spelling."""
    return tuple(_load())


def get(name: str):
    """The facets for one catalogue name, or None. Case-insensitive."""
    lib = _load()
    if name in lib:
        return lib[name]
    lowered = (name or '').strip().lower()
    for key, value in lib.items():
        if key.lower() == lowered:
            return value
    return None


def equipment_label(value: str) -> str:
    return _EQUIPMENT_LABELS.get(value or '', value or 'none')


def search(*, muscle: str = None, equipment: str = None, category: str = None,
           plan_only: bool = True, limit: int = 60) -> list:
    """
    Facet filter over the catalogue. Exact, case-insensitive, no scoring.

    `muscle` matches primary OR secondary: asking for a triceps movement and
    being shown only the 73 that target it primarily hides every press.
    """
    out = []
    for name, facets in _load().items():
        if plan_only and (facets.get('category') or '') not in _PLAN_CATEGORIES:
            continue
        if category and (facets.get('category') or '').lower() != category.lower():
            continue
        if equipment and (facets.get('equipment') or '').lower() != equipment.lower():
            continue
        if muscle:
            wanted = muscle.lower()
            muscles = [m.lower() for m in
                       (facets.get('primary') or []) + (facets.get('secondary') or [])]
            if wanted not in muscles:
                continue
        out.append({'name': name, **facets})
        if limit and len(out) >= limit:
            break
    return out


def catalogue_text(*, equipment: str = None, plan_only: bool = True,
                   exclude=()) -> str:
    """
    The catalogue as prompt text, grouped by primary muscle.

    `equipment` is the free-text answer from the generation form ("full gym",
    "dumbbells only", "home - bands and a bench"). It narrows the list only
    when it names equipment the catalogue actually has; anything else leaves
    the list whole, because a narrowed list that guessed wrong is worse than
    a long one. A gym with everything is the common case and wants no filter.

    `exclude` is a set of canonical keys this client must not be offered --
    their own contraindicated entries. Showing the model a vocabulary that
    still contains the movement their knee cannot do invites it straight back
    into the plan, and a list is more persuasive than a prose warning.
    """
    # Deferred: exercise_names registers this module's names, so importing it
    # at module scope would be a cycle. See _build_index there.
    from . import exercise_names

    wanted = _equipment_filter(equipment)
    groups, seen = {}, set()
    for name, facets in _load().items():
        if plan_only and (facets.get('category') or '') not in _PLAN_CATEGORIES:
            continue
        if wanted and (facets.get('equipment') or '') not in wanted:
            continue
        # Show the app's own spelling where it has one. The library writes
        # "Dumbbell Bench Press" and "Pushups"; this app has always written
        # "Flat Dumbbell Press" and "Push-ups", those are the names with a
        # verified photo, and a plan that used both spellings would split the
        # user's logged history between them. Folding here means the model is
        # told the right name in the first place rather than having it
        # rewritten underneath it on save.
        display = exercise_names.canonical(name)
        if exclude and exercise_names.key(display) in exclude:
            continue
        if display in seen:
            # Two library names that fold to one app name ("Dumbbell Alternate
            # Bicep Curl" and "Dumbbell Bicep Curl") are one catalogue entry.
            continue
        seen.add(display)
        muscle = (facets.get('primary') or ['other'])[0]
        groups.setdefault(muscle, []).append(
            '%s [%s]' % (display, equipment_label(facets.get('equipment'))))
    if not groups:
        return ''
    return '\n'.join(
        '%s: %s' % (muscle.upper(), '; '.join(sorted(names)))
        for muscle, names in sorted(groups.items()))


def _equipment_filter(equipment: str):
    """
    Which upstream equipment values the user's free text admits, or None.

    Returns None -- meaning "no filter" -- unless the text names specific
    equipment AND does not read like a full gym. Bodyweight is always allowed
    through: no answer to "what equipment do you have" means you cannot do
    push-ups.
    """
    text = (equipment or '').strip().lower()
    if not text:
        return None
    if any(word in text for word in ('full gym', 'commercial gym', 'everything',
                                     'full access', 'any', 'all')):
        return None
    found = set()
    for value, label in (('barbell', 'barbell'), ('dumbbell', 'dumbbell'),
                         ('machine', 'machine'), ('cable', 'cable'),
                         ('kettlebells', 'kettlebell'), ('bands', 'band'),
                         ('e-z curl bar', 'ez'), ('medicine ball', 'medicine ball'),
                         ('exercise ball', 'exercise ball'), ('other', '\0')):
        if label in text:
            found.add(value)
    if not found:
        return None
    found.add('body only')
    return found
