"""Relay-local durable replay barrier. No PostgreSQL/admin/owner credentials.

The ledger must be provisioned separately on ONE exclusive persistent volume.
Missing/mismatched storage fails closed; this module NEVER creates, resets,
prunes or migrates a ledger. Independent replica disks are not supported.
"""
from pathlib import Path
import sqlite3

from core.content_ops.confirmation_dispatch_protocol import (
    ConfirmationBridgeError, DispatchEnvelope, require,
)
from core.content_ops.prompt_receipt import canonical_uuid

# Provisioning specification only. Runtime does not execute this DDL.
LEDGER_DDL = '''
create table identity (id text primary key not null, version integer not null check(version=1));
create table consumed (
    delivery_id text primary key not null,
    permission_id text not null unique,
    card_id text not null unique,
    request_sha256 text not null,
    release_sha text not null
);
create trigger consumed_no_update before update on consumed
begin select raise(abort, 'immutable'); end;
create trigger consumed_no_delete before delete on consumed
begin select raise(abort, 'immutable'); end;
'''


class SQLiteConfirmationReplayLedger:
    def __init__(self, *, enabled=False, path=None, ledger_id=None):
        self._enabled, self._path, self._id = enabled is True, path, ledger_id

    def consume(self, *, envelope):
        if not self._enabled:
            return False
        connection=None
        try:
            require(type(envelope) is DispatchEnvelope and canonical_uuid(self._id)
                and type(self._path) is str)
            p=Path(self._path)
            require(p.is_absolute() and p.is_file() and not p.is_symlink()
                and p.resolve()==p and p.stat().st_mode & 0o077 == 0)
            # mode=rw never creates a missing file, including deletion races.
            connection=sqlite3.connect(p.as_uri()+'?mode=rw',uri=True,timeout=1,isolation_level=None)
            connection.execute('pragma synchronous=FULL')
            require(connection.execute('pragma journal_mode').fetchone()[0] in ('delete','wal'))
            connection.execute('begin immediate')
            require(connection.execute('select id,version from identity').fetchall()==[(self._id,1)])
            # Validate the schema as well as the pinned volume identity before
            # relying on uniqueness/immutability; no permissive fallback.
            actual=connection.execute("select type,name,sql from sqlite_master where sql is not null order by name").fetchall()
            spec=sqlite3.connect(':memory:')
            try:
                spec.executescript(LEDGER_DDL)
                expected=spec.execute("select type,name,sql from sqlite_master where sql is not null order by name").fetchall()
            finally:
                spec.close()
            require(actual==expected)
            e=envelope
            old=connection.execute('select delivery_id from consumed where delivery_id=? or permission_id=? or card_id=?',
                (e.delivery_id,e.permission_id,e.card_id)).fetchone()
            if old is not None:
                connection.rollback()
                return False
            connection.execute('insert into consumed values(?,?,?,?,?)',
                (e.delivery_id,e.permission_id,e.card_id,e.request_sha256,e.release_sha))
            connection.commit()
            return True  # A commit error cannot return True.
        except Exception:
            raise ConfirmationBridgeError('confirmation_bridge_unknown') from None
        finally:
            if connection is not None:
                connection.close()
