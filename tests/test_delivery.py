import hashlib
from itertools import permutations

from delivery.export import export_delivery
from delivery.manifest import decode_manifest
from delivery.order import NoLegalOrder
from delivery.registry import DeliveryNotFound, publish_delivery, read_delivery
from delivery.selection import InvalidSelection
from mirror.apply import accept_batch
from mirror.read import read_mirror
from mirror.state import initialize_mirror
from sources.batches import BatchNotFound, register_batch
from sources.bootstrap import initialize_source
from sources.records import ObservationChange, OriginalBatch, SourceChange
from tests.support import LakeCase
from warehouse.baselines import BaselineNotFound
from warehouse.snapshots import EffectiveSource, Observation, make_snapshot


def switch_prefix_is_legal(prefix):
    # Closed form for this sample, independent of the production transition and planner.
    applied = set(prefix)
    x_is_available = 'A' not in applied or 'C' in applied
    y_is_available = 'B' in applied
    return ('B' not in applied or x_is_available) and ('C' not in applied or y_is_available)


class DeliveryTests(LakeCase):
    def test_dead_end_requires_another_legal_first_batch(self):
        db, baseline_id, _, _ = self.load_source()
        self.assertEqual([batch for batch in 'ABC' if switch_prefix_is_legal(batch)], ['A', 'B'])
        self.assertFalse(switch_prefix_is_legal('AB'))
        self.assertFalse(switch_prefix_is_legal('AC'))
        legal = [
            order for order in permutations('ABC')
            if all(switch_prefix_is_legal(order[:length]) for length in (1, 2, 3))
        ]
        self.assertEqual(legal, [('B', 'C', 'A')])
        delivery_id = publish_delivery(db, baseline_id, ('A', 'B', 'C'))
        manifest = read_delivery(db, delivery_id)
        self.assertEqual(tuple(batch.batch_id for batch in manifest.batches), legal[0])
        self.assertEqual(self.delivery_count(db), 1)

    def test_frozen_export(self):
        db, baseline_id, _, batches = self.load_source()
        delivery_id = publish_delivery(db, baseline_id, ('A', 'B', 'C'))
        package = export_delivery(db, delivery_id)
        expected_batch_order = (batches[1], batches[2], batches[0])
        expected_key = hashlib.sha256(b'{"observations":[],"sources":[["old-X","X"]]}').hexdigest()
        db.close()
        reopened = self.connection('source')
        self.assertEqual(export_delivery(reopened, delivery_id), package)
        manifest = decode_manifest(package)
        self.assertEqual((manifest.baseline_id, manifest.baseline_key), (baseline_id, expected_key))
        self.assertEqual(manifest.batches, expected_batch_order)
        self.assertEqual(manifest.delivery_id, delivery_id)
        self.assertEqual(publish_delivery(reopened, baseline_id, ('C', 'A', 'B')), delivery_id)
        with self.assertRaises(DeliveryNotFound):
            export_delivery(reopened, 'absent')
        self.assertEqual(self.delivery_count(reopened), 1)

    def test_overlapping_targets(self):
        db, baseline_id, _, _ = self.load_source()
        original_id = publish_delivery(db, baseline_id, ('A', 'B', 'C'))
        added = OriginalBatch('D', (ObservationChange('b-reading', 'upsert', 'X', 78),))
        register_batch(db, added)
        # D after B and before C preserves every referenced code, but changes B's target.
        self.assertTrue(all(switch_prefix_is_legal(prefix) for prefix in ('B', 'BD', 'BDC', 'BDCA')))
        with self.assertRaisesRegex(InvalidSelection, 'b-reading'):
            publish_delivery(db, baseline_id, ('A', 'B', 'C', 'D'))
        self.assertEqual(self.delivery_count(db), 1)
        self.assertEqual(read_delivery(db, original_id).delivery_id, original_id)

    def test_atomic_switch(self):
        db = self.connection('source')
        initial = make_snapshot([EffectiveSource('old', 'X')], [Observation('prior', 'X', 71)])
        batch = OriginalBatch('switch', (SourceChange('old', 'remove'), SourceChange('new', 'upsert', 'X')))
        initialize_source(db, 'start', initial, batches=(batch,))
        delivery_id = publish_delivery(db, 'start', ('switch',))
        package = export_delivery(db, delivery_id)
        self.assertEqual(decode_manifest(package).batches, (batch,))
        mirror = self.connection('mirror')
        initialize_mirror(mirror, 'start', initial)
        self.assertEqual(accept_batch(mirror, package, 'switch').position, 1)
        actual = read_mirror(mirror).snapshot
        self.assertEqual(actual.sources, (EffectiveSource('new', 'X'),))
        self.assertEqual(actual.observations, (Observation('prior', 'X', 71),))

    def test_mutual_dependencies_have_no_legal_first_batch(self):
        db, baseline_id, _, batches = self.load_source('interdependent')
        required = {'B': {'X'}, 'C': {'Y'}}
        provided = {'B': {'Y'}, 'C': {'X'}}
        self.assertLessEqual(required['B'] | required['C'], provided['B'] | provided['C'])
        self.assertEqual([key for key in required if required[key] <= provided[key]], [])
        with self.assertRaises(NoLegalOrder):
            publish_delivery(db, baseline_id, tuple(batch.batch_id for batch in batches))
        self.assertEqual(self.delivery_count(db), 0)

    def test_invalid_selection_and_eight_batch_limit(self):
        db = self.connection('source')
        baseline = make_snapshot([EffectiveSource('s', 'X')])
        batches = tuple(
            OriginalBatch(str(index), (ObservationChange(f'r-{index}', 'upsert', 'X', index),))
            for index in range(9)
        )
        initialize_source(db, 'start', baseline, batches=batches)
        ids = tuple(batch.batch_id for batch in batches)
        for base, selected, error in (
            ('start', (), InvalidSelection), ('start', ids, InvalidSelection),
            ('start', ('0', '0'), InvalidSelection), ('start', '0', InvalidSelection),
            ('missing', ('0',), BaselineNotFound), ('start', ('missing',), BatchNotFound),
        ):
            with self.subTest(base=base, selected=selected):
                with self.assertRaises(error):
                    publish_delivery(db, base, selected)
                self.assertEqual(self.delivery_count(db), 0)
        delivery_id = publish_delivery(db, 'start', reversed(ids[:8]))
        self.assertEqual(tuple(batch.batch_id for batch in read_delivery(db, delivery_id).batches), ids[:8])
        self.assertEqual(self.delivery_count(db), 1)
