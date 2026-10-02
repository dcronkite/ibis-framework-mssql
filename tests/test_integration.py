"""Opt-in SQL Server checks; no connection attempt without a configured host."""

import os
import uuid

import ibis
import pandas as pd
import pytest

from ibis_framework_mssql import Backend

pytestmark = pytest.mark.integration


@pytest.fixture
def connection_factory():
    host = os.getenv('IBIS_MSSQL_TEMP_TEST_HOST')
    if not host:
        pytest.skip('Set IBIS_MSSQL_TEMP_TEST_HOST to run SQL Server integration tests')
    kwargs = {'host': host}
    for key in ('database', 'driver', 'user', 'password'):
        if value := os.getenv(f'IBIS_MSSQL_TEMP_TEST_{key.upper()}'):
            kwargs[key] = value
    if value := os.getenv('IBIS_MSSQL_TEMP_TEST_PORT'):
        kwargs['port'] = int(value)
    connections = []

    def connect():
        connection = Backend().connect(**kwargs)
        connections.append(connection)
        return connection

    yield connect
    for connection in connections:
        connection.disconnect()


def unique_name():
    return 'ibis_temp_test_' + uuid.uuid4().hex


def object_id(connection, name):
    with connection.con.cursor() as cursor:
        cursor.execute('SELECT OBJECT_ID(?)', f'tempdb..{name}')
        return cursor.fetchone()[0]


def test_memtable_executes_twice_and_is_local_to_one_session(connection_factory):
    first, second = connection_factory(), connection_factory()
    table = ibis.memtable({'value': [1, 2, 3]})
    name = table.op().name
    assert first.execute(table.value.sum()) == 6
    assert first.execute(table.value.sum()) == 6
    assert object_id(first, '#' + name) is not None
    assert object_id(second, '#' + name) is None


def test_empty_memtable_executes(connection_factory):
    connection = connection_factory()
    table = ibis.memtable(
        pd.DataFrame({'value': pd.Series(dtype='int64')})
    )
    assert connection.execute(table.count()) == 0


def test_finalizer_drops_the_actual_temporary_table(connection_factory):
    connection = connection_factory()
    table = ibis.memtable({'value': [1]})
    name = table.op().name
    assert connection.execute(table.count()) == 1
    connection._make_memtable_finalizer(name)()
    connection.con.commit()
    assert object_id(connection, '#' + name) is None


def test_create_table_temp_still_supports_data_and_global_scope(connection_factory):
    first, second = connection_factory(), connection_factory()
    name = unique_name()
    source = ibis.memtable({'value': [4, 5]})
    created = first.create_table(name, obj=source, temp=True)
    try:
        assert created.op().name == '##' + name
        assert first.execute(created.value.sum()) == 9
        assert object_id(second, '##' + name) is not None
    finally:
        first._make_memtable_finalizer(created.op().name)()
        first.con.commit()
