"""
One-off: turn the agent's stored summary into the first message of a thread.

Every finished feedback item already carries the agent's reply in
`result_notes`. Once a thread exists, that reply has to be a row like any
other or the conversation starts with the user talking to nobody -- every
item filed before this change would open on a question and no answer.

Additive only: it creates rows, never edits or deletes one, so the worst case
is rows that can simply be dropped again. Idempotent on the agent-message
check, so a re-run (or a migration replayed on a restored dump) cannot
double up.

Lives outside the migration so it can be tested against real fixtures. The
local database is empty, so a data migration verified only by running it
proves nothing.
"""


def backfill_agent_messages(Feedback, FeedbackMessage):
    """Create the missing agent reply for each already-finished item.

    Takes the model classes as arguments so a migration can hand in its
    historical versions. Returns the number of rows created.
    """
    finished = set(getattr(Feedback, 'FINISHED', ('done', 'failed', 'skipped')))

    # One query for the items, one for the threads that already have a reply.
    already = set(
        FeedbackMessage.objects
        .filter(role='agent')
        .values_list('feedback_id', flat=True)
    )

    created = 0
    for item in Feedback.objects.filter(status__in=finished).exclude(result_notes=''):
        if item.id in already:
            continue
        msg = FeedbackMessage.objects.create(
            feedback=item,
            role='agent',
            body=item.result_notes,
            # Carry the read state across verbatim. Anything else either
            # re-lights the bell for things already read or silently marks
            # unread ones as read.
            seen_at=item.seen_at,
        )
        # created_at is auto_now_add, so it lands as "now" and the thread
        # would claim the reply arrived today. Correct it to when the item
        # actually finished.
        FeedbackMessage.objects.filter(pk=msg.pk).update(created_at=item.updated_at)
        created += 1
    return created
