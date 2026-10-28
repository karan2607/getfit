"""
Build exercise_muscles.json from free-exercise-db.

Run from the backend/ directory: python scripts/build_exercise_muscles.py

This script did not exist before -- the file was generated inline, which meant
the only way to add a field was to redo the import from memory and hope the
other fields came out identical. It is committed now so the import is
reproducible and reviewable as a diff.

The output feeds api/exercise_muscles.py, which decides whether two exercises
may be supersetted. Only movement FACTS are kept; no instructions, images or
difficulty levels, because nothing downstream reads them and a smaller file is
easier to audit.

`equipment` was omitted on the original import, which is why station
contention could not be checked -- a superset ties up two stations at once, and
"two barbell lifts back to back" is impractical whatever the muscles say.
"""
import json
import os
import urllib.request

SRC = 'https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json'

# Fields kept, in output order. force and primary are REQUIRED downstream:
# exercise_muscles.profile_for returns None without both, so a partial record
# can never produce a confident-looking verdict.
print('Fetching exercises.json...')
with urllib.request.urlopen(SRC) as response:
    exercises = json.loads(response.read())

kb = {}
for exercise in exercises:
    key = exercise['name'].lower()
    record = {}
    if exercise.get('force'):
        record['force'] = exercise['force']
    if exercise.get('mechanic'):
        record['mechanic'] = exercise['mechanic']
    if exercise.get('primaryMuscles'):
        record['primary'] = exercise['primaryMuscles']
    if exercise.get('secondaryMuscles'):
        record['secondary'] = exercise['secondaryMuscles']
    if exercise.get('equipment'):
        record['equipment'] = exercise['equipment']
    if record:
        kb[key] = record

out_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'exercise_muscles.json')
with open(out_path, 'w') as handle:
    json.dump(kb, handle, indent=2, sort_keys=True)
    handle.write('\n')

print('Built %d records -> %s' % (len(kb), out_path))
