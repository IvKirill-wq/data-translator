import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import glossary as glossary_module
from app.core.cache import Cache
from app.core.pipeline import (
    Pair,
    PipelineError,
    apply,
    as_text,
    collect,
    mapped_rows,
    prepare_target,
    translate,
)
from app.core.prefilter import needs_translation, normalize_key
from app.core.protect import Protector
from app.db.base import (
    BIGINT,
    DATE,
    DATETIME,
    DECIMAL,
    INT,
    TEXT,
    UBIGINT,
    Column,
    DbError,
    ensure_unique,
    split_table,
)
from app.db.types import (
    ch_ddl,
    ch_decimal_spec,
    ch_kind,
    clip_name,
    pg_ddl,
    pg_kind,
    pg_name,
    stamp_utc,
    unwrap_ch,
)
from tests.fakes import FakeAdapter, FakeStream, FakeTranslator, text_column


class PrefilterTest(unittest.TestCase):
    def test_needs_translation(self):
        self.assertTrue(needs_translation('Договор'))
        self.assertFalse(needs_translation('Contract 12'))
        self.assertFalse(needs_translation('   '))
        self.assertFalse(needs_translation(None))
        self.assertFalse(needs_translation(42))

    def test_normalize_key_folds_variants(self):
        first = normalize_key('Ключ')
        self.assertEqual(first, normalize_key('ключ '))
        self.assertEqual(first, normalize_key(' Ключ '))
        self.assertEqual(normalize_key('Счёт'), normalize_key('счет'))
        self.assertEqual(normalize_key('два   слова'), 'два слова')


class ProtectTest(unittest.TestCase):
    def setUp(self):
        self.protector = Protector(keep=['Ромашка'])

    def test_round_trip(self):
        masked, parts = self.protector.mask('ООО "Ромашка" № 12/345')
        self.assertNotIn('Ромашка', masked)
        self.assertEqual(self.protector.unmask(masked, parts), 'ООО "Ромашка" № 12/345')

    def test_keep_term_outside_quotes(self):
        masked, parts = self.protector.mask('Ромашка и Василёк')
        self.assertEqual(parts, ['Ромашка'])
        self.assertEqual(self.protector.unmask('QQ1QQ and Cornflower', parts),
                         'Ромашка and Cornflower')

    def test_missing_marker_is_rejected(self):
        masked, parts = self.protector.mask('ООО "Ромашка"')
        self.assertIsNone(self.protector.unmask('LLC Chamomile', parts))

    def test_case_insensitive_marker(self):
        masked, parts = self.protector.mask('ООО "Ромашка"')
        self.assertEqual(self.protector.unmask('LLC qq1qq', parts), 'LLC "Ромашка"')

    def test_nothing_left_to_translate(self):
        masked, _ = self.protector.mask('"Ромашка" 12')
        self.assertFalse(self.protector.has_translatable_rest(masked))
        self.assertTrue(self.protector.has_translatable_rest(self.protector.mask('Счёт 12')[0]))


class GlossaryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'glossary.tsv'
        self.path.write_text(
            '# комментарий\n'
            '\n'
            'whole\tООО\tLLC\n'
            'whole\tСчёт\taccount\n'
            'term\tbill\tinvoice\n'
            'term\tруб.\tRUB\n'
            'keep\tРомашка\n'
            'мусор\tбез\tвида\n',
            encoding='utf-8',
        )
        self.glossary = glossary_module.load(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_whole_lookup_is_normalized(self):
        self.assertEqual(self.glossary.value_for(normalize_key('ооо')), 'LLC')
        self.assertEqual(self.glossary.value_for(normalize_key('счет')), 'account')
        self.assertIsNone(self.glossary.value_for('нет такого'))

    def test_english_terms_work_after_the_engine(self):
        self.assertEqual(self.glossary.fix_terms('Payment bill 5'), 'Payment invoice 5')
        self.assertEqual(self.glossary.fix_terms('billing'), 'billing')
        self.assertEqual(self.glossary.keep, ('Ромашка',))

    def test_russian_terms_work_before_the_engine(self):
        self.assertEqual(self.glossary.fix_source('Оплата 100 руб.'), 'Оплата 100 RUB')
        self.assertEqual(self.glossary.fix_terms('Оплата 100 руб.'), 'Оплата 100 руб.')
        self.assertEqual(len(self.glossary.pre), 1)
        self.assertEqual(len(self.glossary.post), 1)

    def test_backslash_in_target_is_literal(self):
        path = Path(self.directory.name) / 'slash.tsv'
        path.write_text('term\tand or\tand\\or\nterm\tfoo\tbar\\1\n', encoding='utf-8')
        glossary = glossary_module.load(path)
        self.assertEqual(glossary.fix_terms('x and or y'), 'x and\\or y')
        self.assertEqual(glossary.fix_terms('a foo b'), 'a bar\\1 b')

    def test_missing_file_is_empty(self):
        empty = glossary_module.load(Path(self.directory.name) / 'нет.tsv')
        self.assertEqual(empty.whole, {})
        self.assertEqual(empty.keep, ())
        self.assertEqual(empty.pre, ())
        self.assertEqual(empty.post, ())


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = Cache(Path(self.directory.name) / 'sub' / 'cache.sqlite3')

    def tearDown(self):
        self.directory.cleanup()

    def test_save_and_fetch(self):
        self.cache.save([('ключ', 'Ключ', 'Key')], reviewed=False)
        found = self.cache.fetch(['ключ', 'нет'])
        self.assertEqual(found['ключ'], ('Key', False))
        self.assertNotIn('нет', found)

    def test_reviewed_is_not_overwritten_by_engine(self):
        self.cache.save([('ключ', 'Ключ', 'Human')], reviewed=True)
        self.cache.save([('ключ', 'Ключ', 'Machine')], reviewed=False)
        self.assertEqual(self.cache.fetch(['ключ'])['ключ'], ('Human', True))

    def test_reviewed_overwrites_engine(self):
        self.cache.save([('ключ', 'Ключ', 'Machine')], reviewed=False)
        self.cache.save([('ключ', 'Ключ', 'Human')], reviewed=True)
        self.assertEqual(self.cache.fetch(['ключ'])['ключ'], ('Human', True))

    def test_many_keys(self):
        entries = [(f'к{number}', f'К{number}', f'K{number}') for number in range(1200)]
        self.cache.save(entries, reviewed=False)
        found = self.cache.fetch([key for key, _, _ in entries])
        self.assertEqual(len(found), 1200)


class TypesTest(unittest.TestCase):
    def test_pg_kinds(self):
        self.assertEqual(pg_kind('text'), TEXT)
        self.assertEqual(pg_kind('int4'), INT)
        self.assertEqual(pg_kind('int8'), BIGINT)
        self.assertEqual(pg_kind('numeric'), DECIMAL)
        self.assertEqual(pg_kind('date'), DATE)
        self.assertEqual(pg_kind('interval'), TEXT)
        self.assertEqual(pg_kind('int4[]'), TEXT)
        self.assertEqual(pg_kind('text[]'), TEXT)

    def test_array_oid_becomes_text(self):
        self.assertEqual(pg_name(23, 'int4', 1007), 'int4')
        self.assertEqual(pg_name(1007, 'int4', 1007), 'int4[]')
        self.assertEqual(pg_kind(pg_name(1007, 'int4', 1007)), TEXT)
        self.assertEqual(pg_kind(pg_name(23, 'int4', 1007)), INT)
        self.assertEqual(pg_name(705, '', 0), '705')

    def test_ch_kinds(self):
        self.assertEqual(unwrap_ch('Nullable(LowCardinality(String))'), 'String')
        self.assertEqual(ch_kind('LowCardinality(Nullable(String))'), TEXT)
        self.assertEqual(ch_kind('Enum8(\'а\' = 1)'), TEXT)
        self.assertEqual(ch_kind('Int32'), INT)
        self.assertEqual(ch_kind('UInt32'), BIGINT)
        self.assertEqual(ch_kind('UInt64'), UBIGINT)
        self.assertEqual(ch_kind('Int256'), TEXT)
        self.assertEqual(ch_kind('Decimal(18, 2)'), DECIMAL)
        self.assertEqual(ch_kind('DateTime64(3)'), 'datetime')
        self.assertEqual(ch_kind("DateTime64(3, 'UTC')"), 'datetimetz')
        self.assertEqual(ch_kind('Array(String)'), TEXT)

    def test_ddl(self):
        self.assertEqual(pg_ddl(Column('a', UBIGINT, 'UInt64')), 'numeric(20, 0)')
        self.assertEqual(ch_ddl(Column('a', UBIGINT, 'UInt64')), 'Nullable(UInt64)')
        self.assertEqual(pg_ddl(Column('a', TEXT, 'text')), 'text')
        self.assertEqual(ch_ddl(Column('a', TEXT, 'text')), 'Nullable(String)')
        self.assertEqual(ch_ddl(Column('a', DATETIME, 'timestamp')),
                         "Nullable(DateTime64(3, 'UTC'))")

    def test_decimal_keeps_precision(self):
        exact = Column('a', DECIMAL, 'numeric', precision=20, scale=12)
        self.assertEqual(pg_ddl(exact), 'numeric(20, 12)')
        self.assertEqual(ch_ddl(exact), 'Nullable(Decimal(20, 12))')
        unknown = Column('a', DECIMAL, 'numeric')
        self.assertEqual(pg_ddl(unknown), 'numeric')
        self.assertEqual(ch_ddl(unknown), 'Nullable(Decimal(38, 9))')
        wide = Column('a', DECIMAL, 'numeric', precision=200, scale=150)
        self.assertEqual(ch_ddl(wide), 'Nullable(Decimal(76, 76))')

    def test_ch_decimal_spec(self):
        self.assertEqual(ch_decimal_spec('Decimal(18, 4)'), (18, 4))
        self.assertEqual(ch_decimal_spec('Nullable(Decimal64(4))'), (18, 4))
        self.assertEqual(ch_decimal_spec('Decimal256(10)'), (76, 10))
        self.assertEqual(ch_decimal_spec('String'), (0, 0))

    def test_clip_name(self):
        self.assertEqual(clip_name('короткое'), 'короткое')
        long_name = 'Наименование контрагента по справочнику организаций'
        clipped = clip_name(long_name)
        self.assertLessEqual(len(clipped.encode('utf-8')), 63)
        self.assertTrue(long_name.startswith(clipped))
        self.assertEqual(clip_name('a' * 70), 'a' * 63)

    def test_stamp_utc_only_touches_naive_datetimes(self):
        naive = datetime(2024, 1, 1, 12, 0)
        aware = datetime(2024, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=3)))
        row = (naive, aware, 'строка', None)
        stamped = stamp_utc(row, [0, 1, 2, 3])
        self.assertEqual(stamped[0], naive.replace(tzinfo=timezone.utc))
        self.assertEqual(stamped[1], aware)
        self.assertEqual(stamped[2], 'строка')
        self.assertIsNone(stamped[3])

    def test_duplicate_names_are_refused(self):
        columns = [text_column('id'), text_column('kind'), text_column('id')]
        with self.assertRaises(DbError) as caught:
            ensure_unique(columns, 'PostgreSQL')
        self.assertIn('id', str(caught.exception))
        self.assertEqual(len(ensure_unique(columns[:2], 'PostgreSQL')), 2)

    def test_split_table(self):
        self.assertEqual(split_table('t').name, 't')
        self.assertEqual(split_table(' s . t ').schema, 's')
        with self.assertRaises(DbError):
            split_table('a.b.c')
        with self.assertRaises(DbError):
            split_table('')


