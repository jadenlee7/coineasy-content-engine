"""Local, default-OFF authenticated original-response store; no live wiring.

The exclusive relay signs ONLY its own original HTTP response with a dedicated
injected response-authentication key. This key is not a bot token, webhook secret
or human-approval key. No key is generated, discovered or provisioned here.
Possession of this HMAC key is the trust boundary, not independent Telegram
attestation. Never expose sealing as an endpoint for caller-supplied responses.
The owner stores bounded bytes privately; no response/ID/secret is returned in
status receipts. Recorder and importer use separate transactions in that order.
"""
from dataclasses import astuple, dataclass
from datetime import timezone
import hashlib
import hmac

from core.content_ops.cancellation_markup import encode
from core.content_ops.cancellation_markup_confirmation import _time
from core.content_ops.confirmation_delivery_owner import StoredConfirmationSendReceipt
from core.content_ops.prompt_receipt import canonical_uuid, digest


class ConfirmationSourceError(ValueError):
    pass


def require(ok):
    if not ok:
        raise ConfirmationSourceError('confirmation_source_unconfirmed')


@dataclass(frozen=True, repr=False)
class SealedConfirmationResponse:
    receipt: StoredConfirmationSendReceipt
    relay_seal: str


def _receipt(value):
    require(type(value) is StoredConfirmationSendReceipt and canonical_uuid(value.delivery_id)
        and digest(value.request_sha256) and type(value.http_status) is int
        and 100 <= value.http_status <= 599 and type(value.raw_response) is bytes
        and 0 < len(value.raw_response) <= 32768)
    _time(value.observed_at)


class ConfirmationResponseAuthenticator:
    def __init__(self, key):
        require(type(key) is bytes and len(key) >= 32)
        self._key = key

    def _mac(self, receipt):
        _receipt(receipt)
        value=encode(['confirmation-relay-response@1',receipt.delivery_id,
            receipt.request_sha256,receipt.http_status,hashlib.sha256(receipt.raw_response).hexdigest(),
            receipt.observed_at.astimezone(timezone.utc).isoformat(timespec='microseconds')]).encode()
        return hmac.new(self._key,value,hashlib.sha256).hexdigest()

    def seal(self, *, enabled=False, receipt=None):
        if enabled is not True: return None
        try:
            return SealedConfirmationResponse(receipt,self._mac(receipt))
        except Exception:
            raise ConfirmationSourceError('confirmation_source_unconfirmed') from None

    def verify(self, envelope):
        try:
            require(type(envelope) is SealedConfirmationResponse and digest(envelope.relay_seal))
            require(hmac.compare_digest(envelope.relay_seal,self._mac(envelope.receipt)))
            return envelope.receipt
        except Exception:
            raise ConfirmationSourceError('confirmation_source_unconfirmed') from None


_COLUMNS='delivery_id::text,request_sha256,http_status,raw_response,observed_at,response_sha256,relay_seal'


def _values(envelope):
    receipt=envelope.receipt
    return (*astuple(receipt),hashlib.sha256(receipt.raw_response).hexdigest(),envelope.relay_seal)


def _same(row, expected):
    require(type(row) is tuple and len(row)==len(expected)
            and all(type(a) is type(b) and a==b for a,b in zip(row,expected)))


class PostgresConfirmationSourceStore:
    """Insert once under reserved-delivery lock; unknown commit never retries.

    HMAC is checked BEFORE any DB access. This records authenticated transport
    evidence, including failure responses, NOT matched delivery or approval.
    Bodies/destinations are validated separately by the existing importer.
    """
    def __init__(self, connection_factory, *, authenticator=None):
        self._factory,self._auth = connection_factory,authenticator

    def record(self, *, enabled=False, envelope=None):
        if enabled is not True: return None
        try:
            require(type(self._auth) is ConfirmationResponseAuthenticator)
            receipt=self._auth.verify(envelope)
            expected=_values(envelope)
            with self._factory() as connection:
                require(connection.autocommit is False)
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute('''select request_sha256,reserved_at,expires_at
                        from private.content_ops_button_confirmation_deliveries
                        where delivery_id=%s::uuid for update''',(receipt.delivery_id,))
                    attempt=cursor.fetchone()
                    require(type(attempt) is tuple and len(attempt)==3 and attempt[0]==receipt.request_sha256)
                    require(_time(attempt[1])<=_time(receipt.observed_at)<_time(attempt[2]))
                    cursor.execute('select clock_timestamp()')
                    require(_time(receipt.observed_at)<=_time(cursor.fetchone()[0]))
                    cursor.execute(f'''select {_COLUMNS} from private.content_ops_button_confirmation_sources
                        where delivery_id=%s::uuid for share''',(receipt.delivery_id,))
                    existing=cursor.fetchone()
                    if existing is None:
                        cursor.execute(f'''insert into private.content_ops_button_confirmation_sources
                            (delivery_id,request_sha256,http_status,raw_response,observed_at,response_sha256,relay_seal)
                            values(%s::uuid,%s,%s,%s,%s,%s,%s) returning {_COLUMNS}''',expected)
                        _same(cursor.fetchone(),expected)
                    else:
                        _same(existing,expected)
            return dict(status='confirmation_source_recorded',reused=existing is not None,
                        execution_authorized=False)
        except Exception:
            raise ConfirmationSourceError('confirmation_source_unconfirmed') from None


class PostgresConfirmationSourceReader:
    """Trusted receipt_loader for the existing importer, SAME supplied cursor.

    Verify bounded bytes and stored MAC on every read. No cached fallback key,
    own transaction, environment access or reconstructed provider response.
    Missing/corrupt records fail closed and cannot become a success receipt.
    """
    def __init__(self, *, enabled=False, authenticator=None):
        self._enabled,self._auth = enabled,authenticator

    def __call__(self, *, cursor=None, delivery_id=None, request_sha256=None):
        if self._enabled is not True: return None
        try:
            require(type(self._auth) is ConfirmationResponseAuthenticator
                    and canonical_uuid(delivery_id) and digest(request_sha256))
            cursor.execute(f'''select {_COLUMNS} from private.content_ops_button_confirmation_sources
                where delivery_id=%s::uuid for share''',(delivery_id,))
            row=cursor.fetchone()
            require(type(row) is tuple and len(row)==7 and row[0]==delivery_id and row[1]==request_sha256)
            receipt=StoredConfirmationSendReceipt(*row[:5])
            _receipt(receipt)
            require(row[5]==hashlib.sha256(receipt.raw_response).hexdigest())
            return self._auth.verify(SealedConfirmationResponse(receipt,row[6]))
        except Exception:
            raise ConfirmationSourceError('confirmation_source_unconfirmed') from None
