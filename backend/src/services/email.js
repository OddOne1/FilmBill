const nodemailer = require("nodemailer");

const transporter = nodemailer.createTransport({
  host: process.env.SMTP_HOST || "smtp.office365.com",
  port: parseInt(process.env.SMTP_PORT || "587"),
  secure: false,
  auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASS },
  tls: { ciphers: "SSLv3" },
});

async function sendMail({ to, subject, html, attachments = [] }) {
  return transporter.sendMail({
    from: `"${process.env.COMPANY_NAME || "FilmBill"}" <${process.env.SMTP_FROM}>`,
    to, subject, html, attachments,
  });
}

const TEXTS = {
  de: {
    invoice: "Rechnung", quote: "Angebot",
    greeting: "Guten Tag", attached: "anbei übermitteln wir Ihnen",
    overTotal: "über", dueDate: "Fälligkeitsdatum",
    validUntil: "Gültig bis", invSubj: (n)=>`Rechnung ${n}`, qtSubj: (n)=>`Angebot ${n}`,
    paymentReminder: "Zahlungserinnerung", remind: (lv)=>`${lv}. Mahnung`,
    pleaseRefer: "wir erlauben uns, Sie auf den noch offenen Betrag Ihrer Rechnung",
    toInform: "hinzuweisen.", invAmount: "Rechnungsbetrag",
    overdueSince: "Fällig seit", lateFee: "Mahngebühr",
    lateInterest: "Verzugszinsen", lateInterestNote: "× Basiszinssatz",
    additional: "Bei weiterem Zahlungsverzug werden weitere Mahngebühren verrechnet.",
    pleasePay: "Bitte überweisen Sie den ausstehenden Betrag umgehend auf unser Konto.",
    iban: "IBAN", bic: "BIC",
    ifPaid: "Falls Sie bereits bezahlt haben, bitten wir Sie, dieses Schreiben als gegenstandslos zu betrachten.",
    pwResetTitle: "Passwort zurücksetzen", pwResetGreet: "Hallo",
    pwResetText: "klicken Sie auf den folgenden Link um Ihr Passwort zurückzusetzen. Der Link ist 1 Stunde gültig.",
    pwResetBtn: "Passwort zurücksetzen",
    pwResetIgnore: "Falls Sie diese Anfrage nicht gestellt haben, ignorieren Sie diese E-Mail.",
    pwResetSubj: "Passwort zurücksetzen – FilmBill",
    inviteSubj: (c)=>`Einladung zu ${c}`, inviteText: "Sie wurden zu",
    inviteText2: "eingeladen.", inviteLogin: "Login",
    inviteTempPw: "Temporäres Passwort", inviteBtn: "Jetzt einloggen",
    inviteChangeNote: "Bitte ändern Sie Ihr Passwort nach dem ersten Login.",
  },
  en: {
    invoice: "Invoice", quote: "Quote",
    greeting: "Dear", attached: "please find attached",
    overTotal: "in the amount of", dueDate: "Due date",
    validUntil: "Valid until", invSubj: (n)=>`Invoice ${n}`, qtSubj: (n)=>`Quote ${n}`,
    paymentReminder: "Payment Reminder", remind: (lv)=>`${lv} Reminder`,
    pleaseRefer: "we would like to remind you of the outstanding amount of invoice",
    toInform: ".", invAmount: "Invoice amount",
    overdueSince: "Due since", lateFee: "Late fee",
    lateInterest: "Interest", lateInterestNote: "× base rate",
    additional: "Further late fees will apply if payment is not received soon.",
    pleasePay: "Please transfer the outstanding amount to our account immediately.",
    iban: "IBAN", bic: "BIC",
    ifPaid: "If you have already paid, please disregard this notice.",
    pwResetTitle: "Reset Password", pwResetGreet: "Hi",
    pwResetText: "click the link below to reset your password. The link is valid for 1 hour.",
    pwResetBtn: "Reset Password",
    pwResetIgnore: "If you did not request this, please ignore this email.",
    pwResetSubj: "Reset Password – FilmBill",
    inviteSubj: (c)=>`Invitation to ${c}`, inviteText: "You have been invited to",
    inviteText2: ".", inviteLogin: "Login",
    inviteTempPw: "Temporary password", inviteBtn: "Sign in now",
    inviteChangeNote: "Please change your password after first login.",
  },
};

function tpl(content, primary='#f5c842', bg='#0f0f0f') {
  return `<div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;color:#1a1a1a">
    <div style="background:${bg};padding:24px 32px;border-radius:8px 8px 0 0">
      <h1 style="color:${primary};margin:0;font-size:20px;letter-spacing:2px">${process.env.COMPANY_NAME||"FilmBill"}</h1>
    </div>
    <div style="background:#fff;padding:32px;border:1px solid #e8e8e8;border-top:none">${content}</div>
    <div style="background:#f9f9f9;padding:16px 32px;border-radius:0 0 8px 8px;border:1px solid #e8e8e8;border-top:none">
      <p style="color:#999;font-size:12px;margin:0">${process.env.COMPANY_NAME} · ${process.env.SMTP_FROM}</p>
    </div></div>`;
}

