import ibis
import ibis.common.exceptions as com
import ibis.expr.operations as ops
import pandas as pd
import pytest
from ibis.backends.mssql import Backend as StockBackend

from ibis_framework_mssql import Backend


def named_memtable(data, *, name, **kwargs):
    table = ibis.memtable(data, **kwargs)
    return table.op().copy(name=name).to_expr()


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, *args, **kwargs):
        self.connection.statements.append((sql, None))
        return self

    def executemany(self, sql, rows):
        self.connection.statements.append((sql, list(rows)))
        return self

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return RecordingCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass


def recording_backend(cls=Backend):
    backend = cls()
    backend.con = RecordingConnection()
    return backend


def test_stock_backend_uploads_a_permanent_memtable():
    backend = recording_backend(StockBackend)
    table = named_memtable({'value': [1]}, name='stock_demo')
    backend._register_in_memory_table(table.op())
    assert backend.con.statements[0][0].startswith('CREATE TABLE [stock_demo]')
    assert 'FROM [stock_demo]' in backend.compile(table)


def test_prefixing_only_the_uploader_leaves_select_and_drop_broken():
    class OneLinePatch(StockBackend):
        def _register_in_memory_table(self, op):
            return super()._register_in_memory_table(op.copy(name='#' + op.name))

    backend = recording_backend(OneLinePatch)
    table = named_memtable({'value': [1]}, name='one_line_demo')
    backend._register_in_memory_table(table.op())
    assert '[#one_line_demo]' in backend.con.statements[0][0]
    assert 'FROM [one_line_demo]' in backend.compile(table)
    backend._make_memtable_finalizer(table.op().name)()
    assert '[#one_line_demo]' not in backend.con.statements[-1][0]


@pytest.mark.parametrize('name', [
    'demo', '#local_demo', '##global_demo', 'quoted name',
])
def test_upload_select_and_drop_use_the_same_physical_name(name):
    backend = recording_backend()
    table = named_memtable({'value': [1, 2]}, name=name)
    physical = name if name.startswith('#') else '#' + name
    backend._register_in_memory_table(table.op())
    create, insert = backend.con.statements
    assert f'[{physical}]' in create[0]
    assert f'[{physical}]' in insert[0]
    assert insert[1] == [(1,), (2,)]
    assert f'FROM [{physical}]' in backend.compile(table.filter(table.value > 1))
    backend._make_memtable_finalizer(name)()
    assert f'[{physical}]' in backend.con.statements[-1][0]
    assert backend.con.statements[-1][0].startswith('DROP TABLE')
    assert table.op().name == name


def test_registration_runs_once_for_repeated_use_of_the_same_expression():
    backend = recording_backend()
    table = named_memtable({'value': [1]}, name='repeated_demo')
    backend._run_pre_execute_hooks(table)
    backend._run_pre_execute_hooks(table.filter(table.value > 0))
    assert len(backend.con.statements) == 2  # one CREATE and one INSERT
    assert table.op() in backend._memtables


def test_join_compiles_both_temporary_names():
    backend = recording_backend()
    left = named_memtable({'key': [1], 'a': [2]}, name='left_demo')
    right = named_memtable({'key': [1], 'b': [3]}, name='right_demo')
    sql = backend.compile(left.join(right, 'key'))
    assert '[#left_demo]' in sql
    assert '[#right_demo]' in sql


def test_empty_memtable_creates_a_table_without_inserting_rows():
    backend = recording_backend()
    table = named_memtable(
        pd.DataFrame({'value': pd.Series(dtype='int64')}), name='empty_demo'
    )
    backend._register_in_memory_table(table.op())
    assert len(backend.con.statements) == 1
    assert '[#empty_demo]' in backend.con.statements[0][0]
    assert 'FROM [#empty_demo]' in backend.compile(table)


def test_null_type_rejection_is_preserved():
    backend = recording_backend()
    table = ibis.memtable({'value': [None]}, schema={'value': 'null'})
    with pytest.raises(com.IbisTypeError, match='null'):
        backend._register_in_memory_table(table.op())
    assert not backend.con.statements


def test_permanent_database_table_name_is_preserved():
    backend = recording_backend()
    table = ops.DatabaseTable(
        name='permanent_demo',
        schema=ibis.schema({'value': 'int64'}),
        source=backend,
        namespace=ops.Namespace(database='dbo'),
    ).to_expr()
    sql = backend.compile(table)
    assert '[dbo].[permanent_demo]' in sql
    assert '#permanent_demo' not in sql


def test_create_table_temp_retains_upstream_global_scope():
    backend = recording_backend()
    table = backend.create_table('global_created', schema={'value': 'int64'}, temp=True)
    assert '[##global_created]' in backend.con.statements[0][0]
    assert table.op().name == '##global_created'
    assert '[##global_created]' in backend.compile(table)


def test_create_table_with_data_uses_one_global_name_for_create_insert_and_select():
    backend = recording_backend()
    source = named_memtable({'value': [4, 5]}, name='source_demo')
    table = backend.create_table('global_populated', obj=source, temp=True)
    create, upload, global_create, global_insert = backend.con.statements
    assert '[#source_demo]' in create[0]
    assert '[#source_demo]' in upload[0]
    assert '[##global_populated]' in global_create[0]
    assert '[##global_populated]' in global_insert[0]
    assert '[#source_demo]' in global_insert[0]
    assert '[##global_populated]' in backend.compile(table)


def test_permanent_create_is_preserved():
    backend = recording_backend()
    table = backend.create_table('permanent_created', schema={'value': 'int64'})
    assert backend.con.statements[0][0].startswith('CREATE TABLE [permanent_created]')
    assert table.op().name == 'permanent_created'


def test_distinct_entry_point_can_be_loaded_by_ibis():
    table = named_memtable({'value': [1]}, name='entry_point_demo')
    assert '[#entry_point_demo]' in ibis.mssql_temp.compile(table)
    assert '[#entry_point_demo]' not in ibis.mssql.compile(table)
