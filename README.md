# ibis-framework-mssql

Temp table experiment due to https://github.com/ibis-project/ibis/issues/11374.

Once installed, Ibis discovers the distinct `mssql_temp` entry point:

```python
import ibis

con = ibis.mssql_temp.connect(
    host='YOUR_SQL_SERVER',
    database='YOUR_DATABASE',
    driver='ODBC Driver 18 for SQL Server',
)

t = ibis.memtable({'value': [1, 2, 3]})
expr = t.filter(t.value > 1)
print(con.compile(expr))  # FROM [#pandas_memtable_...]
print(con.execute(expr))
print(con.execute(expr))  # reuses the registered table
con.disconnect()  # SQL Server removes the local table at session end
```
