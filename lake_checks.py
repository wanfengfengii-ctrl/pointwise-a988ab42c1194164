import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

from delivery.export import export_delivery
from delivery.manifest import decode_manifest
from delivery.order import NoLegalOrder
from delivery.registry import publish_delivery
from mirror.apply import BatchOutOfOrder, accept_batch
from mirror.read import MirrorView, read_mirror
from mirror.state import initialize_mirror
from receipts.ledger import Receipt
from receipts.lookup import read_receipt
from sources.batches import register_batch
from sources.bootstrap import initialize_source
from sources.identity import batch_from_document
from warehouse.baselines import read_baseline
from warehouse.eligibility import MissingEffectiveSource
from warehouse.snapshots import decode_snapshot


SWITCH_ORDER = ('B', 'C', 'A')
SWITCH_PREFIXES = [
    {'position': 1, 'sources': [['old-X', 'X'], ['y-origin', 'Y']],
     'observations': [['b-reading', 'X', 44]], 'receipt': ['B', 1]},
    {'position': 2, 'sources': [['new-X', 'X'], ['old-X', 'X'], ['y-origin', 'Y']],
     'observations': [['b-reading', 'X', 44], ['c-reading', 'Y', 17]], 'receipt': ['C', 2]},
    {'position': 3, 'sources': [['new-X', 'X'], ['y-origin', 'Y']],
     'observations': [['b-reading', 'X', 44], ['c-reading', 'Y', 17]], 'receipt': ['A', 3]},
]


def sample_document(name: str) -> dict:
    filenames = {'switch': 'switch.json', 'interdependent': 'interdependent.json'}
    path = Path(__file__).with_name('samples') / filenames[name]
    return json.loads(path.read_text(encoding='utf-8'))


def load_sample(name: str):
    document = sample_document(name)
    baseline = decode_snapshot(json.dumps(document['baseline']))
    batches = tuple(batch_from_document(batch) for batch in document['batches'])
    return document['baseline_id'], baseline, batches


def independent_switch_package() -> str:
    """Construct the hand-ordered example directly from the sample and wire format."""
    document = sample_document('switch')
    batches = {batch['id']: batch for batch in document['batches']}
    baseline_text = json.dumps(document['baseline'], sort_keys=True, separators=(',', ':'))
    body = {
        'version': 1,
        'baseline_id': document['baseline_id'],
        'baseline_key': hashlib.sha256(baseline_text.encode('ascii')).hexdigest(),
        'batches': [batches[batch_id] for batch_id in SWITCH_ORDER],
    }
    body_text = json.dumps(body, sort_keys=True, separators=(',', ':'))
    delivery_id = 'delivery-' + hashlib.sha256(body_text.encode('ascii')).hexdigest()
    return json.dumps({'delivery_id': delivery_id, **body}, sort_keys=True, separators=(',', ':'))


def prefix_evidence(view: MirrorView, receipt: Receipt) -> dict:
    return {
        'position': view.cursor.position,
        'sources': [[item.source_id, item.code] for item in view.snapshot.sources],
        'observations': [[item.observation_id, item.code, item.value] for item in view.snapshot.observations],
        'receipt': [receipt.batch_id, receipt.position],
    }


def _publish_switch(path: Path):
    baseline_id, baseline, batches = load_sample('switch')
    with closing(sqlite3.connect(path)) as source:
        initialize_source(source, baseline_id, baseline)
        for batch in batches:
            register_batch(source, batch)
        register_batch(source, batches[1])
        delivery_id = publish_delivery(source, baseline_id, tuple(batch.batch_id for batch in batches))
        frozen = export_delivery(source, delivery_id)
        return read_baseline(source, baseline_id), frozen


