"""Stored-action routing for the existing poller; no error-based fallback."""
from core.content_ops.review_ingress import ReviewIngressError
from core.content_ops.review_edit_ingress import verified_edit_reply, PostgresEditReplyOwner, _receipt
from core.content_ops.banner_feedback import PostgresBannerFeedbackOwner, feedback_receipt


class PostgresPrivateReplyOwner:
    def __init__(self, connection_factory, *, bindings):
        self._connect=connection_factory
        self._copy=PostgresEditReplyOwner(connection_factory)
        self._banner=PostgresBannerFeedbackOwner(connection_factory,bindings=bindings)

    def handle_update(self, *, enabled=False, raw_body=None, headers=(), policy=None,
                      bindings=None, now=None):
        if enabled is not True:
            return {'status':'disabled','execution_authorized':False}
        event=verified_edit_reply(raw_body=raw_body,headers=headers,policy=policy,bindings=bindings,now=now)
        try:
            # Routing only: selected owner independently revalidates and locks
            # the registration, action, actor, version and expiry before writes.
            with self._connect() as connection:
                if connection.autocommit is not False: raise ValueError()
                with connection.cursor() as cur:
                    cur.execute('''select a.action from private.content_ops_button_edit_prompts p
                        join private.content_ops_button_actions a
                          on a.review_id=p.review_id and a.idempotency_key=p.edit_action_key
                        where p.bot_binding=%s and p.room_binding=%s and p.message_binding=%s
                          and p.actor_id=%s::uuid and a.actor_id=p.actor_id''',
                        (event.bot_binding,event.room_binding,event.message_binding,event.actor_id))
                    rows=cur.fetchall()
                    if len(rows)!=1: raise ValueError()
                    action=rows[0][0]
            if action=='edit_banner':
                return feedback_receipt(self._banner.save_banner_feedback(event))
            if action in {'edit_telegram','edit_x'}:
                return _receipt(self._copy.save_edit_reply(event))
            raise ValueError()
        except Exception:
            raise ReviewIngressError('review_edit_outcome_unknown') from None
