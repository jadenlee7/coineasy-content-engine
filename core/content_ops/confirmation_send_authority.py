"""Read-only exact private-prompt permission, independent of the requested click.

No UI authority, secret discovery or live route. The separate command registrar
is local and unmounted; only its authenticated exact operator command may create
this owner record. A delivery reservation or content/markup approval is NOT this
permission. All owner reads use the current card guard's transaction cursor.
"""
from dataclasses import astuple, dataclass
from datetime import datetime, timezone
import hashlib

from core.content_ops.cancellation_markup import _check_plan
from core.content_ops.cancellation_markup_owner import _inputs
from core.content_ops.cancellation_markup_authority import OriginalControlEvidence, verify_control_evidence
from core.content_ops.cancellation_markup_confirmation import MarkupConfirmationTarget, _target
from core.content_ops.cancellation_markup_guard import PostgresCancellationMarkupGuard
from core.content_ops.confirmation_delivery_owner import confirmation_send_request
from core.content_ops.prompt_receipt import canonical_uuid


@dataclass(frozen=True, repr=False)
class ConfirmationSendPermission:
    permission_id: str
    delivery_id: str
    card_id: str
    actor_id: str
    human_binding: str
    target_binding: str
    request_sha256: str
    action: str
    authorized_at: datetime
    expires_at: datetime
    active: bool


class PostgresConfirmationSendReader:
    def __init__(self, *, enabled=False): self._enabled = enabled is True

    def __call__(self, *, cursor, card_id, delivery_id, actor_id):
        if not self._enabled: return None, None
        try:
            if not all(canonical_uuid(v) for v in (card_id,delivery_id,actor_id)):
                raise ValueError()
            cursor.execute('''select card_id::text,receipt_sha256,parent_binding_sha256,
                bot_id,chat_id,message_id,thread_id,delivered_at,message_date,text_sha256,
                entities_json,markup_json from private.content_ops_button_control_evidence
                where card_id=%s::uuid for share''',(card_id,))
            evidence = cursor.fetchone()
            if evidence is None: return None, None
            cursor.execute('''select permission_id::text,delivery_id::text,card_id::text,
                actor_id::text,human_binding,target_binding,request_sha256,action,
                authorized_at,expires_at,active from private.content_ops_button_confirmation_send_permissions
                where card_id=%s::uuid and delivery_id=%s::uuid and actor_id=%s::uuid for share''',
                (card_id,delivery_id,actor_id))
            permission = cursor.fetchone()
            if permission is None: return None, None
            return OriginalControlEvidence(*evidence), ConfirmationSendPermission(*permission)
        except Exception:
            raise ValueError('confirmation_send_authority_unconfirmed') from None


class ExactConfirmationSendAuthority:
    def __init__(self, *, enabled=False, target=None, delivery_id=None, reader=None):
        self._enabled, self._target, self._delivery = enabled is True, target, delivery_id
        self._reader = reader

    def __call__(self, *, cursor=None, locked_card=None, now=None, **identity):
        if not self._enabled: return False
        try:
            _inputs(**identity); _target(self._target)
            def require(value):
                if not value: raise ValueError()
            require(canonical_uuid(self._delivery) and callable(self._reader) and cursor is not None)
            plan, b, t = identity['plan'], identity['bindings'], self._target
            require(type(now) is datetime and now.utcoffset() is not None)
            _check_plan(plan,b,int(now.timestamp()))
            require(type(locked_card) is dict and locked_card.get('id')==plan.card_id
                and locked_card.get('active') is True)
            require(t == MarkupConfirmationTarget(t.approval_id,identity['attempt_id'],plan.card_id,
                identity['actor_id'],b.digest('human',plan.bot_id,identity['human_id']),plan.seal,
                t.original_receipt_sha256,plan.started_at,plan.expires_at))
            evidence, p = self._reader(cursor=cursor,card_id=plan.card_id,
                delivery_id=self._delivery,actor_id=identity['actor_id'])
            verify_control_evidence(evidence,plan,b,locked_card)
            require(type(p) is ConfirmationSendPermission and canonical_uuid(p.permission_id))
            require(p.delivery_id==self._delivery and p.card_id==plan.card_id
                and p.actor_id==identity['actor_id'] and p.human_binding==t.human_binding
                and t.original_receipt_sha256==evidence.receipt_sha256
                and p.target_binding==b.digest('confirmation-delivery-target@1',*astuple(t))
                and p.request_sha256==hashlib.sha256(confirmation_send_request(t,plan,b)).hexdigest()
                and p.action=='send_private_confirmation@1' and p.active is True)
            require(all(type(x) is datetime and x.utcoffset() is not None
                for x in (p.authorized_at,p.expires_at)))
            require(datetime.fromtimestamp(plan.started_at,timezone.utc)<=p.authorized_at<=now
                <p.expires_at==datetime.fromtimestamp(plan.expires_at,timezone.utc))
            return True
        except Exception:
            return False


class PostgresConfirmationSendGuard:
    """Reuse active-card/reviewer serialization, with the distinct send authority."""
    def __init__(self, factory, *, enabled=False, target=None, delivery_id=None):
        self.target, self.delivery_id = target, delivery_id
        self._guard = PostgresCancellationMarkupGuard(factory,enabled=enabled,
            authorize_plan=ExactConfirmationSendAuthority(enabled=enabled,target=target,
                delivery_id=delivery_id,reader=PostgresConfirmationSendReader(enabled=enabled)))

    def hold(self, **identity): return self._guard.hold(**identity)
