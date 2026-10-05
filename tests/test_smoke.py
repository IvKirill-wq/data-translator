import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.pipeline import Pair

SPEC = importlib.util.spec_from_file_location(
    'smoke', Path(__file__).resolve().parent.parent / 'scripts' / 'smoke.py'
)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class Options:
    def __init__(self, columns=(), auto=False):
        self.columns = list(columns)
        self.auto = auto


class SmokeTest(unittest.TestCase):
    def test_column_names(self):
        self.assertEqual(smoke.column_names(Options(['kind,note', ' id '])), ['kind', 'note', 'id'])
        self.assertEqual(smoke.column_names(Options(['kind'], auto=True)), [])
        self.assertEqual(smoke.column_names(Options()), [])

    def test_pairs_round_trip(self):
        pairs = {
            'kind': [Pair(source='Договор', translation='Contract', machine='Agreement')],
            'note': [Pair(source='Отгрузка', translation='Shipment', machine='Shipment')],
        }
        directory = tempfile.TemporaryDirectory()
        try:
            path = Path(directory.name) / 'pairs.tsv'
            with contextlib.redirect_stderr(io.StringIO()):
                smoke.write_pairs(pairs, str(path))
            back = smoke.read_pairs(str(path))
            self.assertEqual(list(back), ['kind', 'note'])
            self.assertEqual(back['kind'][0].source, 'Договор')
            self.assertEqual(back['kind'][0].translation, 'Contract')
            self.assertEqual(back['kind'][0].machine, 'Agreement')
            self.assertEqual(back['note'][0].translation, 'Shipment')
        finally:
            directory.cleanup()

    def test_hand_written_tsv_without_machine(self):
        directory = tempfile.TemporaryDirectory()
        try:
            path = Path(directory.name) / 'pairs.tsv'
            path.write_text('kind\tДоговор\tContract\n\nплохая строка\n', encoding='utf-8')
            back = smoke.read_pairs(str(path))
            self.assertEqual(list(back), ['kind'])
            self.assertEqual(back['kind'][0].machine, '')
        finally:
            directory.cleanup()


if __name__ == '__main__':
    unittest.main()
