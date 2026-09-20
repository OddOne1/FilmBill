"""
Notification preferences actually gate what is sent.

Inherited from FreeFrame, where the settings page had offered per-category
controls and an email frequency since it shipped and nothing on the API side
ever read them back: every configured email fired regardless of what the user
chose. A control that does not control anything is worse than no control —
it is a false statement.

**P0a has no categories.** FilmBill has no events to categorise yet, and
inventing billing-flavoured names for switches that gate nothing would
recreate that exact failure. `CATEGORIES` is therefore empty, and these tests
pin both halves of what that means: the frequency control is live, and every
category name is treated as unknown-and-not-gated rather than as off.

The per-category machinery is kept and tested against a hypothetical category
set, so the gate is known to work on the day P2's first category is added to
`CATEGORIES` and to the settings page in the same change.
"""
from unittest.mock import patch

import pytest

from apps.api.services import notification_prefs
from apps.api.services.notification_prefs import (
    category_setting,
    email_frequency,
    is_digest_frequency,
    is_known_category,
    should_create_notification,
    should_send_email,
    CATEGORIES,
)


class FakeUser:
    def __init__(self, preferences=None):
        self.preferences = preferences


def user_with(category=None, value=None, frequency=None):
    notifications = {}
    if category:
        notifications[category] = value
    if frequency:
        notifications["email_frequency"] = frequency
    return FakeUser({"notifications": notifications})


@pytest.fixture
def with_categories():
    """Stand in a category set, so the gating logic is exercised.

    Patched rather than hardcoded into the module: the empty set is the real
    P0a state and `TestP0aHasNoCategories` below asserts it. These tests are
    about the mechanism, which has to be known-good before anything depends
    on it.
    """
    categories = frozenset({"document_revised", "invoice_overdue"})
    with patch.object(notification_prefs, "CATEGORIES", categories):
        yield categories


class TestP0aHasNoCategories:
    def test_the_known_set_is_empty(self):
        """Pinned against the settings page. A category added on one side
        only is either a control that does nothing or a gate the user cannot
        see — both are the bug this module exists to prevent."""
        assert CATEGORIES == frozenset()

    def test_every_category_name_is_therefore_ungated(self):
        """Not "off". An unknown category must pass, or the day someone
        writes a notification before wiring its control, it vanishes."""
        u = user_with("document_revised", "all_off")
        assert is_known_category("document_revised") is False
        assert should_create_notification(u, "document_revised") is True
        assert should_send_email(u, "document_revised") is True

    def test_the_frequency_control_still_works_without_categories(self):
        """The one live control in P0a."""
        u = user_with(frequency="never")
        assert should_send_email(u, "anything") is False
        assert should_create_notification(u, "anything") is True


class TestDefaults:
    def test_untouched_user_gets_everything(self, with_categories):
        # THE regression this gate could cause, and the worst one: a user
        # who never opened the settings page has no stored preferences at
        # all, and must not be silently muted.
        for blank in (FakeUser(None), FakeUser({}), FakeUser({"notifications": {}})):
            for cat in with_categories:
                assert should_create_notification(blank, cat) is True
                assert should_send_email(blank, cat) is True

    def test_garbage_preferences_do_not_mute(self, with_categories):
        # preferences is free-form JSON; a non-dict must not read as "off".
        for junk in (FakeUser("nope"), FakeUser({"notifications": "nope"}),
                     FakeUser({"notifications": {"document_revised": "banana"}})):
            assert should_create_notification(junk, "document_revised") is True
            assert should_send_email(junk, "document_revised") is True

    def test_unknown_setting_value_falls_back_to_all_on(self, with_categories):
        u = user_with("document_revised", "sometimes")
        assert category_setting(u, "document_revised") == "all_on"


class TestThreeStates:
    def test_all_on_sends_both(self, with_categories):
        u = user_with("document_revised", "all_on")
        assert should_create_notification(u, "document_revised") is True
        assert should_send_email(u, "document_revised") is True

    def test_in_app_keeps_the_bell_and_drops_the_email(self, with_categories):
        # The whole point of the middle option.
        u = user_with("document_revised", "in_app")
        assert should_create_notification(u, "document_revised") is True
        assert should_send_email(u, "document_revised") is False

    def test_all_off_drops_both(self, with_categories):
        # "All Off" suppressing the in-app row too is the plain reading of
        # the label, and the only one that leaves "In-App Only" a distinct
        # option rather than a synonym.
        u = user_with("document_revised", "all_off")
        assert should_create_notification(u, "document_revised") is False
        assert should_send_email(u, "document_revised") is False

    def test_categories_are_independent(self, with_categories):
        u = FakeUser({"notifications": {
            "document_revised": "all_off",
            "invoice_overdue": "all_on",
        }})
        assert should_create_notification(u, "document_revised") is False
        assert should_send_email(u, "invoice_overdue") is True


class TestEmailFrequency:
    def test_never_suppresses_every_email_but_keeps_the_bell(self, with_categories):
        u = user_with("document_revised", "all_on", frequency="never")
        assert should_send_email(u, "document_revised") is False
        assert should_create_notification(u, "document_revised") is True

    @pytest.mark.parametrize("freq", ["15min", "hourly", "daily"])
    def test_digest_values_still_send_rather_than_vanish(self, freq, with_categories):
        # There is no digest system. Treating these as "never" would give the
        # user silence while they wait for a daily summary that is never
        # built — the same broken promise this module exists to fix, pointing
        # the other way. Flagged as unimplemented instead.
        u = user_with("document_revised", "all_on", frequency=freq)
        assert should_send_email(u, "document_revised") is True
        assert is_digest_frequency(u) is True

    def test_instant_is_not_a_digest(self):
        assert is_digest_frequency(user_with(frequency="instant")) is False
        assert email_frequency(FakeUser({})) == "instant"

    def test_frequency_instant_does_not_override_an_off_category(self, with_categories):
        u = user_with("document_revised", "all_off", frequency="instant")
        assert should_send_email(u, "document_revised") is False


class TestUnknownCategories:
    def test_a_type_with_no_control_is_not_gated(self, with_categories):
        # A notification type with no switch in the settings page must not be
        # suppressed by a setting that does not describe it.
        u = user_with("document_revised", "all_off")
        assert should_create_notification(u, "quote_expiring") is True
        assert should_create_notification(u, None) is True
        assert is_known_category("quote_expiring") is False

    def test_no_user_never_emails(self):
        # A recipient row that could not be loaded is not a licence to mail
        # an address we do not have.
        assert should_send_email(None, "document_revised") is False
        assert should_create_notification(None, "document_revised") is True
