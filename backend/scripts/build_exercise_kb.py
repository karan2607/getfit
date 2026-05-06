"""
One-time script to build exercise_kb.json from free-exercise-db.
Run from the backend/ directory: python scripts/build_exercise_kb.py
"""
import json
import urllib.request
import os

BASE = 'https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/'
SRC = 'https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json'

print('Fetching exercises.json...')
with urllib.request.urlopen(SRC) as r:
    exercises = json.loads(r.read())

kb = {}
for ex in exercises:
    key = ex['name'].lower()
    urls = [BASE + img for img in ex.get('images', [])]
    if urls:
        kb[key] = urls

out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'exercise_kb.json')
with open(out_path, 'w') as f:
    json.dump(kb, f, indent=2, sort_keys=True)

print(f'Built KB with {len(kb)} exercises → {out_path}')
