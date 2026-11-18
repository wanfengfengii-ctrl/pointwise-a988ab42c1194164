import json

from delivery.export import export_delivery
from delivery.manifest import InvalidManifest, encode_manifest, freeze_manifest
from delivery.registry import publish_delivery
from lake_checks import independent_switch_package, load_sample
from mirror.apply import BatchOutOfOrder, accept_batch
from mirror.imports import BatchOutsideDelivery, DeliveryBindingMismatch
from mirror.read import read_mirror
from mirror.state import MirrorNotReady, initialize_mirror
from receipts.lookup import read_receipt
from sources.bootstrap import initialize_source
from sources.records import ObservationChange, OriginalBatch, SourceChange
from tests.support import LakeCase
from warehouse.eligibility import MissingEffectiveSource
from warehouse.snapshots import EffectiveSource, Observation, make_snapshot


class MirrorTests(LakeCase):
    def open_switch_mirror(self):
        baseline_id, baseline, _ = load_sample('switch')
        mirror = self.connection('mirror')
        initialize_mirror(mirror, baseline_id, baseline)
        return mirror

    def test_closed_source_handoff(self):
        source, baseline_id, baseline, _ = self.load_source()
        delivery_id = publish_delivery(source, baseline_id, ('A', 'B', 'C'))
        package = export_delivery(source, delivery_id)
        source.close()
        mirror = self.connection('mirror')
        initialize_mirror(mirror, baseline_id, baseline)
        expected = (
            ('B', (('old-X', 'X'), ('y-origin', 'Y')), (('b-reading', 'X', 44),)),
            ('C', (('new-X', 'X'), ('old-X', 'X'), ('y-origin', 'Y')),
             (('b-reading', 'X', 44), ('c-reading', 'Y', 17))),
            ('A', (('new-X', 'X'), ('y-origin', 'Y')),
             (('b-reading', 'X', 44), ('c-reading', 'Y', 17))),
        )
        for position, (batch_id, sources, observations) in enumerate(expected, 1):
            with self.subTest(batch=batch_id):
                receipt = accept_batch(mirror, package, batch_id)
                view = read_mirror(mirror)
                self.assertEqual(view.state_id, f'{delivery_id}:{position}')
                self.assertEqual((view.cursor.delivery_id, view.cursor.position), (delivery_id, position))
                self.assertEqual(view.snapshot.sources, tuple(EffectiveSource(*item) for item in sources))
                self.assertEqual(view.snapshot.observations, tuple(Observation(*item) for item in observations))
                self.assertEqual((receipt.delivery_id, receipt.batch_id, receipt.position), (delivery_id, batch_id, position))
                self.assertEqual(read_receipt(mirror, receipt.receipt_id), receipt)
                self.assertEqual(self.receipt_count(mirror), position)

    def test_first_prefix_from_independent_package(self):
        mirror = self.open_switch_mirror()
        receipt = accept_batch(mirror, independent_switch_package(), 'B')
        view = read_mirror(mirror)
        self.assertEqual((view.cursor.position, receipt.position, receipt.batch_id), (1, 1, 'B'))
        self.assertEqual(view.snapshot.sources, (EffectiveSource('old-X', 'X'), EffectiveSource('y-origin', 'Y')))
        self.assertEqual(view.snapshot.observations, (Observation('b-reading', 'X', 44),))
        self.assertEqual(self.receipt_count(mirror), 1)
        self.assertEqual(read_receipt(mirror, receipt.receipt_id), receipt)

    def test_shared_code_survives_old_source_removal(self):
        mirror = self.open_switch_mirror()
        package = independent_switch_package()
        accept_batch(mirror, package, 'B')
        accept_batch(mirror, package, 'C')
        self.assertEqual(
            tuple(row.source_id for row in read_mirror(mirror).snapshot.sources if row.code == 'X'),
            ('new-X', 'old-X'),
        )
        self.assertEqual(accept_batch(mirror, package, 'A').position, 3)
        snapshot = read_mirror(mirror).snapshot
        self.assertEqual(tuple(row.source_id for row in snapshot.sources if row.code == 'X'), ('new-X',))
        self.assertIn(Observation('b-reading', 'X', 44), snapshot.observations)
        self.assertEqual(self.receipt_count(mirror), 3)

    def test_wrong_baseline_or_eligible_skip_preserves_state(self):
        baseline_id, baseline, _ = load_sample('switch')
        package = independent_switch_package()
        other = self.connection('other-baseline')
        initialize_mirror(other, baseline_id + '-other', baseline)
        before = read_mirror(other)
        with self.assertRaises(DeliveryBindingMismatch):
            accept_batch(other, package, 'B')
        self.assertEqual(read_mirror(other), before)
        self.assertEqual(self.receipt_count(other), 0)
        mirror = self.open_switch_mirror()
        before = read_mirror(mirror)
        self.assertEqual(before.snapshot.observations, ())
        with self.assertRaises(BatchOutOfOrder):
            accept_batch(mirror, package, 'A')
        with self.assertRaises(BatchOutsideDelivery):
            accept_batch(mirror, package, 'absent')
        self.assertEqual(read_mirror(mirror), before)
        self.assertIsNone(read_mirror(mirror).cursor.delivery_id)
        self.assertEqual(self.receipt_count(mirror), 0)

    def test_same_named_baseline_with_different_content(self):
        baseline_id, _, _ = load_sample('switch')
        changed = make_snapshot([EffectiveSource('old-X', 'X')], [Observation('existing', 'X', 38)])
        mirror = self.connection('mirror')
        initialize_mirror(mirror, baseline_id, changed)
        before = read_mirror(mirror)
        with self.assertRaises(DeliveryBindingMismatch):
            accept_batch(mirror, independent_switch_package(), 'B')
        self.assertEqual(read_mirror(mirror), before)
        self.assertEqual(self.receipt_count(mirror), 0)

    def test_package_binding_mismatch(self):
        mirror = self.open_switch_mirror()
        document = json.loads(independent_switch_package())
        document['batches'][0]['changes'][1]['value'] = '0x2d'
        before = read_mirror(mirror)
        with self.assertRaises(InvalidManifest):
            accept_batch(mirror, json.dumps(document), 'B')
        self.assertEqual(read_mirror(mirror), before)
        self.assertIsNone(read_mirror(mirror).cursor.delivery_id)
        self.assertEqual(self.receipt_count(mirror), 0)

    def test_wrong_delivery_after_binding_preserves_original_receipt(self):
        mirror = self.open_switch_mirror()
        package = independent_switch_package()
        receipt = accept_batch(mirror, package, 'B')
        baseline_id, baseline, batches = load_sample('switch')
        other_package = encode_manifest(freeze_manifest(baseline_id, baseline, (batches[1],)))
        before = read_mirror(mirror)
        with self.assertRaises(DeliveryBindingMismatch):
            accept_batch(mirror, other_package, 'B')
        self.assertEqual(read_mirror(mirror), before)
        self.assertEqual(read_receipt(mirror, receipt.receipt_id), receipt)
        self.assertEqual(self.receipt_count(mirror), 1)

    def test_invalid_next_batch_keeps_committed_prefix(self):
        baseline = make_snapshot([EffectiveSource('old-X', 'X')], [Observation('prior', 'X', 7)])
        a = OriginalBatch('first', (SourceChange('y-origin', 'upsert', 'Y'),))
        b = OriginalBatch('second', (SourceChange('old-X', 'remove'),))
        manifest = freeze_manifest('start', baseline, (a, b))
        package = encode_manifest(manifest)
        mirror = self.connection('mirror')
        initialize_mirror(mirror, 'start', baseline)
        receipt = accept_batch(mirror, package, 'first')
        before = read_mirror(mirror)
        with self.assertRaises(MissingEffectiveSource) as failed:
            accept_batch(mirror, package, 'second')
        self.assertEqual(failed.exception.codes, ('X',))
        self.assertEqual(read_mirror(mirror), before)
        self.assertEqual(self.receipt_count(mirror), 1)
        self.assertEqual(read_receipt(mirror, receipt.receipt_id), receipt)

    def test_uninitialized(self):
        mirror = self.connection('blank')
        with self.assertRaises(MirrorNotReady):
            read_mirror(mirror)
        with self.assertRaises(MirrorNotReady):
            accept_batch(mirror, independent_switch_package(), 'B')
        with self.assertRaises(MirrorNotReady):
            read_mirror(mirror)
        self.assertEqual(mirror.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(), [])

    def test_exact_integer_projection_and_ordered_corrections(self):
        source = self.connection('numeric-source')
        baseline = make_snapshot([EffectiveSource('x', 'X')], [Observation('withdrawn', 'X', 9)])
        value = -(10 ** 5000)
        batch = OriginalBatch('readings', (
            ObservationChange('unchanged-size', 'upsert', 'X', 9007199254740993),
            ObservationChange('large', 'upsert', 'X', 14),
            ObservationChange('large', 'upsert', 'X', value),
            ObservationChange('zero', 'upsert', 'X', 0),
            ObservationChange('withdrawn', 'remove'),
        ))
        initialize_source(source, 'start', baseline, batches=(batch,))
        package = export_delivery(source, publish_delivery(source, 'start', ('readings',)))
        source.close()
        mirror = self.connection('mirror')
        initialize_mirror(mirror, 'start', baseline)
        accept_batch(mirror, package, 'readings')
        mirror.close()
        reopened = self.connection('mirror')
        actual = read_mirror(reopened).snapshot.observations
        self.assertEqual(tuple(item.observation_id for item in actual), ('large', 'unchanged-size', 'zero'))
        self.assertTrue(actual[0].value == value)
        self.assertEqual(actual[1].value, 9007199254740993)
        self.assertEqual(actual[2].value, 0)
        self.assertTrue(all(type(item.value) is int for item in actual))
