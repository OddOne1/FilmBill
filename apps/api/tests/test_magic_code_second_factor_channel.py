"""A magic-code login may not be completed by an emailed second factor.

The two primary credentials are not interchangeable once the second factor
is also a channel. A password proves something the user KNOWS; a magic code
proves control of their MAILBOX. Mail the second factor to that same mailbox
and the account is protected by one channel wearing two hats — whoever can
read the inbox holds both halves.

FreeFrame's pending token carried no record of which credential produced it,
so `/auth/2fa/send-email-fallback` cheerfully mailed a second code to the
address that had just been used as the first factor. Found by running the
flow end to end against Mailpit during P0a acceptance, not by reading the
code: every unit test passed, because each half is correct on its own.

The rule, in three places:
  * login by magic code does not AUTO-SEND an email code to an
    email-enrolled user (routers/auth.py::_login_outcome)
  * it refuses to send one ON REQUEST (/auth/2fa/send-email-fallback)
  * and it refuses to ENROL email as the second factor mid-login
    (/auth/2fa/setup)

A password login is unaffected in all three.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from apps.api.models.user import UserStatus
from apps.api.services import totp_service
from apps.api.services.auth_service import (
    VIA_MAGIC_CODE,
    VIA_PASSWORD,
    create_2fa_pending_token,
    pending_token_via,
)

_REDIS_OK = "apps.api.routers.auth.redis_verify_magic_code"
_SEND_CODE = "apps.api.routers.auth._send_2fa_email_code"


def _user(*, method="email"):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.email = "u@example.com"
    u.status = UserStatus.active
    u.deleted_at = None
    u.password_hash = "$2b$12$fake"
    u.email_verified = True
    u.two_factor_enabled = True
    u.two_factor_method = method
    u.totp_secret_encrypted = (
        totp_service.encrypt_secret(totp_service.generate_totp_secret())
        if method == "totp"
        else None
    )
    u.backup_codes_hashed = None
    return u


class TestThePendingTokenRecordsItsOrigin:
    def test_a_password_login_is_marked_as_such(self, client, mock_db):
        user = _user(method="totp")
        mock_db.first.return_value = user

        with patch("apps.api.routers.auth.verify_password", return_value=True), \
             patch("apps.api.routers.auth.require_2fa_enabled", return_value=False):
            resp = client.post(
                "/auth/login",
                json={"email": user.email, "password": "hunter2hunter2"},
            )

        assert pending_token_via(resp.json()["pending_token"]) == VIA_PASSWORD

    def test_a_magic_code_login_is_marked_as_such(self, client, mock_db):
        user = _user(method="totp")
        mock_db.first.return_value = user

        with patch(_REDIS_OK, return_value=(True, "")), \
             patch("apps.api.routers.auth.require_2fa_enabled", return_value=False):
            resp = client.post(
                "/auth/verify-magic-code",
                json={"email": user.email, "code": "123456"},
            )

        assert pending_token_via(resp.json()["pending_token"]) == VIA_MAGIC_CODE

    def test_a_token_with_no_claim_reads_as_password(self):
        """The permissive default, and the right one: only the magic-code
        path mints the restricted kind, and it always sets the claim."""
        assert pending_token_via(create_2fa_pending_token(str(uuid.uuid4()))) == VIA_PASSWORD


class TestNoAutoSendAfterAMagicCode:
    def test_an_email_enrolled_user_is_not_mailed_a_second_code(self, client, mock_db):
        """THE regression. Two codes in one inbox is one factor, twice."""
        user = _user(method="email")
        mock_db.first.return_value = user

        with patch(_REDIS_OK, return_value=(True, "")), \
             patch("apps.api.routers.auth.require_2fa_enabled", return_value=False), \
             patch(_SEND_CODE) as send:
            resp = client.post(
                "/auth/verify-magic-code",
                json={"email": user.email, "code": "123456"},
            )

        body = resp.json()
        assert body["requires_2fa"] is True
        assert body["email_code_sent"] is False
        send.assert_not_called()

    def test_a_password_login_still_mails_one(self, client, mock_db):
        """The restriction is about the CHANNEL, not about 2FA in general.
        An email-enrolled user signing in with their password is the case
        the automatic send exists for, and it must be untouched."""
        user = _user(method="email")
        mock_db.first.return_value = user

        with patch("apps.api.routers.auth.verify_password", return_value=True), \
             patch("apps.api.routers.auth.require_2fa_enabled", return_value=False), \
             patch(_SEND_CODE, return_value=True) as send:
            resp = client.post(
                "/auth/login",
                json={"email": user.email, "password": "hunter2hunter2"},
            )

        assert resp.json()["email_code_sent"] is True
        send.assert_called_once()


class TestTheFallbackIsRefusedOnThatPath:
    def test_requesting_one_after_a_magic_code_is_refused(self, client, mock_db):
        user = _user(method="totp")
        mock_db.first.return_value = user
        pending = create_2fa_pending_token(str(user.id), via=VIA_MAGIC_CODE)

        with patch(_SEND_CODE) as send:
            resp = client.post(
                "/auth/2fa/send-email-fallback",
                json={"pending_token": pending},
            )

        assert resp.status_code == 403
        send.assert_not_called()

    def test_the_refusal_says_what_to_do_instead(self, client, mock_db):
        """A dead end the user cannot get out of is worse than the risk it
        avoids, so the message names both ways forward."""
        user = _user(method="totp")
        mock_db.first.return_value = user
        pending = create_2fa_pending_token(str(user.id), via=VIA_MAGIC_CODE)

        with patch(_SEND_CODE):
            detail = client.post(
                "/auth/2fa/send-email-fallback",
                json={"pending_token": pending},
            ).json()["detail"].lower()

        assert "backup code" in detail
        assert "password" in detail

    def test_requesting_one_after_a_password_login_still_works(self, client, mock_db):
        """The lost-authenticator path, which is what this endpoint is for."""
        user = _user(method="totp")
        mock_db.first.return_value = user
        pending = create_2fa_pending_token(str(user.id), via=VIA_PASSWORD)

        with patch(_SEND_CODE, return_value=True) as send:
            resp = client.post(
                "/auth/2fa/send-email-fallback",
                json={"pending_token": pending},
            )

        assert resp.status_code == 200
        send.assert_called_once()


class TestEnrolmentCannotChooseTheSameChannel:
    def test_enrolling_email_mid_magic_code_login_is_refused(
        self, client, mock_db, staged_2fa_setup
    ):
        """Otherwise the very next step reads the confirmation code out of
        the mailbox that was already the first factor."""
        user = _user(method="totp")
        user.two_factor_enabled = False
        user.two_factor_method = None
        user.totp_secret_encrypted = None
        mock_db.first.return_value = user
        pending = create_2fa_pending_token(str(user.id), via=VIA_MAGIC_CODE)

        with patch(_SEND_CODE) as send:
            resp = client.post(
                "/auth/2fa/setup",
                json={"pending_token": pending, "method": "email"},
            )

        assert resp.status_code == 400
        send.assert_not_called()
        assert staged_2fa_setup == {}

    def test_enrolling_totp_mid_magic_code_login_is_fine(
        self, client, mock_db, staged_2fa_setup
    ):
        """An authenticator is a different channel, which is the whole
        point — this path must stay open or a forced enrolment after a
        magic-code login would have nowhere to go."""
        user = _user(method="totp")
        user.two_factor_enabled = False
        user.two_factor_method = None
        user.totp_secret_encrypted = None
        mock_db.first.return_value = user
        pending = create_2fa_pending_token(str(user.id), via=VIA_MAGIC_CODE)

        resp = client.post(
            "/auth/2fa/setup",
            json={"pending_token": pending, "method": "totp"},
        )

        assert resp.status_code == 200
        assert resp.json()["method"] == "totp"
        assert staged_2fa_setup

    def test_enrolling_email_from_a_password_login_is_fine(
        self, client, mock_db, staged_2fa_setup
    ):
        user = _user(method="totp")
        user.two_factor_enabled = False
        user.two_factor_method = None
        user.totp_secret_encrypted = None
        mock_db.first.return_value = user
        pending = create_2fa_pending_token(str(user.id), via=VIA_PASSWORD)

        with patch(_SEND_CODE, return_value=True):
            resp = client.post(
                "/auth/2fa/setup",
                json={"pending_token": pending, "method": "email"},
            )

        assert resp.status_code == 200
        assert resp.json()["method"] == "email"
