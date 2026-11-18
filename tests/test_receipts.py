import sqlite3

from lake_checks import independent_switch_package, load_sample
from mirror.apply import accept_batch
from mirror.read import read_mirror
from mirror.state import MirrorInitializationConflict, initialize_mirror
from receipts.lookup import ReceiptNotFound, read_receipt
from tests.support import LakeCase
from warehouse.snapshots import EffectiveSource, Observation, make_snapshot


class ReceiptTests(LakeCase):
    def initialized(self, name='mirror'):
        baseline_id, baseline, _ = load_sample('switch')
        db = self.connection(name)
        initialize_mirror(db, baseline_id, baseline)
        return db

    def test_rollback_after_receipt_at_commit(self):
        mirror = self.initialized()
        mirror.execute('PRAGMA foreign_keys = ON')
        mirror.execute('CREATE TABLE approvals (id INTEGER PRIMARY KEY)')
        mirror.execute('''CREATE TABLE pending_approvals (
            receipt_id TEXT PRIMARY KEY, approval_id INTEGER NOT NULL,
            FOREIGN KEY (approval_id) REFERENCES approvals(id) DEFERRABLE INITIALLY DEFERRED
        )''')
        mirror.execute('''CREATE TRIGGER require_approval_at_commit AFTER INSERT ON mirror_receipts
            BEGIN INSERT INTO pending_approvals VALUES (NEW.receipt_id, 1); END''')
        package = independent_switch_package()
        before = read_mirror(mirror)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY'):
            accept_batch(mirror, package, 'B')
        self.assertFalse(mirror.in_transaction)
        self.assertEqual(read_mirror(mirror), before)
        self.assertEqual(self.receipt_count(mirror), 0)
        self.assertEqual(mirror.execute('SELECT COUNT(*) FROM pending_approvals').fetchone(), (0,))
        mirror.close()

        reopened = self.connection('mirror')
        reopened.execute('PRAGMA foreign_keys = ON')
        self.assertEqual(read_mirror(reopened), before)
        self.assertIsNone(read_mirror(reopened).cursor.delivery_id)
        self.assertEqual(self.receipt_count(reopened), 0)
        reopened.execute('INSERT INTO approvals VALUES (1)')
        receipt = accept_batch(reopened, package, 'B')
        view = read_mirror(reopened)
        self.assertEqual((view.cursor.delivery_id, view.cursor.position), (receipt.delivery_id, 1))
        self.assertEqual(view.snapshot.observations, (Observation('b-reading', 'X', 44),))
        self.assertEqual(view.snapshot.sources, (EffectiveSource('old-X', 'X'), EffectiveSource('y-origin', 'Y')))
        self.assertEqual(read_receipt(reopened, receipt.receipt_id), receipt)
        self.assertEqual(accept_batch(reopened, package, 'B'), receipt)
        self.assertEqual(self.receipt_count(reopened), 1)
        self.assertEqual(reopened.execute('SELECT COUNT(*) FROM pending_approvals').fetchone(), (1,))

    def test_reopen_retry_and_resume_matches_continuous_receiving(self):
        package = independent_switch_package()
        mirror = self.initialized()
        receipt = accept_batch(mirror, package, 'B')
        before = read_mirror(mirror)
        mirror.close()
        reopened = self.connection('mirror')
        self.assertEqual(accept_batch(reopened, package, 'B'), receipt)
        self.assertEqual(read_mirror(reopened), before)
        self.assertEqual(self.receipt_count(reopened), 1)
        resumed = [receipt]
        for batch_id in ('C', 'A'):
            resumed.append(accept_batch(reopened, package, batch_id))

        continuous = self.initialized('continuous')
        uninterrupted = [accept_batch(continuous, package, batch_id) for batch_id in ('B', 'C', 'A')]
        self.assertEqual(resumed, uninterrupted)
        self.assertEqual(read_mirror(reopened), read_mirror(continuous))
        end = read_mirror(reopened)
        self.assertEqual(end.cursor.position, 3)
        self.assertEqual(end.snapshot.sources, (EffectiveSource('new-X', 'X'), EffectiveSource('y-origin', 'Y')))
        self.assertEqual(end.snapshot.observations, (Observation('b-reading', 'X', 44), Observation('c-reading', 'Y', 17)))
        self.assertEqual(self.receipt_count(reopened), 3)

    def test_old_receipt_after_progress(self):
        package = independent_switch_package()
        mirror = self.initialized()
        first = accept_batch(mirror, package, 'B')
        second = accept_batch(mirror, package, 'C')
        self.assertNotEqual(first.receipt_id, second.receipt_id)
        before = read_mirror(mirror)
        mirror.close()
        reopened = self.connection('mirror')
        retried = accept_batch(reopened, package, 'B')
        self.assertEqual(retried, first)
        self.assertEqual((retried.batch_id, retried.position), ('B', 1))
        self.assertEqual(read_receipt(reopened, first.receipt_id), first)
        self.assertEqual(read_receipt(reopened, second.receipt_id), second)
        self.assertEqual(read_mirror(reopened), before)
        self.assertEqual(read_mirror(reopened).cursor.position, 2)
        self.assertEqual(self.receipt_count(reopened), 2)

    def test_reinitialize_keeps_prefix_and_original_baseline_binding(self):
        package = independent_switch_package()
        baseline_id, baseline, _ = load_sample('switch')
        mirror = self.initialized()
        receipt = accept_batch(mirror, package, 'B')
        before = read_mirror(mirror)
        self.assertEqual(initialize_mirror(mirror, baseline_id, baseline), before.cursor)
        with self.assertRaises(MirrorInitializationConflict):
            initialize_mirror(mirror, baseline_id, make_snapshot())
        self.assertEqual(read_mirror(mirror), before)
        self.assertEqual(read_receipt(mirror, receipt.receipt_id), receipt)
        self.assertEqual(self.receipt_count(mirror), 1)
        mirror.close()
        reopened = self.connection('mirror')
        self.assertEqual(accept_batch(reopened, package, 'C').position, 2)
        self.assertEqual(self.receipt_count(reopened), 2)

    def test_unknown_receipt(self):
        mirror = self.initialized()
        with self.assertRaises(ReceiptNotFound):
            read_receipt(mirror, 'unknown')
        receipt = accept_batch(mirror, independent_switch_package(), 'B')
        with self.assertRaises(ReceiptNotFound):
            read_receipt(mirror, 'unknown')
        self.assertEqual(read_receipt(mirror, receipt.receipt_id), receipt)
        self.assertEqual(self.receipt_count(mirror), 1)
