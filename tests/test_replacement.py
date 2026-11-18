import json

from delivery.export import export_delivery
from delivery.manifest import (
    DeliveryManifest,
    InvalidManifest,
    ReplacementManifest,
    decode_manifest,
    encode_manifest,
    freeze_replacement,
)
from delivery.order import NoLegalOrder
from delivery.registry import DeliveryNotFound, publish_delivery, publish_replacement
from delivery.selection import InvalidSelection
from mirror.apply import BatchOutOfOrder, accept_batch
from mirror.imports import BatchOutsideDelivery, DeliveryBindingMismatch
from mirror.read import read_mirror
from mirror.state import initialize_mirror
from receipts.lookup import read_receipt
from replacement_examples import (
    BASELINE,
    BASELINE_ID,
    BATCHES,
    CORRECTED,
    ORIGINAL,
    PREFIX,
    check_replacement,
)
from sources.batches import BatchNotFound
from sources.bootstrap import initialize_source
from tests.support import LakeCase


class ReplacementSourceTests(LakeCase):
    def load_source(self):
        source = self.connection('source')
        initialize_source(source, BASELINE_ID, BASELINE, batches=BATCHES)
        return source

    def publish_parent(self, source, middle='P'):
        delivery_id = publish_delivery(source, BASELINE_ID, ('U', middle, 'A'))
        return delivery_id, export_delivery(source, delivery_id)

    def test_publish_binds_parent_prefix_and_replans_from_it(self):
        source = self.load_source()
        parent_id, parent_package = self.publish_parent(source)
        child_id = publish_replacement(source, parent_id, 2, ('D', 'C'))
        package = export_delivery(source, child_id)
        manifest = decode_manifest(package)
        self.assertIs(type(manifest), ReplacementManifest)
        self.assertEqual(
            (manifest.baseline_id, manifest.parent_delivery_id, manifest.parent_position),
            (BASELINE_ID, parent_id, 2),
        )
        self.assertEqual(tuple(batch.batch_id for batch in manifest.batches), ('C', 'D'))
        self.assertNotEqual(child_id, parent_id)
        self.assertEqual(export_delivery(source, parent_id), parent_package)
        self.assertIs(type(decode_manifest(parent_package)), DeliveryManifest)
        self.assertEqual(self.delivery_count(source), 2)
        source.close()
        reopened = self.connection('source')
        self.assertEqual(publish_replacement(reopened, parent_id, 2, ('C', 'D')), child_id)
        self.assertEqual(export_delivery(reopened, child_id), package)
        self.assertEqual(export_delivery(reopened, parent_id), parent_package)
        self.assertEqual(self.delivery_count(reopened), 2)

    def test_parent_must_be_an_applicable_normal_delivery(self):
        source = self.load_source()
        parent_id, _ = self.publish_parent(source)
        child_id = publish_replacement(source, parent_id, 2, ('C', 'D'))
        with self.assertRaises(DeliveryNotFound):
            publish_replacement(source, 'absent', 1, ('C',))
        with self.assertRaises(ValueError):
            publish_replacement(source, child_id, 1, ('D',))
        for position in (0, 3, 4, -1, True, '2', 2.0, None):
            with self.subTest(position=position), self.assertRaises(ValueError):
                publish_replacement(source, parent_id, position, ('C',))
        self.assertEqual(self.delivery_count(source), 2)

    def test_selection_failures_publish_nothing(self):
        source = self.load_source()
        parent_id, _ = self.publish_parent(source)
        with self.assertRaises(NoLegalOrder):
            publish_replacement(source, parent_id, 2, ('Z',))
        for selected in (('A',), ('P',), ('C', 'A')):
            with self.subTest(selected=selected), self.assertRaises(InvalidSelection):
                publish_replacement(source, parent_id, 2, selected)
        with self.assertRaises(BatchNotFound):
            publish_replacement(source, parent_id, 2, ('absent',))
        for selected in ((), ('C', 'C'), 'C'):
            with self.subTest(selected=selected), self.assertRaises(InvalidSelection):
                publish_replacement(source, parent_id, 2, selected)
        self.assertEqual(self.delivery_count(source), 1)

    def test_unsigned_parent_remainder_may_be_selected(self):
        source = self.load_source()
        parent_id, _ = self.publish_parent(source)
        child_id = publish_replacement(source, parent_id, 2, ('U',))
        manifest = decode_manifest(export_delivery(source, child_id))
        self.assertEqual(tuple(batch.batch_id for batch in manifest.batches), ('U',))
        self.assertEqual(self.delivery_count(source), 2)

    def test_replacement_manifest_round_trip_and_tamper(self):
        source = self.load_source()
        parent_id, _ = self.publish_parent(source)
        child_id = publish_replacement(source, parent_id, 2, ('D', 'C'))
        package = export_delivery(source, child_id)
        self.assertEqual(encode_manifest(decode_manifest(package)), package)
        for field, value in (('parent_position', 1), ('parent_delivery_id', parent_id + 'x'),
                             ('prefix_key', '0' * 64)):
            document = json.loads(package)
            document[field] = value
            with self.subTest(field=field), self.assertRaises(InvalidManifest):
                decode_manifest(json.dumps(document))


