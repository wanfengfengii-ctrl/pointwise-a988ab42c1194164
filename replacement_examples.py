"""Public continuation scenario using only the documented business entry points."""

import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from delivery.export import export_delivery
from delivery.manifest import InvalidManifest, decode_manifest
from delivery.order import NoLegalOrder
from delivery.registry import publish_delivery
from mirror.apply import BatchOutOfOrder, accept_batch
from mirror.imports import DeliveryBindingMismatch
from mirror.read import read_mirror
from mirror.state import initialize_mirror
from receipts.lookup import ReceiptNotFound, read_receipt
from sources.bootstrap import initialize_source
from sources.records import ObservationChange, OriginalBatch, SourceChange
from warehouse.snapshots import EffectiveSource, Observation, make_snapshot


BASELINE_ID = "replacement-day"
BASELINE = make_snapshot()
BATCHES = (
    OriginalBatch("A", (SourceChange("y-origin", "upsert", "Y"),)),
    OriginalBatch("P", ()),
    OriginalBatch("Q", ()),
    OriginalBatch("U", (ObservationChange("r", "upsert", "Y", 90),)),
    OriginalBatch("C", (ObservationChange("r", "upsert", "Y", 17),)),
    OriginalBatch("D", ()),
    OriginalBatch("Z", (ObservationChange("orphan", "upsert", "Z", 1),)),
)
PREFIX = make_snapshot([EffectiveSource("y-origin", "Y")])
CORRECTED = make_snapshot(PREFIX.sources, [Observation("r", "Y", 17)])
ORIGINAL = make_snapshot(PREFIX.sources, [Observation("r", "Y", 90)])


@contextmanager
def rejects(error):
    try:
        yield
    except error:
        return
    raise AssertionError(f"expected {error.__name__}")


def create_parents(source):
    initialize_source(source, BASELINE_ID, BASELINE, batches=BATCHES)
    parents = {}
    for middle in ("P", "Q"):
        delivery_id = publish_delivery(source, BASELINE_ID, ("U", middle, "A"))
        package = export_delivery(source, delivery_id)
        assert tuple(batch.batch_id for batch in decode_manifest(package).batches) == ("A", middle, "U")
        parents[middle] = delivery_id, package
    assert parents["P"][0] != parents["Q"][0]
    return parents


def install_prefix(mirror, parent, middle):
    delivery_id, package = parent
    initialize_mirror(mirror, BASELINE_ID, BASELINE)
    first = accept_batch(mirror, package, "A")
    second = accept_batch(mirror, package, middle)
    view = read_mirror(mirror)
    assert (view.cursor.delivery_id, view.cursor.position) == (delivery_id, 2)
    assert view.snapshot == PREFIX
    assert (first.delivery_id, first.batch_id, first.position) == (delivery_id, "A", 1)
    assert (second.delivery_id, second.batch_id, second.position) == (delivery_id, middle, 2)
    return first, second


def check_v1(directory):
    with closing(sqlite3.connect(directory / "source.sqlite")) as source:
        parents = create_parents(source)
        with rejects(NoLegalOrder):
            publish_delivery(source, BASELINE_ID, ("C", "D"))
    for middle, parent in parents.items():
        path = directory / f"mirror-{middle}.sqlite"
        with closing(sqlite3.connect(path)) as mirror:
            _, second = install_prefix(mirror, parent, middle)
        with closing(sqlite3.connect(path)) as mirror:
            assert read_receipt(mirror, second.receipt_id) == second
            assert accept_batch(mirror, parent[1], "U").position == 3
            assert read_mirror(mirror).snapshot == ORIGINAL
    print(json.dumps({"original_order": ["A", "P", "U"], "position": 3, "r_mm": 90}))


