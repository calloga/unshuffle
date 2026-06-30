import sqlite3

from peewee import SqliteDatabase

from unshuffle.persistence.schema.models import db_proxy


class ThreadAwareSqliteDatabase(SqliteDatabase):
    def __init__(self, connection: sqlite3.Connection):
        self._existing_connection = connection
        super().__init__(':memory:') # not real file for hacking InterfaceError

    def _connect(self):
        return self._existing_connection

    def close(self):
        # lifecycle in UnshuffleDB, not here
        pass

    def close_all(self):
        # lifecycle in UnshuffleDB, not here
        pass

class PeeweeStore:
    _db:SqliteDatabase|None = None

    def _initialize_db_proxy(self, connection):
        self._db = ThreadAwareSqliteDatabase(connection)
        db_proxy.initialize(self._db)