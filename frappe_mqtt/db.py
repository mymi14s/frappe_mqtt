from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import re, pymysql, psycopg2 
import psycopg2.extras  


class DBFrappe:
    def __init__(self, db_type: str, host: str, user: str, password: str, db_name: Optional[str] = None, *, debug: bool = False):
        self.db_type = db_type.strip().lower()
        if self.db_type in {"mysql", "mariadb"}:
            self.db_type = "mysql"
        elif self.db_type in {"postgresql"}:
            self.db_type = "postgres"
        if self.db_type not in {"mysql", "postgres"}:
            raise ValueError("db_type must be 'mysql' or 'postgres'")

        self.db_name = db_name or user
        self.host = host
        self.user = user
        self.password = password
        self.debug = debug

        self.conn = self._connect()
        self._set_autocommit(True)

    def get_doc(self, doctype: str, name: Any) -> Dict[str, Any]:
        table = self._q_table(doctype)
        pk_col = self._detect_pk(doctype)
        sql = f"SELECT * FROM {table} WHERE {self._q_ident(pk_col)} = %s LIMIT 1"
        row = self._fetchone(sql, (name,))
        if row is None:
            raise LookupError(f"{doctype} with {pk_col}={name!r} not found")
        return row

    def new_doc(self, doctype: str, fields: Optional[Dict[str, Any]] = None, **kwargs: Any) -> "NewDocument":
        cols = self._get_table_columns(doctype)
        data = fields.copy() if fields else {}
        data.update(kwargs)
        return NewDocument(db=self, doctype=doctype, allowed_fields=cols, data=data)

    def delete_doc(self, doctype: str, name: Any) -> None:
        table = self._q_table(doctype)
        pk_col = self._detect_pk(doctype)
        sql = f"DELETE FROM {table} WHERE {self._q_ident(pk_col)} = %s"
        self._execute(sql, (name,))

    def get_list(
        self,
        doctype: str,
        *,
        fields: Optional[Sequence[str]] = None,
        filters: Optional[Union[Dict[str, Any], Sequence[Union[Tuple[str, str, Any], Dict[str, Any]]]]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = 20,
        offset: Optional[int] = 0,
    ) -> List[Dict[str, Any]]:
        select_sql = self._select_clause(doctype, fields, default_name_only=True)
        where_sql, params = self._build_where(doctype, filters)
        order_sql = self._build_order_by(doctype, order_by)
        lim_sql = f" LIMIT {int(limit)}" if isinstance(limit, int) and limit is not None and limit >= 0 else ""
        off_sql = f" OFFSET {int(offset)}" if lim_sql and isinstance(offset, int) and offset > 0 else ""

        table = self._q_table(doctype)
        sql = f"SELECT {select_sql} FROM {table}{where_sql}{order_sql}{lim_sql}{off_sql}"
        return self._fetchall(sql, tuple(params))

    def get_all(
        self,
        doctype: str,
        *,
        fields: Optional[Sequence[str]] = None,
        filters: Optional[Union[Dict[str, Any], Sequence[Union[Tuple[str, str, Any], Dict[str, Any]]]]] = None,
        order_by: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self.get_list(
            doctype,
            fields=fields,
            filters=filters,
            order_by=order_by,
            limit=None,
            offset=None,
        )

    def _connect(self):
        if self.db_type == "mysql":
            return pymysql.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.db_name,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
        return psycopg2.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            dbname=self.db_name,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    def _set_autocommit(self, value: bool) -> None:
        if self.db_type == "mysql":
            self.conn.autocommit(value)
        else:
            self.conn.autocommit = value

    def _cursor(self):
        try:
            return self.conn.cursor()
        except (pymysql.err.OperationalError, psycopg2.OperationalError):
            self.conn = self._connect()
            self._set_autocommit(True)
            return self.conn.cursor()

    def _execute(self, sql: str, params: Tuple[Any, ...] = ()) -> None:
        if self.debug:
            print("SQL:", sql, "PARAMS:", params)
        with self._cursor() as cur:
            cur.execute(sql, params)

    def _fetchone(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        if self.debug:
            print("SQL:", sql, "PARAMS:", params)
        with self._cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row if row is None or isinstance(row, dict) else dict(row)

    def _fetchall(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        if self.debug:
            print("SQL:", sql, "PARAMS:", params)
        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [r if isinstance(r, dict) else dict(r) for r in rows]

    def _detect_pk(self, doctype: str) -> str:
        cols = self._get_table_columns(doctype)
        if "name" in cols:
            return "name"
        if "id" in cols:
            return "id"
        pk = self._read_primary_key(doctype)
        if pk:
            return pk
        raise RuntimeError(f"Cannot detect primary key for table '{doctype}'.")

    def _read_primary_key(self, doctype: str) -> Optional[str]:
        table_name = f"tab{doctype}"
        if self.db_type == "mysql":
            sql = (
                "SELECT k.COLUMN_NAME FROM information_schema.table_constraints t "
                "JOIN information_schema.key_column_usage k "
                "USING (constraint_name, table_schema, table_name) "
                "WHERE t.constraint_type='PRIMARY KEY' AND t.table_schema=DATABASE() AND t.table_name=%s"
            )
            row = self._fetchone(sql, (table_name,))
            return row["COLUMN_NAME"] if row else None
        sql = (
            "SELECT a.attname AS column_name "
            "FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = %s::regclass AND i.indisprimary"
        )
        row = self._fetchone(sql, (table_name,))
        return row["column_name"] if row else None

    def _get_table_columns(self, doctype: str) -> List[str]:
        table_name = f"tab{doctype}"
        if self.db_type == "mysql":
            sql = (
                "SELECT COLUMN_NAME FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s"
            )
            rows = self._fetchall(sql, (table_name,))
            return [r["COLUMN_NAME"] for r in rows]
        sql = (
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s"
        )
        rows = self._fetchall(sql, (table_name,))
        return [r["column_name"] for r in rows]

    def _select_clause(self, doctype: str, fields: Optional[Sequence[str]], *, default_name_only: bool) -> str:
        cols = self._get_table_columns(doctype)
        if not fields:
            if default_name_only and "name" in cols:
                use = ["name"]
            else:
                return "*"
        else:
            use = []
            for f in fields:
                if f == "*":
                    return "*"
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", f):
                    raise ValueError(f"Invalid field name: {f!r}")
                if f not in cols:
                    raise ValueError(f"Unknown field for {doctype}: {f!r}")
                use.append(f)
        return ", ".join(self._q_ident(c) for c in use)

    def _build_where(
        self,
        doctype: str,
        filters: Optional[Union[Dict[str, Any], Sequence[Union[Tuple[str, str, Any], Dict[str, Any]]]]],
    ) -> Tuple[str, List[Any]]:
        if not filters:
            return "", []
        cols = set(self._get_table_columns(doctype))
        clauses: List[str] = []
        params: List[Any] = []

        def add_simple(col: str, op: str, val: Any):
            if col not in cols:
                raise ValueError(f"Unknown field: {col!r} for {doctype}")
            op_l = op.strip().lower()
            if op_l in {"=", "!=", ">", ">=", "<", "<="}:
                clauses.append(f"{self._q_ident(col)} {op} %s")
                params.append(val)
            elif op_l in {"like", "ilike"}:
                if op_l == "ilike" and self.db_type == "mysql":
                    clauses.append(f"LOWER({self._q_ident(col)}) LIKE LOWER(%s)")
                else:
                    clauses.append(f"{self._q_ident(col)} {op.upper()} %s")
                params.append(val)
            elif op_l == "in":
                if not isinstance(val, (list, tuple, set)):
                    raise ValueError("'in' operator requires a list/tuple/set")
                placeholders = ", ".join(["%s"] * len(val))
                clauses.append(f"{self._q_ident(col)} IN ({placeholders})")
                params.extend(list(val))
            elif op_l == "between":
                if not (isinstance(val, (list, tuple)) and len(val) == 2):
                    raise ValueError("'between' requires a 2-length tuple/list")
                clauses.append(f"{self._q_ident(col)} BETWEEN %s AND %s")
                params.extend([val[0], val[1]])
            else:
                raise ValueError(f"Unsupported operator: {op}")

        if isinstance(filters, dict):
            for k, v in filters.items():
                add_simple(k, "=", v)
        else:
            for cond in filters:
                if isinstance(cond, dict):
                    for k, v in cond.items():
                        add_simple(k, "=", v)
                else:
                    if not (isinstance(cond, (list, tuple)) and len(cond) == 3):
                        raise ValueError("Each filter tuple must be (field, op, value)")
                    col, op, val = cond
                    add_simple(str(col), str(op), val)
        where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
        return where_sql, params

    def _build_order_by(self, doctype: str, order_by: Optional[str]) -> str:
        if not order_by:
            return ""
        cols = set(self._get_table_columns(doctype))
        parts = []
        for token in order_by.split(","):
            token = token.strip()
            if not token:
                continue
            bits = token.split()
            col = bits[0]
            if col not in cols:
                raise ValueError(f"Unknown column in order_by: {col!r}")
            direction = bits[1].lower() if len(bits) > 1 else "asc"
            if direction not in {"asc", "desc"}:
                raise ValueError("order_by direction must be 'asc' or 'desc'")
            parts.append(f"{self._q_ident(col)} {direction.upper()}")
        return (" ORDER BY " + ", ".join(parts)) if parts else ""

    def _q_ident(self, ident: str) -> str:
        if not ident:
            raise ValueError("Empty identifier")
        if self.db_type == "mysql":
            return "`" + ident.replace("`", "``") + "`"
        return '"' + ident.replace('"', '""') + '"'

    def _q_table(self, table: str) -> str:
        real_table = f"tab{table}"
        if self.db_type == "mysql":
            return "`" + real_table.replace("`", "``") + "`"
        return '"' + real_table.replace('"', '""') + '"'


@dataclass
class NewDocument:
    db: DBFrappe
    doctype: str
    allowed_fields: List[str]
    data: Dict[str, Any]

    def update(self, values: Dict[str, Any]) -> None:
        for k, v in values.items():
            if k not in self.allowed_fields:
                raise ValueError(f"Unknown field: {k}")
            self.data[k] = v

    def insert(self) -> Dict[str, Any]:
        if not self.data:
            raise ValueError("No data set for insert")
        cols = [c for c in self.data.keys() if c in self.allowed_fields]
        if not cols:
            raise ValueError(f"No valid columns to insert for table '{self.doctype}'")
        placeholders = ", ".join(["%s"] * len(cols))
        collist = ", ".join([self.db._q_ident(c) for c in cols])
        table = self.db._q_table(self.doctype)

        if self.db.db_type == "postgres":
            sql = f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) RETURNING *"
            row = self.db._fetchone(sql, tuple(self.data[c] for c in cols))
            return row or dict(self.data)
        else:  # MySQL
            sql = f"INSERT INTO {table} ({collist}) VALUES ({placeholders})"
            self.db._execute(sql, tuple(self.data[c] for c in cols))
            pk = self.db._detect_pk(self.doctype)
            if pk in self.data:
                return self.db.get_doc(self.doctype, self.data[pk])
            return dict(self.data)


class frappe_db:
    def __init__(self, db: DBFrappe):
        self.db = db

    def get_doc(self, doctype: str, name: Any) -> Dict[str, Any]:
        return self.db.get_doc(doctype, name)

    def new_doc(self, doctype: str, fields: Optional[Dict[str, Any]] = None, **kwargs: Any) -> NewDocument:
        return self.db.new_doc(doctype, fields, **kwargs)

    def delete_doc(self, doctype: str, name: Any) -> None:
        self.db.delete_doc(doctype, name)

    def get_list(self, *args, **kwargs):
        return self.db.get_list(*args, **kwargs)

    def get_all(self, *args, **kwargs):
        return self.db.get_all(*args, **kwargs)
