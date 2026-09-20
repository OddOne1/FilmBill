"""
Celery tasks for sending emails asynchronously.

Queues:
- email_high: Magic codes, invites (immediate)
- email_low: everything else that is mail (can be slightly delayed)

P0a has only the two high-priority account emails. Document dispatch
(SCOPE §5.3) adds to email_low from P2 on.
"""
from datetime import datetime
from pathlib import Path
from typing import Optional
from celery import shared_task
from jinja2 import Environment, FileSystemLoader

# Setup Jinja2 template environment
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,
)


def render_template(template_name: str, **context) -> str:
    """Render an email template with context."""
    context.setdefault("year", datetime.now().year)
    if "logo_url" not in context:
        # The site's custom logo, resolved once here so every template gets
        # it for free via base.html's header instead of each task passing it
        # through. None when no logo is configured -- base.html then keeps
        # the plain "FilmBill" wordmark. Imported inline, same as
        # _send_email below, to avoid circular imports.
        from ..services.email_service import email_logo_url
        context["logo_url"] = email_logo_url()
    template = jinja_env.get_template(template_name)
    return template.render(**context)


def _send_email(to_email: str, subject: str, html_body: str, text_body: Optional[str] = None) -> bool:
    """Send email using the email service.

    Constructs a fresh EmailService per send rather than using the module
    singleton: mail settings are now admin-editable at runtime, and a
    long-lived worker holding config resolved at import time would keep
    using stale credentials until the next deploy. The extra cost is one
    small indexed read against a single-row table, against an SMTP/SES
    round trip.
    """
    # Import here to avoid circular imports
    from ..services.email_service import EmailService
    return EmailService().send_email(to_email, subject, html_body, text_body)


# ============================================================================
# HIGH PRIORITY EMAILS (email_high queue)
# ============================================================================

@shared_task(bind=True, queue="email_high", max_retries=3, default_retry_delay=30)
def send_magic_code_email(self, to_email: str, code: str, expiry_minutes: int = 10, purpose: str = "login", contact_url: Optional[str] = None):
    """Send magic code email - high priority, immediate delivery."""
    try:
        if purpose == "two_factor":
            # Its own copy, deliberately. "Here is your login code"
            # and "you could not reach your authenticator" are different
            # messages to the person reading them: one is routine, the
            # other means something went wrong and is worth acting on if
            # they did not ask for it.
            subject = f"Your FilmBill verification code: {code}"
            html_body = render_template(
                "email/magic_code.html",
                subject=subject,
                code=code,
                expiry_minutes=expiry_minutes,
            )
            text_body = (
                f"Your FilmBill two-factor verification code is: {code}. "
                f"It expires in {expiry_minutes} minutes. "
                f"If you did not try to sign in, someone has your password — "
                f"change it and tell your admin."
            )
        elif purpose == "password_reset":
            subject = f"Password reset code: {code}"
            html_body = render_template(
                "email/password_reset_code.html",
                subject=subject,
                code=code,
                expiry_minutes=expiry_minutes,
                contact_url=contact_url or "",
            )
            text_body = f"Someone requested a password reset on your FilmBill account. Your code is: {code}. If this was not you, contact your admin. This code expires in {expiry_minutes} minutes."
        else:
            subject = f"Your FilmBill login code: {code}"
            html_body = render_template(
                "email/magic_code.html",
                subject=subject,
                code=code,
                expiry_minutes=expiry_minutes,
            )
            text_body = f"Your FilmBill login code is: {code}. This code expires in {expiry_minutes} minutes."        
        success = _send_email(to_email, subject, html_body, text_body)
        if not success:
            raise Exception("Email sending failed")
        return {"status": "sent", "to": to_email}
    except Exception as exc:
        self.retry(exc=exc)


@shared_task(bind=True, queue="email_high", max_retries=3, default_retry_delay=60)
def send_invite_email(
    self,
    to_email: str,
    inviter_name: str,
    org_name: str,
    invite_link: str,
    expiry_days: int = 7,
):
    """Send an invite email - high priority."""
    try:
        # "…join Acme on FilmBill", but just "…join FilmBill" when the
        # instance has not been renamed. Otherwise a default install mails
        # "join FilmBill on FilmBill".
        product = "FilmBill"
        where = product if org_name.strip() == product else f"{org_name} on {product}"
        subject = f"You've been invited to join {where}"
        html_body = render_template(
            "email/invite.html",
            subject=subject,
            inviter_name=inviter_name,
            org_name=org_name,
            invite_link=invite_link,
            expiry_days=expiry_days,
        )
        text_body = f"{inviter_name} has invited you to join {where}. Accept here: {invite_link}"
        
        success = _send_email(to_email, subject, html_body, text_body)
        if not success:
            raise Exception("Email sending failed")
        return {"status": "sent", "to": to_email}
    except Exception as exc:
        self.retry(exc=exc)