def incident(directory: Path) -> int:
    problems = []
    received = None
    order = None
    try:
        _, exported = _publish_switch(directory / 'source.sqlite')
        manifest = decode_manifest(exported)
        order = tuple(batch.batch_id for batch in manifest.batches)
        if order != SWITCH_ORDER:
            problems.append(f'source froze order {order!r}')
    except NoLegalOrder:
        problems.append('source rejected a complete legal order')

    baseline_id, baseline, _ = load_sample('switch')
    fixed = independent_switch_package()
    with closing(sqlite3.connect(directory / 'mirror.sqlite')) as mirror:
        initialize_mirror(mirror, baseline_id, baseline)
        try:
            receipt = accept_batch(mirror, fixed, 'B')
        except (BatchOutOfOrder, MissingEffectiveSource) as exc:
            problems.append(f'mirror rejected the legal first batch ({exc})')
        else:
            view = read_mirror(mirror)
            received = prefix_evidence(view, receipt)
            count = mirror.execute('SELECT COUNT(*) FROM mirror_receipts').fetchone()[0]
            if received != SWITCH_PREFIXES[0] or count != 1:
                problems.append(f'mirror committed {received!r} with {count} receipts after B')
            else:
                looked_up = read_receipt(mirror, receipt.receipt_id)
                if looked_up != receipt or view.cursor.delivery_id != receipt.delivery_id:
                    problems.append('mirror receipt does not identify its committed delivery')
    if problems:
        print('LAKE_HANDOFF_PREFIX_MISMATCH ' + ' | '.join(problems), file=sys.stderr)
        return 1
    print(json.dumps({'planned_order': order, 'first_prefix': received}, ensure_ascii=False))
    return 0


def demo(directory: Path) -> None:
    baseline, package = _publish_switch(directory / 'source.sqlite')
    manifest = decode_manifest(package)
    order = [batch.batch_id for batch in manifest.batches]
    if order != list(SWITCH_ORDER):
        raise AssertionError(f'unexpected delivery order {order!r}')
    mirror_path = directory / 'mirror.sqlite'
    actual = []
    with closing(sqlite3.connect(mirror_path)) as mirror:
        initialize_mirror(mirror, manifest.baseline_id, baseline)
    first_receipt = None
    for batch_id in order:
        with closing(sqlite3.connect(mirror_path)) as mirror:
            if batch_id == 'A':
                before_retry = read_mirror(mirror)
                if accept_batch(mirror, package, 'B') != first_receipt or read_mirror(mirror) != before_retry:
                    raise AssertionError('reopened mirror did not preserve the old B receipt and current prefix')
            receipt = accept_batch(mirror, package, batch_id)
            if read_receipt(mirror, receipt.receipt_id) != receipt:
                raise AssertionError('receipt differs from its persisted record')
            if batch_id == 'B':
                first_receipt = receipt
            actual.append(prefix_evidence(read_mirror(mirror), receipt))
    if actual != SWITCH_PREFIXES:
        raise AssertionError(f'unexpected mirror prefixes {actual!r}')
    print(f'交付 {manifest.delivery_id}')
    for evidence in actual:
        print(json.dumps(evidence, ensure_ascii=False))
    print('POINTWISE_SCENARIO ' + json.dumps({
        'case_id': 'v5',
        'input': sample_document('switch'),
        'expected': {'order': list(SWITCH_ORDER)},
        'actual': {'order': order},
        'downstream_expected': SWITCH_PREFIXES,
        'downstream_actual': actual,
        'code_paths': ['delivery/order.py', 'delivery/registry.py', 'mirror/apply.py', 'mirror/read.py', 'receipts/lookup.py'],
        'oracle': '手算。B 依赖旧 X，C 依赖 B 提供的 Y，C 接替 X 后才能停用旧出处。核对各次签收时的完整出处和读值。',
    }, ensure_ascii=False, separators=(',', ':')))


def main() -> int:
    parser = argparse.ArgumentParser(description='运行澄河观测湖的确定性样例')
    parser.add_argument('command', choices=('demo', 'incident'))
    args = parser.parse_args()
    with TemporaryDirectory(prefix='chenghe-lake-') as temporary:
        directory = Path(temporary)
        if args.command == 'incident':
            return incident(directory)
        demo(directory)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
