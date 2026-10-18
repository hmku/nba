"""Render Postgres DDL from the SQLAlchemy metadata: python -m fantasy.db.ddl"""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from fantasy.db.schema import metadata


def render() -> str:
    dialect = postgresql.dialect()
    out: list[str] = []
    for table in metadata.sorted_tables:
        ddl = str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip()
        out.append(ddl + ";")
        for idx in table.indexes:
            out.append(str(CreateIndex(idx, if_not_exists=True).compile(dialect=dialect)).strip() + ";")
    return "\n\n".join(out) + "\n"


if __name__ == "__main__":
    print(render())
