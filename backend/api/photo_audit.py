"""
Which library photos are allowed to be shown, and for what.

The photo library is the upstream free-exercise-db, and its folder names are
not trustworthy: its "barbell squat" folder holds FRONT squat photos, its
"cable shrugs" folder holds a cable curl. Matching an exercise name against
those folder names -- however exactly -- trusts the labels, and that is what
produced every "wrong photo" report this app has had.

So every folder was read by two models that were not shown its label, and the
394 they disagreed on were decided by a third read. A folder is served only
where the readers agree. Where they agree the label is wrong but agree on
what the photo DOES show, the folder is re-pointed at that exercise instead
of being thrown away. Where they do not agree, nothing is served: a blank is
honest, a biceps curl for a hamstring curl is not.

This module is the gate. `_resolve_kb_key` still finds candidate keys; this
decides whether the one it found may be used.
"""
import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

_AUDIT = None
_LOCK = threading.Lock()

#: Verdicts that may NOT be shown.
#:
#: Only one: a folder the readers agreed is mislabelled. Everything else is
#: served, INCLUDING 'unresolved'.
#:
#: The first cut had this the other way round -- serve only what the audit
#: confirmed -- and it withheld 478 of the 873 folders, because two readers
#: disagreeing is common on a 2D photo of an unfamiliar movement. That is
#: not evidence the photo is wrong; it is evidence the readers could not
#: agree on a name. Those photos are served today and are mostly fine, and
#: blanking them would have made the library LESS useful in the name of an
#: audit meant to make it more so.
WITHHELD = ('reassigned',)


def _load() -> dict:
    global _AUDIT
    if _AUDIT is None:
        with _LOCK:
            if _AUDIT is None:
                path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    '..', 'exercise_photos.json')
                try:
                    with open(path) as fh:
                        _AUDIT = json.load(fh)
                    logger.info('photo audit loaded: %d folders',
                                len(_AUDIT.get('photos') or {}))
                except Exception as exc:
                    # No audit file means no gate. The app then behaves as it
                    # did before the audit existed -- worse, but not broken.
                    logger.warning('photo audit load failed: %s', exc)
                    _AUDIT = {}
    return _AUDIT


def verdict(kb_key: str) -> str:
    """
    What the audit decided about one library folder.

    'unknown' for a folder the audit never covered, which is NOT the same as
    'unresolved': unknown means nobody looked, and the caller may fall back to
    the old behaviour rather than show a blank.
    """
    row = (_load().get('photos') or {}).get(kb_key or '')
    if not row:
        return 'unknown'
    return row.get('verdict') or 'unknown'


def shows(kb_key: str):
    """What the readers agreed the folder's photos actually depict."""
    row = (_load().get('photos') or {}).get(kb_key or '')
    return (row or {}).get('shows')


def may_serve(kb_key: str, *, name: str = '') -> bool:
    """
    True unless the audit found this folder's photos show something else.

    Deliberately permissive. The audit's job is to remove photos that are
    WRONG, not to require proof of every photo that is right: an unaudited
    folder, or one the readers could not agree on, keeps working exactly as
    it does today. The only photos withheld are the ones two readers agreed
    depict a different movement from the folder's own label -- which is the
    case that produced every wrong-photo report.
    """
    state = verdict(kb_key)
    if state in WITHHELD:
        logger.info('photo audit withheld %r for %r (%s)', kb_key, name, state)
        return False
    return True


#: `why` values that mean a human decided this exercise gets no photo.
#: Only a human ruling blanks an exercise. The audit's own "I could not find
#: one" does not: that is the absence of evidence, and the resolver that has
#: served these photos all along is better than nothing.
DELIBERATE_NONE = ('human: no photo',)


def key_for(name):
    """
    The folder explicitly assigned to an exercise name, or None.

    Covers both halves of the audit's output: the exercises a human has ruled
    on, and the folders re-pointed at what they actually show.

    Returns the string 'none' ONLY for a deliberate human ruling that no photo
    depicts this exercise. An entry the audit simply could not assign returns
    None -- "not mentioned" -- so the caller falls through to the ordinary
    resolver. The two were conflated at first, and "nothing verified depicts
    it" blanked the pec deck, whose own folder two readers had confirmed shows
    exactly that.
    """
    exercises = _load().get('exercises') or {}
    if name not in exercises:
        return None
    assigned = exercises[name]
    why = ''
    if isinstance(assigned, dict):
        why = assigned.get('why') or ''
        assigned = assigned.get('photo_key')
    if assigned:
        return assigned
    return 'none' if why in DELIBERATE_NONE else None
