from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from lake_checks import load_sample
from sources.bootstrap import initialize_source


class LakeCase(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory(prefix='chenghe-test-'))
        self.directory = Path(temporary)

    def connection(self, name):
        db = sqlite3.connect(self.directory / (name + '.sqlite'), isolation_level=None)
        self.addCleanup(db.close)
        return db

    def load_source(self, name='switch', database='source'):
        baseline_id, baseline, batches = load_sample(name)
        source = self.connection(database)
        initialize_source(source, baseline_id, baseline, batches=batches)
        return source, baseline_id, baseline, batches

    def receipt_count(self, db):
        return db.execute('SELECT COUNT(*) FROM mirror_receipts').fetchone()[0]

    def delivery_count(self, db):
        return db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0]