def check_replacement(directory):
    from delivery.registry import publish_replacement

    source_path = directory / "source.sqlite"
    with closing(sqlite3.connect(source_path)) as source:
        parents = create_parents(source)
        parent_id, parent_package = parents["P"]
        with rejects(NoLegalOrder):
            publish_replacement(source, parent_id, 2, ("Z",))
        with rejects(ValueError):
            publish_replacement(source, parent_id, 2, ("P",))
        child_id = publish_replacement(source, parent_id, 2, ("D", "C"))
        child_package = export_delivery(source, child_id)
        competitor = publish_replacement(source, parent_id, 2, ("D",))
        competitor_package = export_delivery(source, competitor)
        assert child_id not in {parent_id, competitor}
    with closing(sqlite3.connect(source_path)) as source:
        assert publish_replacement(source, parent_id, 2, ("C", "D")) == child_id
        assert export_delivery(source, child_id) == child_package
        assert export_delivery(source, parent_id) == parent_package

    with closing(sqlite3.connect(directory / "wrong-parent.sqlite")) as mirror:
        install_prefix(mirror, parents["Q"], "Q")
        before = read_mirror(mirror)
        with rejects(DeliveryBindingMismatch):
            accept_batch(mirror, child_package, "C")
        assert read_mirror(mirror) == before
    with closing(sqlite3.connect(directory / "too-late.sqlite")) as mirror:
        install_prefix(mirror, parents["P"], "P")
        accept_batch(mirror, parent_package, "U")
        before = read_mirror(mirror)
        with rejects(DeliveryBindingMismatch):
            accept_batch(mirror, child_package, "C")
        assert read_mirror(mirror) == before and before.snapshot == ORIGINAL

    mirror_path = directory / "target.sqlite"
    with closing(sqlite3.connect(mirror_path)) as mirror:
        first, second = install_prefix(mirror, parents["P"], "P")
        before = read_mirror(mirror)
        with rejects(InvalidManifest):
            accept_batch(mirror, child_package + "{", "C")
        with rejects(BatchOutOfOrder):
            accept_batch(mirror, child_package, "D")
        assert read_mirror(mirror) == before
        denied = []

        def deny_commit(action, operation, *_):
            if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT":
                denied.append(operation)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        mirror.set_authorizer(deny_commit)
        try:
            with rejects(sqlite3.DatabaseError):
                accept_batch(mirror, child_package, "C")
        finally:
            mirror.set_authorizer(None)
        assert denied and not mirror.in_transaction

    with closing(sqlite3.connect(mirror_path)) as mirror, closing(sqlite3.connect(":memory:")) as failed_copy:
        assert read_mirror(mirror) == before
        assert accept_batch(mirror, parent_package, "P") == second
        mirror.backup(failed_copy)
        corrected = accept_batch(mirror, child_package, "C")
        with rejects(ReceiptNotFound):
            read_receipt(failed_copy, corrected.receipt_id)
        view = read_mirror(mirror)
        assert (view.cursor.delivery_id, view.cursor.position, view.snapshot) == (child_id, 1, CORRECTED)
        assert (corrected.delivery_id, corrected.batch_id, corrected.position) == (child_id, "C", 1)
        assert accept_batch(mirror, parent_package, "A") == first
        assert accept_batch(mirror, parent_package, "P") == second
        assert accept_batch(mirror, child_package, "C") == corrected
        with rejects(DeliveryBindingMismatch):
            accept_batch(mirror, parent_package, "U")
        with rejects(DeliveryBindingMismatch):
            accept_batch(mirror, competitor_package, "D")
        assert read_mirror(mirror) == view

    with closing(sqlite3.connect(mirror_path)) as mirror:
        resumed = accept_batch(mirror, child_package, "D")
        assert (resumed.delivery_id, resumed.batch_id, resumed.position) == (child_id, "D", 2)
        view = read_mirror(mirror)
        assert (view.cursor.delivery_id, view.cursor.position, view.snapshot) == (child_id, 2, CORRECTED)
        for receipt in (first, second, corrected, resumed):
            assert read_receipt(mirror, receipt.receipt_id) == receipt
        assert accept_batch(mirror, parent_package, "P") == second
        assert read_mirror(mirror) == view
    print(json.dumps({"parent_retry": [second.delivery_id, second.batch_id, second.position],
                      "replacement_receipt": [corrected.delivery_id, corrected.batch_id, corrected.position],
                      "reopened_position": resumed.position, "r_mm": 17, "old_U_rejected": True}))


def demo(directory):
    """Run the original offline handoff demo, then the replacement takeover demo."""
    from lake_checks import demo as switch_demo

    switch_directory = directory / "switch"
    switch_directory.mkdir()
    switch_demo(switch_directory)
    takeover_directory = directory / "takeover"
    takeover_directory.mkdir()
    check_replacement(takeover_directory)


def main():
    parser = argparse.ArgumentParser(description="澄河观测湖的日批续发样例")
    parser.add_argument("mode", choices=("baseline", "verify", "demo"))
    args = parser.parse_args()
    with TemporaryDirectory(prefix="chenghe-replacement-") as temporary:
        directory = Path(temporary)
        if args.mode == "baseline":
            check_v1(directory)
        elif args.mode == "verify":
            check_replacement(directory)
        else:
            demo(directory)


if __name__ == "__main__":
    main()
