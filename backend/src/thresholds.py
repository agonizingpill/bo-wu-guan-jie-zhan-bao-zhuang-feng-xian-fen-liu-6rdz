import logging

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from redis.exceptions import RedisError

from .scoring import evaluate, heldout_sample, sha, trial_verdict
from .storage import THRESHOLD_QUEUE, audit, queue

LOCK = 66004


def current_profile(db):
    return db.execute('SELECT * FROM threshold_profile ORDER BY id DESC LIMIT 1').fetchone()


def latest_trial(db):
    return db.execute('SELECT * FROM threshold_trial ORDER BY id DESC').fetchone()


def serialize(trial, profile=None):
    if not trial:
        return None
    result = {key: value for key, value in trial.items() if key != 'sample'}
    if profile:
        result['stale'] = trial['baseline_profile_id'] != profile['id']
    return result


def summarize(db, trial_id=None):
    profile = current_profile(db)
    sample = heldout_sample()
    trial = (db.execute('SELECT * FROM threshold_trial WHERE id=%s', (trial_id,)).fetchone()
             if trial_id else latest_trial(db))
    return {'profile': profile, 'current': evaluate(sample, profile['thresholds']),
            'trial': serialize(trial, profile), 'sample_sha256': sha(sample), 'sample_size': len(sample)}


def start(db, proposed, actor):
    db.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
    profile = current_profile(db)
    existing = db.execute('''SELECT * FROM threshold_trial WHERE proposed=%s AND baseline_profile_id=%s
                          ORDER BY id DESC''', (Jsonb(proposed), profile['id'])).fetchone()
    if existing:
        # Retrying after an interruption resumes the stored trial and its original sample.
        if existing['status'] in ('running', 'failed'):
            db.execute('''UPDATE threshold_trial SET status='running', attempts=attempts+1, error=NULL
                       WHERE id=%s''', (existing['id'],))
            audit(db, None, actor, 'threshold_trial_resumed', trial_id=existing['id'],
                  attempts=existing['attempts'] + 1)
            existing = db.execute('SELECT * FROM threshold_trial WHERE id=%s', (existing['id'],)).fetchone()
        return serialize(existing, profile)
    sample = heldout_sample()
    row = db.execute('''INSERT INTO threshold_trial (status,proposed,baseline,baseline_profile_id,sample,
                      sample_sha256,requested_by) VALUES ('running',%s,%s,%s,%s,%s,%s) RETURNING *''',
                     (Jsonb(proposed), Jsonb(profile['thresholds']), profile['id'], Jsonb(sample),
                      sha(sample), actor)).fetchone()
    audit(db, None, actor, 'threshold_trial_queued', trial_id=row['id'], thresholds=proposed)
    return serialize(row, profile)

def publish(db, trial_id, actor):
    db.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK,))
    trial = db.execute('SELECT * FROM threshold_trial WHERE id=%s FOR UPDATE', (trial_id,)).fetchone()
    if not trial:
        raise HTTPException(404, '未找到阈值试算记录')
    if trial['status'] == 'published':
        raise HTTPException(409, '该试算已发布')
    if trial['status'] != 'completed':
        raise HTTPException(409, '试算尚未完成，不能发布')
    profile = current_profile(db)
    if profile['id'] != trial['baseline_profile_id']:
        raise HTTPException(409, '试算后现行阈值已调整，请用当前阈值重新试算')
    if not trial['eligible']:
        reasons = '；'.join(trial['ineligibility']) or '试算未达到发布条件'
        raise HTTPException(409, f'试算未达到发布条件：{reasons}')
    row = db.execute('''INSERT INTO threshold_profile (thresholds,origin,trial_id,published_by)
                     VALUES (%s,'trial',%s,%s) RETURNING *''',
                     (Jsonb(trial['proposed']), trial_id, actor)).fetchone()
    db.execute("UPDATE threshold_trial SET status='published', published_at=now() WHERE id=%s", (trial_id,))
    audit(db, None, actor, 'thresholds_published', trial_id=trial_id, thresholds=trial['proposed'],
          previous_thresholds=trial['baseline'])
    return {'profile': row, 'current': evaluate(heldout_sample(), row['thresholds'])}


def progress(db, trial_id):
    row = db.execute('SELECT * FROM threshold_trial WHERE id=%s', (trial_id,)).fetchone()
    if not row:
        raise HTTPException(404, '未找到阈值试算记录')
    return serialize(row, current_profile(db))


def evaluate_trial(db, trial):
    baseline = evaluate(trial['sample'], trial['baseline'])
    proposed = evaluate(trial['sample'], trial['proposed'])
    eligible, reasons = trial_verdict(baseline, proposed)
    db.execute('''UPDATE threshold_trial SET status='completed', results=%s, eligible=%s, ineligibility=%s,
                  computed_at=now(), error=NULL WHERE id=%s''',
               (Jsonb({'baseline': baseline, 'proposed': proposed}), eligible, Jsonb(reasons), trial['id']))
    audit(db, None, 'worker', 'threshold_trial_completed', trial_id=trial['id'], eligible=eligible,
          baseline=baseline['total'], proposed=proposed['total'])


def notify_worker(trial_id):
    try:
        with queue() as redis:
            redis.lpush(THRESHOLD_QUEUE, trial_id)
    except RedisError:
        logging.exception('Redis enqueue failed; worker will recover the committed trial')