class AsTextTest(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(as_text('строка'), 'строка')
        self.assertEqual(as_text(['а', 'б']), '["а", "б"]')
        self.assertEqual(as_text({'к': 1}), '{"к": 1}')
        self.assertEqual(as_text(b'\x01\xff'), '01ff')
        self.assertEqual(as_text(12), '12')
        self.assertEqual(as_text(None), 'None')


class CollectTest(unittest.TestCase):
    def adapter(self, rows):
        return FakeAdapter(columns=[text_column('name'), text_column('other')], rows=rows)

    def test_groups_by_normalized_key(self):
        adapter = self.adapter([('Ключ', 'x'), ('ключ ', 'y'), ('Счёт', 'z'), (None, 'q'),
                                ('Contract', 'w'), ('', 'e')])
        values, rows = collect(adapter, 'select 1', ['name'], 100)
        self.assertEqual(rows, 6)
        self.assertEqual(list(values), ['name'])
        self.assertEqual(list(values['name'].values()), ['Ключ', 'Счёт'])

    def test_several_columns(self):
        adapter = self.adapter([('Ключ', 'Папка'), ('ключ', 'папка'), ('Счёт', None)])
        values, rows = collect(adapter, 'select 1', ['name', 'other'], 100)
        self.assertEqual(rows, 3)
        self.assertEqual(list(values['name'].values()), ['Ключ', 'Счёт'])
        self.assertEqual(list(values['other'].values()), ['Папка'])

    def test_automatic_choice_drops_columns_without_cyrillic(self):
        adapter = self.adapter([('Ключ', 'plain'), ('Счёт', '12')])
        values, rows = collect(adapter, 'select 1', [], 100)
        self.assertEqual(list(values), ['name'])

    def test_unknown_column(self):
        with self.assertRaises(PipelineError):
            collect(self.adapter([]), 'select 1', ['нет'], 100)

    def test_limit_counts_every_column(self):
        adapter = self.adapter([('Один', 'Два'), ('Три', 'Четыре')])
        with self.assertRaises(PipelineError):
            collect(adapter, 'select 1', ['name', 'other'], 3)


class TranslateTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = Cache(Path(self.directory.name) / 'cache.sqlite3')
        path = Path(self.directory.name) / 'glossary.tsv'
        path.write_text(
            'whole\tООО\tLLC\nterm\tcontract\tagreement\nterm\tbill\tinvoice\n'
            'term\tруб.\tRUB\nkeep\tРомашка\n',
            encoding='utf-8',
        )
        self.glossary = glossary_module.load(path)
        self.protector = Protector(keep=self.glossary.keep)

    def tearDown(self):
        self.directory.cleanup()

    def run_translate(self, values, translator, rows=7):
        return translate(values, self.cache, self.glossary, self.protector, translator, rows=rows)

    def one_column(self, texts):
        return {'kind': {normalize_key(text): text for text in texts}}

    def test_sources_of_truth(self):
        self.cache.save([(normalize_key('Склад'), 'Склад', 'Warehouse')], reviewed=True)
        values = self.one_column(['ООО', 'Склад', 'Договор', '"Ромашка" 15'])
        translator = FakeTranslator({'Договор': 'contract'})
        result = self.run_translate(values, translator)
        answers = {pair.source: pair.translation for pair in result.pairs['kind']}
        self.assertEqual(answers['ООО'], 'LLC')
        self.assertEqual(answers['Склад'], 'Warehouse')
        self.assertEqual(answers['Договор'], 'agreement')
        self.assertEqual(answers['"Ромашка" 15'], '"Ромашка" 15')
        self.assertEqual(translator.seen, ['Договор'])
        self.assertEqual(result.stats.rows, 7)
        self.assertEqual(result.stats.values, 4)
        self.assertEqual(result.stats.columns, 1)
        self.assertEqual(result.stats.engine, 1)
        self.assertEqual(result.stats.cache, 1)
        self.assertEqual(result.stats.glossary, 1)
        self.assertEqual(result.stats.protected, 1)

    def test_one_engine_call_for_a_value_in_two_columns(self):
        values = {
            'kind': {normalize_key('Договор'): 'Договор'},
            'note': {normalize_key('договор '): 'договор '},
        }
        translator = FakeTranslator({'Договор': 'Supply'})
        result = self.run_translate(values, translator)
        self.assertEqual(translator.seen, ['Договор'])
        self.assertEqual(result.stats.values, 1)
        self.assertEqual(result.stats.columns, 2)
        self.assertEqual(result.pairs['kind'][0].translation, 'Supply')
        self.assertEqual(result.pairs['note'][0].translation, 'Supply')
        self.assertEqual(result.pairs['note'][0].source, 'договор ')

    def test_engine_result_is_cached_unreviewed(self):
        values = self.one_column(['Поставка'])
        self.run_translate(values, FakeTranslator({'Поставка': 'Supply'}))
        self.assertEqual(self.cache.fetch([normalize_key('Поставка')]),
                         {normalize_key('Поставка'): ('Supply', False)})
        second = FakeTranslator()
        result = self.run_translate(values, second)
        self.assertEqual(second.seen, [])
        self.assertEqual(result.pairs['kind'][0].translation, 'Supply')
        self.assertEqual(result.stats.cache, 1)

    def test_lost_markers_retry_without_protection(self):
        values = self.one_column(['ООО "Весна"'])
        translator = FakeTranslator({'ООО QQ1QQ': 'LLC Spring', 'ООО "Весна"': 'LLC Vesna'})
        result = self.run_translate(values, translator)
        self.assertEqual(result.pairs['kind'][0].translation, 'LLC Vesna')
        self.assertEqual(result.stats.raw, 1)
        self.assertEqual(result.stats.fallback, 0)
        self.assertEqual(translator.seen, ['ООО QQ1QQ', 'ООО "Весна"'])

    def test_blank_answer_falls_back_to_original(self):
        values = self.one_column(['Поставка'])
        result = self.run_translate(values, FakeTranslator({'Поставка': '   '}))
        self.assertEqual(result.pairs['kind'][0].translation, 'Поставка')
        self.assertEqual(result.stats.fallback, 1)
        self.assertEqual(result.stats.engine, 0)
        self.assertEqual(self.cache.fetch([normalize_key('Поставка')]), {})

    def test_blank_cached_row_is_ignored(self):
        self.cache.save([(normalize_key('Склад'), 'Склад', '')], reviewed=True)
        values = self.one_column(['Склад'])
        result = self.run_translate(values, FakeTranslator({'Склад': 'Warehouse'}))
        self.assertEqual(result.pairs['kind'][0].translation, 'Warehouse')
        self.assertEqual(result.stats.engine, 1)

    def test_terms_do_not_touch_protected_spans(self):
        values = self.one_column(['Договор с Bill Inc'])
        translator = FakeTranslator({'Договор с QQ1QQ': 'Contract with QQ1QQ'})
        result = self.run_translate(values, translator)
        self.assertEqual(result.pairs['kind'][0].translation, 'agreement with Bill Inc')

    def test_russian_term_is_replaced_before_the_engine(self):
        values = self.one_column(['Оплата 100 руб.'])
        translator = FakeTranslator({'Оплата 100 QQ1QQ': 'Payment 100 QQ1QQ'})
        result = self.run_translate(values, translator)
        self.assertEqual(result.pairs['kind'][0].source, 'Оплата 100 руб.')
        self.assertEqual(result.pairs['kind'][0].translation, 'Payment 100 RUB')
        self.assertEqual(translator.seen, ['Оплата 100 QQ1QQ'])

    def test_fully_protected_value_keeps_the_russian_term_fix(self):
        values = self.one_column(['100 руб.'])
        result = self.run_translate(values, FakeTranslator())
        self.assertEqual(result.pairs['kind'][0].translation, '100 RUB')
        self.assertEqual(result.stats.protected, 1)

    def test_pairs_are_sorted(self):
        values = self.one_column(['Яблоко', 'Арбуз', 'Банан'])
        result = self.run_translate(values, FakeTranslator())
        self.assertEqual([pair.source for pair in result.pairs['kind']],
                         ['Арбуз', 'Банан', 'Яблоко'])


class ShortTranslator:
    def translate(self, texts):
        return list(texts)[:-1]


class TranslateFailureTest(unittest.TestCase):
    def test_short_answer_is_an_error(self):
        directory = tempfile.TemporaryDirectory()
        try:
            cache = Cache(Path(directory.name) / 'cache.sqlite3')
            values = {'kind': {normalize_key(text): text for text in ('Первый', 'Второй')}}
            with self.assertRaises(PipelineError):
                translate(values, cache, glossary_module.EMPTY, Protector(), ShortTranslator())
        finally:
            directory.cleanup()


class NonTextValuesTest(unittest.TestCase):
    def test_collect_and_apply_agree_on_the_key(self):
        columns = [Column(name='doc', kind=TEXT, raw_type='jsonb')]
        rows = [({'вид': 'Договор'},), (None,)]
        source = FakeAdapter(columns=columns, rows=rows)
        values, scanned = collect(source, 'select 1', ['doc'], 10)
        self.assertEqual(scanned, 2)
        self.assertEqual(list(values['doc'].values()), ['{"вид": "Договор"}'])
        pairs = {'doc': [Pair(source='{"вид": "Договор"}', translation='{"kind": "Contract"}')]}
        target = FakeAdapter()
        apply(FakeAdapter(columns=columns, rows=rows), target, 'select 1', pairs, 'out')
        written = target.inserted[0][2]
        self.assertEqual(written, [('{"kind": "Contract"}',), (None,)])


class ApplyTest(unittest.TestCase):
    def source(self):
        columns = [
            Column(name='id', kind=INT, raw_type='int4'),
            Column(name='kind', kind=TEXT, raw_type='text'),
            Column(name='note', kind=TEXT, raw_type='text'),
        ]
        rows = [
            (1, 'Договор', 'Отгрузка'),
            (2, 'договор ', None),
            (3, 'Акт', 'оплата'),
            (4, None, None),
        ]
        return FakeAdapter(columns=columns, rows=rows)

    def pairs(self):
        return {
            'kind': [
                Pair(source='Договор', translation=' Contract ', machine='Agreement'),
                Pair(source='Акт', translation=''),
            ],
        }

    def two_columns(self):
        return {
            'kind': [Pair(source='Договор', translation='Contract', machine='Contract')],
            'note': [
                Pair(source='Отгрузка', translation='Shipment', machine='Shipment'),
                Pair(source='Оплата', translation='Payment', machine='Payment'),
            ],
        }

    def test_creates_table_and_maps_values(self):
        source = self.source()
        target = FakeAdapter()
        applied = apply(source, target, 'select 1', self.pairs(), 'schema.out')
        self.assertEqual(applied.action, 'создана')
        self.assertEqual(applied.written, 4)
        self.assertEqual(applied.replaced, 2)
        self.assertEqual(applied.columns, 1)
        created_table, created_columns = target.created
        self.assertEqual(created_table, 'schema.out')
        self.assertEqual([column.name for column in created_columns], ['id', 'kind', 'note'])
        self.assertEqual([column.kind for column in created_columns], [INT, TEXT, TEXT])
        _, _, rows, clear = target.inserted[0]
        self.assertFalse(clear)
        self.assertEqual(rows[0], (1, 'Contract', 'Отгрузка'))
        self.assertEqual(rows[1], (2, 'Contract', None))
        self.assertEqual(rows[2], (3, 'Акт', 'оплата'))
        self.assertEqual(rows[3], (4, None, None))

    def test_several_columns_at_once(self):
        target = FakeAdapter()
        applied = apply(self.source(), target, 'select 1', self.two_columns(), 'out')
        self.assertEqual(applied.columns, 2)
        self.assertEqual(applied.replaced, 4)
        rows = target.inserted[0][2]
        self.assertEqual(rows[0], (1, 'Contract', 'Shipment'))
        self.assertEqual(rows[2], (3, 'Акт', 'Payment'))

    def test_non_text_column_becomes_text(self):
        target = FakeAdapter()
        pairs = {'id': [Pair(source='1', translation='one', machine='one')]}
        apply(self.source(), target, 'select 1', pairs, 'out')
        _, columns = target.created
        self.assertEqual([column.kind for column in columns], [TEXT, TEXT, TEXT])
        rows = target.inserted[0][2]
        self.assertEqual(rows[0], ('one', 'Договор', 'Отгрузка'))
        self.assertEqual(rows[1], ('2', 'договор ', None))

    def test_existing_table_is_cleared_inside_the_write(self):
        target = FakeAdapter(existing=[
            text_column('id'), text_column('kind'), text_column('note')
        ])
        applied = apply(self.source(), target, 'select 1', self.pairs(), 'out')
        self.assertEqual(applied.action, 'перезаписана')
        self.assertIsNone(target.created)
        self.assertTrue(target.inserted[0][3])

    def test_existing_table_with_other_columns_is_refused(self):
        target = FakeAdapter(existing=[text_column('id'), text_column('другая')])
        with self.assertRaises(PipelineError):
            apply(self.source(), target, 'select 1', self.pairs(), 'out')
        self.assertEqual(target.inserted, [])

    def test_unknown_column_is_refused(self):
        target = FakeAdapter()
        pairs = {'нет': [Pair(source='Акт', translation='Act')]}
        with self.assertRaises(PipelineError):
            apply(self.source(), target, 'select 1', pairs, 'out')

    def test_empty_translations_are_refused(self):
        with self.assertRaises(PipelineError):
            apply(self.source(), FakeAdapter(), 'select 1',
                  {'kind': [Pair(source='Договор', translation='   ')]}, 'out')

    def test_only_hand_edited_pairs_are_marked_reviewed(self):
        directory = tempfile.TemporaryDirectory()
        try:
            cache = Cache(Path(directory.name) / 'cache.sqlite3')
            pairs = {'kind': [
                Pair(source='Договор', translation=' Contract ', machine='Agreement'),
                Pair(source='Акт', translation='Act', machine='Act'),
            ]}
            apply(self.source(), FakeAdapter(), 'select 1', pairs, 'out', cache=cache)
            found = cache.fetch([normalize_key('Договор'), normalize_key('Акт')])
            self.assertEqual(found[normalize_key('Договор')], ('Contract', True))
            self.assertEqual(found[normalize_key('Акт')], ('Act', False))
        finally:
            directory.cleanup()

    def test_row_width_mismatch(self):
        columns = [text_column('a'), text_column('b')]
        stream = FakeStream(columns, [('x', 'y', 'z')])
        with self.assertRaises(PipelineError):
            list(mapped_rows(stream, {0: {}}, columns, [0]))

    def test_prepare_target_reports_action(self):
        target = FakeAdapter()
        self.assertEqual(prepare_target(target, 'out', [text_column('a')]), 'создана')
        self.assertEqual(prepare_target(target, 'out', [text_column('a')]), 'перезаписана')


if __name__ == '__main__':
    unittest.main()
