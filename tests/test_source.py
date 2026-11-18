import json

from delivery.export import export_delivery
from delivery.registry import publish_delivery
from sources.batches import BatchConflict, BatchNotFound, read_batch, register_batch
from sources.bootstrap import initialize_source
from sources.identity import decode_batch, encode_batch
from sources.records import ObservationChange, OriginalBatch, SourceChange
from sources.validation import InvalidBatch
from tests.support import LakeCase
from warehouse.baselines import BaselineConflict, BaselineNotFound, read_baseline, register_baseline
from warehouse.eligibility import MissingEffectiveSource
from warehouse.snapshots import EffectiveSource, Observation, WarehouseSnapshot, make_snapshot


class SourceTests(LakeCase):
    def test_baseline_read(self):
        db = self.connection('source')
        unordered = WarehouseSnapshot(
            (EffectiveSource('z', 'X'), EffectiveSource('a', 'X')),
            (Observation('z', 'X', 9007199254740993), Observation('a', 'X', 0)),
        )
        self.assertEqual(register_baseline(db, 'morning', unordered), 'morning')
        returned = read_baseline(db, 'morning')
        self.assertEqual(returned.sources, (EffectiveSource('a', 'X'), EffectiveSource('z', 'X')))
        self.assertEqual(returned.observations, (Observation('a', 'X', 0), Observation('z', 'X', 9007199254740993)))
        with self.assertRaises(MissingEffectiveSource):
            register_baseline(db, 'invalid', make_snapshot(observations=[Observation('r', 'X', 5)]))
        for absent in ('unknown', 'invalid'):
            with self.assertRaises(BaselineNotFound):
                read_baseline(db, absent)

    def test_initialize_again(self):
        db, baseline_id, snapshot, batches = self.load_source()
        extra = OriginalBatch('later', (SourceChange('other', 'upsert', 'Z'),))
        register_batch(db, extra)
        delivery_id = publish_delivery(db, baseline_id, ('A', 'B', 'C'))
        package = export_delivery(db, delivery_id)
        self.assertEqual(initialize_source(db, baseline_id, snapshot, batches=batches), baseline_id)
        with self.assertRaises(BaselineConflict):
            initialize_source(db, baseline_id, make_snapshot())
        self.assertEqual(read_baseline(db, baseline_id), snapshot)
        self.assertEqual(read_batch(db, 'later'), extra)
        self.assertEqual(export_delivery(db, delivery_id), package)
        self.assertEqual(self.delivery_count(db), 1)

    def test_sample_conflict_keeps_user_data(self):
        db = self.connection('source')
        baseline = make_snapshot([EffectiveSource('old-X', 'X')])
        initialize_source(db, 'morning', baseline)
        user_batch = OriginalBatch('B', (ObservationChange('user-reading', 'upsert', 'X', 9),))
        register_batch(db, user_batch)
        from lake_checks import load_sample

        baseline_id, snapshot, sample_batches = load_sample('switch')
        with self.assertRaises(BatchConflict):
            initialize_source(db, baseline_id, snapshot, batches=sample_batches)
        self.assertEqual(read_batch(db, 'B'), user_batch)
        with self.assertRaises(BatchNotFound):
            read_batch(db, 'A')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM original_batches').fetchone(), (1,))

    def test_same_content_retry(self):
        db = self.connection('source')
        initialize_source(db, 'empty', make_snapshot())
        batch = OriginalBatch('B', (SourceChange('source', 'upsert', 'X'),))
        self.assertEqual(register_batch(db, batch), 'B')
        reordered_wire = json.dumps(json.loads(encode_batch(batch)), sort_keys=True, indent=2)
        self.assertEqual(register_batch(db, decode_batch(reordered_wire)), 'B')
        self.assertEqual(read_batch(db, 'B'), batch)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM original_batches').fetchone(), (1,))

    def test_typed_edits(self):
        db = self.connection('source')
        initialize_source(db, 'empty', make_snapshot())
        batch = OriginalBatch('zero', (
            SourceChange('same-id', 'upsert', 'X'), ObservationChange('same-id', 'upsert', 'X', 0),
        ))
        register_batch(db, batch)
        self.assertEqual(read_batch(db, 'zero').changes[1].value, 0)
        self.assertIs(type(read_batch(db, 'zero').changes[1].value), int)
        for bad in (ObservationChange('r', 'upsert', 'X'), ObservationChange('r', 'upsert', 'X', True),
                    ObservationChange('r', 'upsert', None, 4), SourceChange('s', 'remove', 'X'),
                    SourceChange('s', 'upsert', ' ')):
            with self.subTest(change=bad), self.assertRaises(InvalidBatch):
                register_batch(db, OriginalBatch('invalid', (bad,)))
        for value in ({}, {'value': None}, {'value': False}):
            wire = {'id': 'invalid', 'changes': [dict(type='observation', id='r', op='upsert', code='X', **value)]}
            with self.subTest(wire=wire), self.assertRaises(InvalidBatch):
                decode_batch(json.dumps(wire))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM original_batches').fetchone(), (1,))

    def test_conflicting_retry(self):
        db = self.connection('source')
        original = OriginalBatch('B', (ObservationChange('reading', 'upsert', 'X', 44),))
        register_batch(db, original)
        with self.assertRaises(BatchConflict):
            register_batch(db, OriginalBatch('B', (ObservationChange('reading', 'upsert', 'X', 45),)))
        self.assertEqual(read_batch(db, 'B'), original)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM original_batches').fetchone(), (1,))

    def test_precise_retry(self):
        db = self.connection('source')
        original = OriginalBatch('B', (ObservationChange('reading', 'upsert', 'X', 9007199254740992),))
        later = OriginalBatch('B', (ObservationChange('reading', 'upsert', 'X', 9007199254740993),))
        register_batch(db, original)
        with self.assertRaises(BatchConflict):
            register_batch(db, later)
        db.close()
        reopened = self.connection('source')
        self.assertEqual(read_batch(reopened, 'B').changes[0].value, 9007199254740992)
        self.assertEqual(reopened.execute('SELECT COUNT(*) FROM original_batches').fetchone(), (1,))

    def test_change_order_is_part_of_original_content(self):
        db = self.connection('source')
        changes = (SourceChange('s', 'upsert', 'X'), SourceChange('s', 'remove'))
        register_batch(db, OriginalBatch('B', changes))
        with self.assertRaises(BatchConflict):
            register_batch(db, OriginalBatch('B', changes[::-1]))
        self.assertEqual(read_batch(db, 'B').changes, changes)
