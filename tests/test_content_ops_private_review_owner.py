"""Cheap boundary checks complement disposable PostgreSQL behavioral tests."""
from unittest.mock import Mock

import pytest

from core.content_ops.private_review_owner import PostgresPrivateReviewOwner
from core.content_ops.polling_review_adapter import PollingReviewAdapter
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import ReviewIngressError
from test_content_ops_review_edit_ingress import POLICY


@pytest.mark.parametrize('enabled',[False,None,1,'true'])
def test_disabled_owner_does_not_connect_or_parse(enabled):
    connect=Mock(side_effect=AssertionError('unexpected I/O'))
    owner=PostgresPrivateReviewOwner(connect,EditBindings(b'synthetic-binding-key-1234567890123456'))
    assert owner.handle_update(enabled=enabled,raw_body=object())['status']=='disabled'
    connect.assert_not_called()


def test_unauthenticated_request_cannot_connect():
    connect=Mock(side_effect=AssertionError('unexpected I/O'))
    owner=PostgresPrivateReviewOwner(connect,EditBindings(b'synthetic-binding-key-1234567890123456'))
    with pytest.raises(ReviewIngressError):
        owner.handle_update(enabled=True,raw_body=b'{}',policy=POLICY,now=123)
    connect.assert_not_called()


def test_no_legacy_owner_fallback_configuration():
    owner=PostgresPrivateReviewOwner(Mock(),EditBindings(b'synthetic-binding-key-1234567890123456'))
    with pytest.raises(ReviewIngressError):
        PollingReviewAdapter(enabled=True,policy=POLICY,review_owner=Mock(),transactional_review_owner=owner)
