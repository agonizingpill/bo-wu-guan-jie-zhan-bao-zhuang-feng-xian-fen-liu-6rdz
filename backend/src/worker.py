import logging
import time
from pathlib import Path

from psycopg import Error as DatabaseError
from psycopg.types.json import Jsonb
from redis.exceptions import RedisError

from .loans import change_status, lock_loan
from .schemas import LoanStatus
from .scoring import score
from .storage import QUEUE, THRESHOLD_QUEUE, audit, connection, initialize, queue
from .thresholds import current_profile, evaluate_trial

HEARTBEAT = Path('/tmp/museum-heartbeat')


def process(case_id):
    with connection() as db:
        reference = db.execute('SELECT loan_id FROM triage_case WHERE id=%s', (case_id,)).fetchone()
        if not reference:
            return
        loan_id = reference['loan_id']
        lock_loan(db, loan_id)
        case = db.execute('SELECT * FROM triage_case WHERE id=%s', (case_id,)).fetchone()
        if case['status'] != 'queued':
            return
        try:
            pinned = case['thresholds'] or current_profile(db)['thresholds']
            probability, risk, evidence = score(case['input_snapshot'], pinned)
        except Exception as exc:
            logging.exception('Model failed to score case %s', case_id)
            db.execute("UPDATE triage_case SET status='failed',error=%s,attempts=attempts+1 WHERE id=%s",
                       ('模型计算失败。请检查包装快照后重试。', case_id))
            change_status(db, loan_id, LoanStatus.PACKING)
            audit(db, loan_id, 'worker', 'triage_failed', case_id=case_id, error_type=type(exc).__name__)
            return
        db.execute('''UPDATE triage_case SET status='scored', probability=%s, risk=%s, evidence=%s,
                       attempts=attempts+1,computed_at=now(),error=NULL WHERE id=%s''',
                   (probability, risk, Jsonb(evidence), case_id))
        if risk == 'high':
            db.execute("INSERT INTO unpack_review (case_id,status) VALUES (%s,'awaiting_witness')", (case_id,))
        change_status(db, loan_id, LoanStatus.REVIEW_REQUIRED if risk == 'high' else LoanStatus.READY)
        audit(db, loan_id, 'worker', 'triage_scored', case_id=case_id, risk=risk, probability=probability)
        print(f'case={case_id} loan={loan_id} risk={risk} probability={probability:.6f}', flush=True)


def process_trial(trial_id):
    with connection() as db:
        trial = db.execute('SELECT * FROM threshold_trial WHERE id=%s FOR UPDATE', (trial_id,)).fetchone()
        if not trial or trial['status'] not in ('running', 'failed'):
            return
        try:
            evaluate_trial(db, trial)
        except Exception as exc:
            logging.exception('Threshold trial %s failed', trial_id)
            db.execute("UPDATE threshold_trial SET status='failed',error=%s WHERE id=%s",
                       ('阈值试算失败。请用同一批留出样本重试。', trial_id))
            audit(db, None, 'worker', 'threshold_trial_failed', trial_id=trial_id,
                  error_type=type(exc).__name__)
            return
        print(f'trial={trial_id} eligible={trial_result(db, trial_id)}', flush=True)


def trial_result(db, trial_id):
    return db.execute('SELECT eligible FROM threshold_trial WHERE id=%s', (trial_id,)).fetchone()['eligible']


def recover_queue(redis):
    # PostgreSQL keeps the pending intent if a producer or worker exits before completion.
    with connection() as db:
        pending = db.execute("SELECT id FROM triage_case WHERE status='queued' ORDER BY id LIMIT 100").fetchall()
        trials = db.execute("SELECT id FROM threshold_trial WHERE status='running' ORDER BY id LIMIT 20").fetchall()
    if pending:
        redis.lpush(QUEUE, *(row['id'] for row in pending))
    if trials:
        redis.lpush(THRESHOLD_QUEUE, *(row['id'] for row in trials))


def main():
    initialize()
    with queue() as redis:
        while True:
            try:
                worked = False
                message = redis.brpop(QUEUE, timeout=1)
                if message:
                    worked = True
                    process(int(message[1]))
                message = redis.brpop(THRESHOLD_QUEUE, timeout=1)
                if message:
                    worked = True
                    process_trial(int(message[1]))
                if not worked:
                    recover_queue(redis)
                HEARTBEAT.touch()
            except (DatabaseError, RedisError, ValueError):
                logging.exception('Queue operation failed')
                time.sleep(2)


if __name__ == '__main__':
    main()
