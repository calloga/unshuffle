import sqlite3
from contextlib import contextmanager
from functools import wraps
from threading import RLock
from typing import Callable

from peewee import SqliteDatabase

from unshuffle.persistence.schema.models import db_proxy

ConnectionProvider = Callable[[], sqlite3.Connection]

# Peewee models use one process-wide DatabaseProxy. Binding that proxy and
# executing a query must therefore be one atomic operation across all
# UnshuffleDB instances and worker threads.
_PEEWEE_PROXY_LOCK = RLock()


def bind_peewee_store(cls):
    """Serialize and bind every operation implemented by a Peewee store."""
    for name, method in tuple(vars(cls).items()):
        if name == "__init__" or isinstance(method, (staticmethod, classmethod, property)):
            continue
        if not callable(method):
            continue

        @wraps(method)
        def bound_method(self, *args, __method=method, **kwargs):
            with self._bound_database():
                return __method(self, *args, **kwargs)

        setattr(cls, name, bound_method)
    return cls


class ThreadAwareSqliteDatabase(SqliteDatabase):
    """Peewee database that reuses the sqlite3 connection owned by UnshuffleDB.

    UnshuffleDB hands out one connection per thread, so the provider is
    resolved lazily on every ``_connect``: Peewee keeps its connection state
    in thread locals, which means each thread ends up bound to the very same
    connection the raw SQL paths use in that thread. Sharing one connection
    across threads would make a worker write and a follow-up write from
    another thread deadlock on the database lock.
    """

    def __init__(self, connection_provider: ConnectionProvider):
        self._connection_provider = connection_provider
        super().__init__(':memory:') # not real file for hacking InterfaceError

    def _connect(self):
        return self._connection_provider()

    def begin(self, lock_type=None):
        # The shared sqlite3 connection runs in the legacy isolation mode, so
        # raw DML executed outside Peewee opens an implicit transaction that
        # Peewee does not know about. Issuing BEGIN on top of it raises
        # "cannot start a transaction within a transaction"; joining the open
        # transaction matches what the raw ``with conn:`` blocks did before.
        if self.connection().in_transaction:
            return
        super().begin(lock_type)

    def close(self):
        # lifecycle in UnshuffleDB, not here
        pass

    def close_all(self):
        # lifecycle in UnshuffleDB, not here
        pass


class ConnectionBoundStore:
    """Store bound to UnshuffleDB's per-thread connection provider."""

    def __init__(self, connection_provider: ConnectionProvider):
        self._connection_provider = connection_provider

    @property
    def _connection(self) -> sqlite3.Connection:
        return self._connection_provider()


class PeeweeStore(ConnectionBoundStore):
    _db: SqliteDatabase | None = None

    def _initialize_db_proxy(self, connection_provider: ConnectionProvider):
        self._connection_provider = connection_provider
        self._db = ThreadAwareSqliteDatabase(connection_provider)
        with _PEEWEE_PROXY_LOCK:
            db_proxy.initialize(self._db)

    def _bind_db_proxy(self):
        """Re-point the shared db_proxy at this store's connection.

        db_proxy is a single process-wide singleton, so when multiple
        UnshuffleDB instances (each with its own sqlite3 connection) are
        alive at once, constructing the newest one silently steals the
        proxy from older ones. Call this before any Peewee query to make
        sure it still targets this store's connection.
        """
        if self._db is None:
            raise RuntimeError("Peewee store has not been initialized")
        if db_proxy.obj is not self._db:
            db_proxy.initialize(self._db)

    @contextmanager
    def _bound_database(self):
        with _PEEWEE_PROXY_LOCK:
            self._bind_db_proxy()
            yield self._db