class ReplacementMirrorTests(LakeCase):
    def setUp(self):
        super().setUp()
        source = self.connection('source')
        initialize_source(source, BASELINE_ID, BASELINE, batches=BATCHES)
        self.parent_id = publish_delivery(source, BASELINE_ID, ('U', 'P', 'A'))
        self.parent_package = export_delivery(source, self.parent_id)
        self.child_id = publish_replacement(source, self.parent_id, 2, ('D', 'C'))
        self.child_package = export_delivery(source, self.child_id)
        source.close()

    def signed_mirror(self, name='mirror', batches=('A', 'P')):
        mirror = self.connection(name)
        initialize_mirror(mirror, BASELINE_ID, BASELINE)
        receipts = [accept_batch(mirror, self.parent_package, batch_id) for batch_id in batches]
        return mirror, receipts

    def test_takeover_requires_the_exact_parent_prefix(self):
        fresh = self.connection('fresh')
        initialize_mirror(fresh, BASELINE_ID, BASELINE)
        before = read_mirror(fresh)
        with self.assertRaises(DeliveryBindingMismatch):
            accept_batch(fresh, self.child_package, 'C')
        self.assertEqual(read_mirror(fresh), before)

        late, _ = self.signed_mirror('late', ('A', 'P', 'U'))
        before = read_mirror(late)
        self.assertEqual(before.snapshot, ORIGINAL)
        with self.assertRaises(DeliveryBindingMismatch):
            accept_batch(late, self.child_package, 'C')
        self.assertEqual(read_mirror(late), before)

    def test_first_replacement_batch_and_single_takeover(self):
        mirror, (first, second) = self.signed_mirror()
        before = read_mirror(mirror)
        self.assertEqual(before.snapshot, PREFIX)
        with self.assertRaises(BatchOutOfOrder):
            accept_batch(mirror, self.child_package, 'D')
        with self.assertRaises(BatchOutsideDelivery):
            accept_batch(mirror, self.child_package, 'U')
        self.assertEqual(read_mirror(mirror), before)

        corrected = accept_batch(mirror, self.child_package, 'C')
        self.assertEqual(
            (corrected.delivery_id, corrected.batch_id, corrected.position), (self.child_id, 'C', 1)
        )
        view = read_mirror(mirror)
        self.assertEqual(
            (view.cursor.delivery_id, view.cursor.position, view.snapshot),
            (self.child_id, 1, CORRECTED),
        )
        self.assertEqual(accept_batch(mirror, self.parent_package, 'A'), first)
        self.assertEqual(accept_batch(mirror, self.parent_package, 'P'), second)
        self.assertEqual(accept_batch(mirror, self.child_package, 'C'), corrected)
        with self.assertRaises(DeliveryBindingMismatch):
            accept_batch(mirror, self.parent_package, 'U')
        crafted = encode_manifest(
            freeze_replacement(BASELINE_ID, BASELINE, self.child_id, 1, CORRECTED, (BATCHES[5],))
        )
        with self.assertRaises(DeliveryBindingMismatch):
            accept_batch(mirror, crafted, 'D')
        self.assertEqual(read_mirror(mirror), view)

    def test_reopen_resumes_replacement_positions(self):
        mirror, (first, second) = self.signed_mirror()
        corrected = accept_batch(mirror, self.child_package, 'C')
        mirror.close()
        reopened = self.connection('mirror')
        resumed = accept_batch(reopened, self.child_package, 'D')
        self.assertEqual(
            (resumed.delivery_id, resumed.batch_id, resumed.position), (self.child_id, 'D', 2)
        )
        view = read_mirror(reopened)
        self.assertEqual(
            (view.cursor.delivery_id, view.cursor.position, view.snapshot),
            (self.child_id, 2, CORRECTED),
        )
        for receipt in (first, second, corrected, resumed):
            self.assertEqual(read_receipt(reopened, receipt.receipt_id), receipt)
        self.assertEqual(accept_batch(reopened, self.parent_package, 'P'), second)
        self.assertEqual(read_mirror(reopened), view)

    def test_full_handover_example(self):
        handoff = self.directory / 'handoff'
        handoff.mkdir()
        check_replacement(handoff)
