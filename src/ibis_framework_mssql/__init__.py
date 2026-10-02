"""Consistent SQL Server temporary-table names for Ibis MSSQL."""

import sqlglot as sg
import sqlglot.expressions as sge

from ibis.backends.mssql import Backend as MSSQLBackend
from ibis.backends.sql.compilers.mssql import MSSQLCompiler

__all__ = ['Backend']


def _temp_name(name: str) -> str:
    """Turn temporary names into #local (avoid ##global)"""
    return name if name.startswith('#') else f'#{name}'


class TempMSSQLCompiler(MSSQLCompiler):
    __slots__ = ()

    def visit_InMemoryTable(self, op, *, name, schema, data):
        return super().visit_InMemoryTable(op, name=_temp_name(name), schema=schema, data=data)


class Backend(MSSQLBackend):
    """MSSQL backend with matching temporary names for upload, query, and drop.

    Unprefixed memtable names become local temporary tables on this connection.
    An explicitly supplied ## name retains SQL Server's global scope.
    """

    name = 'mssql_temp'
    compiler = TempMSSQLCompiler()

    def _register_in_memory_table(self, op):
        # copy only the operation used by the uploader (not ibis internal)
        return super()._register_in_memory_table(op.copy(name=_temp_name(op.name)))

    def _make_memtable_finalizer(self, name):
        target = sg.table(_temp_name(name), quoted=self.compiler.quoted).sql(self.dialect)
        # Render the quoted identifier separately: sqlglot v30 changed the Drop AST's target field
        drop_sql = f'DROP TABLE IF EXISTS {target}'
        connection = self.con

        def finalize():
            with connection.cursor() as cursor:
                cursor.execute(drop_sql)

        return finalize

    def _safe_ddl(self, query, *args, **kwargs):
        # ibis v12 expects its explicit temp tables to be global
        if isinstance(query, sge.Create) and query.kind == 'TABLE':
            properties = query.args.get('properties')
            if properties is not None and any(
                    isinstance(prop, sge.TemporaryProperty)
                    for prop in properties.expressions
            ):
                query = query.copy()
                target = query.this
                if isinstance(target, sge.Schema):
                    target = target.this
                target.set(
                    'this',
                    sg.to_identifier('##' + target.name.lstrip('#'), quoted=self.compiler.quoted),
                )
                remaining = [
                    prop for prop in query.args['properties'].expressions
                    if not isinstance(prop, sge.TemporaryProperty)
                ]
                query.set('properties', sge.Properties(expressions=remaining) if remaining else None)
        return super()._safe_ddl(query, *args, **kwargs)