async function sendInvoiceEmail(invoice, customer, attachments = []) {
  const lang = invoice.doc_language||'de';
  const T = TEXTS[lang]||TEXTS.de;
  const isQuote = invoice.doc_type === 'quote';
  const label = isQuote ? T.quote : T.invoice;
  const locale = lang==='de' ? 'de-AT' : 'en-US';
  const total = Number(invoice.total).toLocaleString(locale, { style:'currency', currency:'EUR' });
  const html = tpl(`
    <p>${T.greeting} ${customer.name},</p>
    <p>${T.attached} <strong>${label} ${invoice.doc_no}</strong> ${T.overTotal} <strong>${total}</strong>.</p>
    ${!isQuote ? `<p>${T.dueDate}: <strong>${new Date(invoice.due_date).toLocaleDateString(locale)}</strong></p>` : ''}
    ${isQuote && invoice.valid_until ? `<p>${T.validUntil}: <strong>${new Date(invoice.valid_until).toLocaleDateString(locale)}</strong></p>` : ''}
    ${invoice.intro_text ? `<p>${invoice.intro_text}</p>` : ''}
    ${invoice.notes_text ? `<hr style="border:none;border-top:1px solid #eee;margin:24px 0"><p>${invoice.notes_text}</p>` : ''}
    <p style="color:#666;font-size:13px;margin-top:24px">${invoice.terms||''}</p>
  `);
  return sendMail({ to: customer.email, subject: `${label} ${invoice.doc_no} – ${process.env.COMPANY_NAME}`, html, attachments });
}

async function sendReminderEmail(invoice, customer, level, fee, interest, settings) {
  const lang = invoice.doc_language||'de';
  const T = TEXTS[lang]||TEXTS.de;
  const isReminder = level === 0;
  const title = isReminder ? T.paymentReminder : T.remind(level);
  const locale = lang==='de' ? 'de-AT' : 'en-US';
  const fmt = (v) => Number(v).toLocaleString(locale, { style:'currency', currency:'EUR' });
  const html = tpl(`
    <p>${T.greeting} ${customer.name},</p>
    <p>${T.pleaseRefer} <strong>${invoice.doc_no}</strong> ${T.toInform}</p>
    <p><strong>${T.invAmount}:</strong> ${fmt(invoice.total)}<br>
       <strong>${T.overdueSince}:</strong> ${new Date(invoice.due_date).toLocaleDateString(locale)}</p>
    ${fee>0 ? `<p><strong>${T.lateFee}:</strong> ${fmt(fee)}</p>` : ''}
    ${interest>0 ? `<p><strong>${T.lateInterest} (${settings.late_interest_rate} ${T.lateInterestNote}):</strong> ${fmt(interest)}</p>` : ''}
    ${!isReminder && fee>0 ? `<p style="background:#fff3cd;padding:12px;border-radius:4px;font-size:13px">${T.additional}</p>` : ''}
    <p>${T.pleasePay}</p>
    <p><strong>${T.iban}:</strong> ${settings.company_iban||'—'}<br>
       <strong>${T.bic}:</strong> ${settings.company_bic||'—'}</p>
    <p>${T.ifPaid}</p>
  `);
  return sendMail({ to: customer.email, subject: `${title} – ${invoice.doc_no}`, html });
}

async function sendPasswordReset(user, resetUrl) {
  const T = TEXTS.de;
  const html = tpl(`
    <h2>${T.pwResetTitle} – FilmBill</h2>
    <p>${T.pwResetGreet} ${user.name},</p>
    <p>${T.pwResetText}</p>
    <a href="${resetUrl}" style="display:inline-block;background:#f5c842;color:#0f0f0f;padding:12px 24px;border-radius:4px;text-decoration:none;font-weight:bold">${T.pwResetBtn}</a>
    <p style="color:#999;font-size:13px;margin-top:24px">${T.pwResetIgnore}</p>
  `);
  return sendMail({ to:user.email, subject:T.pwResetSubj, html });
}

async function sendUserInvite(user, inviteUrl, tempPassword) {
  const T = TEXTS.de;
  const c = process.env.COMPANY_NAME||"FilmBill";
  const html = tpl(`
    <p>Hallo ${user.name},</p>
    <p>${T.inviteText} <strong>${c}</strong> ${T.inviteText2}</p>
    <p><strong>${T.inviteLogin}:</strong> ${user.email}<br><strong>${T.inviteTempPw}:</strong> ${tempPassword}</p>
    <a href="${inviteUrl}" style="display:inline-block;background:#f5c842;color:#0f0f0f;padding:12px 24px;border-radius:4px;text-decoration:none;font-weight:bold">${T.inviteBtn}</a>
    <p style="color:#999;font-size:13px;margin-top:24px">${T.inviteChangeNote}</p>
  `);
  return sendMail({ to:user.email, subject:T.inviteSubj(c), html });
}

module.exports = { sendMail, sendInvoiceEmail, sendReminderEmail, sendPasswordReset, sendUserInvite };
