const router = require("express").Router();
const db = require("../db");
const { authenticate } = require("../middleware/auth");
const { sendInvoiceEmail, sendReminderEmail } = require("../services/email");

router.use(authenticate);

async function getSettings() {
  const { rows } = await db.query("SELECT key, value FROM settings");
  const s = {}; rows.forEach(r => { s[r.key] = r.value; }); return s;
}

async function recalc(invoiceId) {
  const { rows: items } = await db.query(
    "SELECT units, days, unit_price, tax_rate FROM invoice_items WHERE invoice_id=$1", [invoiceId]
  );
  const { rows: [inv] } = await db.query("SELECT * FROM invoices WHERE id=$1", [invoiceId]);
  const s = await getSettings();
  let subtotal = 0, tax_total = 0;
  for (const it of items) {
    const line = Number(it.units) * Number(it.days) * Number(it.unit_price);
    subtotal += line;
    if (inv.manual_tax == null && s.tax_mode !== 'manual') tax_total += line * Number(it.tax_rate);
  }
  if (inv.manual_tax != null) tax_total = Number(inv.manual_tax);
  const prod_pct = Number(inv.production_fee_pct || 0) / 100;
  const production_fee_amount = subtotal * prod_pct;
  let discount_amount = 0;
  if (inv.discount_type === 'fixed')   discount_amount = Number(inv.discount_value || 0);
  if (inv.discount_type === 'percent') discount_amount = subtotal * Number(inv.discount_value || 0) / 100;
  const total = subtotal + tax_total + production_fee_amount - discount_amount;
  await db.query(
    `UPDATE invoices SET subtotal=$1,tax_total=$2,production_fee_amount=$3,discount_amount=$4,total=$5 WHERE id=$6`,
    [subtotal.toFixed(2), tax_total.toFixed(2), production_fee_amount.toFixed(2), discount_amount.toFixed(2), total.toFixed(2), invoiceId]
  );
}

async function nextDocNo(docType) {
  const s = await getSettings();
  const year = new Date().getFullYear();
  if (docType === 'quote') {
    const { rows } = await db.query("SELECT nextval('quote_seq') AS n");
    return `${s.quote_prefix||'AN'}-${year}-${rows[0].n.toString().padStart(4,'0')}`;
  } else {
    const { rows } = await db.query("SELECT nextval('invoice_seq') AS n");
    return `${s.invoice_prefix||'RE'}-${year}-${rows[0].n.toString().padStart(4,'0')}`;
  }
}

router.get("/", async (req, res, next) => {
  try {
    const { status, customer_id, q, doc_type } = req.query;
    const conditions = [], params = [];
    if (doc_type)    { conditions.push(`i.doc_type=$${params.length+1}`);    params.push(doc_type); }
    if (status)      { conditions.push(`i.status=$${params.length+1}`);      params.push(status); }
    if (customer_id) { conditions.push(`i.customer_id=$${params.length+1}`); params.push(customer_id); }
    if (q) { conditions.push(`(i.doc_no ILIKE $${params.length+1} OR c.name ILIKE $${params.length+1} OR i.subject ILIKE $${params.length+1})`); params.push(`%${q}%`); }
    const where = conditions.length ? "WHERE "+conditions.join(" AND ") : "";
    const { rows } = await db.query(
      `SELECT i.*, c.name AS customer_name, u.name AS issued_by_name,
        (SELECT value FROM contact_entries WHERE entity_id=c.id AND type='email' AND is_primary=true LIMIT 1) AS customer_email
       FROM invoices i JOIN customers c ON c.id=i.customer_id JOIN users u ON u.id=i.issued_by
       ${where} ORDER BY i.created_at DESC`, params
    );
    res.json(rows);
  } catch (e) { next(e); }
});

router.get("/:id", async (req, res, next) => {
  try {
    const { rows } = await db.query(
      `SELECT i.*, c.name AS customer_name, c.company_id, co.name AS company_name, co.vat AS company_vat,
        u.name AS issued_by_name
       FROM invoices i JOIN customers c ON c.id=i.customer_id
       LEFT JOIN companies co ON co.id=c.company_id
       JOIN users u ON u.id=i.issued_by WHERE i.id=$1`, [req.params.id]
    );
    if (!rows[0]) return res.status(404).json({ error: "Not found" });
    const { rows: items } = await db.query(
      "SELECT * FROM invoice_items WHERE invoice_id=$1 ORDER BY sort_order", [req.params.id]
    );
    // get customer contacts
    const { rows: contacts } = await db.query(
      "SELECT * FROM contact_entries WHERE entity_id=$1 ORDER BY sort_order", [rows[0].customer_id]
    );
    res.json({ ...rows[0], items, customer_contacts: contacts });
  } catch (e) { next(e); }
});

router.post("/", async (req, res, next) => {
  try {
    const {
      doc_type='invoice', customer_id, due_date, valid_until, rental_start, rental_end,
      subject, notes_text, terms, header_text, intro_text, closing_text, footer_text,
      agb_text, include_agb=false, discount_type='none', discount_value=0,
      production_fee_pct=0, manual_tax, doc_language='de', doc_layout='classic',
      extra_recipients='', items=[]
    } = req.body;
    const s = await getSettings();
    const doc_no = await nextDocNo(doc_type);
    // Auto-populate subject with doc number
    const finalSubject = subject ? `${doc_no} | ${subject}` : doc_no;
    const { rows } = await db.query(
      `INSERT INTO invoices (doc_no,doc_type,doc_language,doc_layout,customer_id,issued_by,
        due_date,valid_until,rental_start,rental_end,subject,notes_text,terms,
        header_text,intro_text,closing_text,footer_text,agb_text,include_agb,
        discount_type,discount_value,production_fee_pct,manual_tax,extra_recipients)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24) RETURNING *`,
      [doc_no, doc_type, doc_language, doc_layout, customer_id, req.user.id,
       due_date, valid_until||null, rental_start||null, rental_end||null,
       finalSubject, notes_text,
       terms||(doc_type==='quote'?s.quote_terms:s.invoice_terms),
       header_text||s[`${doc_type}_header`]||'',
       intro_text ||s[`${doc_type}_intro`] ||'',
       closing_text||s[`${doc_type}_closing`]||'',
       footer_text||s[`${doc_type}_footer`]||'',
       agb_text||s.agb_text||'', include_agb,
       discount_type, discount_value, production_fee_pct, manual_tax||null, extra_recipients||'']
    );
    const inv = rows[0];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      const line = Number(it.units||1)*Number(it.days||1)*Number(it.unit_price||0);
      await db.query(
        `INSERT INTO invoice_items (invoice_id,inventory_id,description,units,days,unit_price,tax_rate,line_total,sort_order)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
        [inv.id, it.inventory_id||null, it.description, it.units||1, it.days||1,
         it.unit_price, it.tax_rate??s.default_tax_rate??0.20, line.toFixed(2), i]
      );
    }
    await recalc(inv.id);
    const { rows: final } = await db.query("SELECT * FROM invoices WHERE id=$1", [inv.id]);
    res.status(201).json(final[0]);
  } catch (e) { next(e); }
});

router.put("/:id", async (req, res, next) => {
  try {
    const {
      customer_id, due_date, valid_until, rental_start, rental_end, subject,
      notes_text, terms, header_text, intro_text, closing_text, footer_text,
      agb_text, include_agb, discount_type, discount_value, production_fee_pct,
      manual_tax, status, doc_language, doc_layout, extra_recipients, items
    } = req.body;
    await db.query(
      `UPDATE invoices SET customer_id=COALESCE($1,customer_id),due_date=$2,valid_until=$3,
       rental_start=$4,rental_end=$5,subject=$6,notes_text=$7,terms=$8,
       header_text=$9,intro_text=$10,closing_text=$11,footer_text=$12,
       agb_text=$13,include_agb=$14,discount_type=$15,discount_value=$16,
       production_fee_pct=$17,manual_tax=$18,status=COALESCE($19::doc_status,status),
       doc_language=COALESCE($20,doc_language),doc_layout=COALESCE($21,doc_layout),
       extra_recipients=COALESCE($22,extra_recipients) WHERE id=$23`,
      [customer_id||null,due_date,valid_until||null,rental_start||null,rental_end||null,
       subject,notes_text,terms,header_text,intro_text,closing_text,footer_text,
       agb_text,include_agb??false,discount_type,discount_value,production_fee_pct,
       manual_tax||null,status||null,doc_language||null,doc_layout||null,
       extra_recipients||null,req.params.id]
    );
    if (items) {
      const s = await getSettings();
      await db.query("DELETE FROM invoice_items WHERE invoice_id=$1", [req.params.id]);
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        const line = Number(it.units||1)*Number(it.days||1)*Number(it.unit_price||0);
        await db.query(
          `INSERT INTO invoice_items (invoice_id,inventory_id,description,units,days,unit_price,tax_rate,line_total,sort_order)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
          [req.params.id,it.inventory_id||null,it.description,it.units||1,it.days||1,
           it.unit_price,it.tax_rate??s.default_tax_rate??0.20,line.toFixed(2),i]
        );
      }
      await recalc(req.params.id);
    }
    const { rows } = await db.query("SELECT * FROM invoices WHERE id=$1", [req.params.id]);
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.post("/:id/mark-paid", async (req, res, next) => {
  try {
    const { rows } = await db.query(
      "UPDATE invoices SET status='paid',paid_at=NOW() WHERE id=$1 RETURNING *", [req.params.id]
    );
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.post("/:id/convert", async (req, res, next) => {
  try {
    const { rows:[orig] } = await db.query("SELECT * FROM invoices WHERE id=$1", [req.params.id]);
    if (!orig || orig.doc_type!=='quote') return res.status(400).json({ error:"Not a quote" });
    const { rows: items } = await db.query("SELECT * FROM invoice_items WHERE invoice_id=$1 ORDER BY sort_order", [orig.id]);
    const s = await getSettings();
    const doc_no = await nextDocNo('invoice');
    const due = new Date(); due.setDate(due.getDate()+30);
    const subject = `${doc_no} | ${orig.subject?.split('|').slice(1).join('|').trim() || ''}`.trim();
    const { rows:[inv] } = await db.query(
      `INSERT INTO invoices (doc_no,doc_type,doc_language,doc_layout,customer_id,issued_by,
        due_date,rental_start,rental_end,subject,notes_text,terms,header_text,intro_text,
        closing_text,footer_text,agb_text,include_agb,discount_type,discount_value,
        production_fee_pct,manual_tax,converted_from,extra_recipients)
       VALUES ($1,'invoice',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23) RETURNING *`,
      [doc_no,orig.doc_language,orig.doc_layout,orig.customer_id,req.user.id,
       due.toISOString().slice(0,10),orig.rental_start,orig.rental_end,subject,
       orig.notes_text,s.invoice_terms,orig.header_text,orig.intro_text,
       orig.closing_text,orig.footer_text,orig.agb_text,orig.include_agb,
       orig.discount_type,orig.discount_value,orig.production_fee_pct,orig.manual_tax,orig.id,orig.extra_recipients]
    );
    for (let i=0;i<items.length;i++) {
      const it=items[i];
      const line=Number(it.units)*Number(it.days)*Number(it.unit_price);
      await db.query(
        `INSERT INTO invoice_items (invoice_id,inventory_id,description,units,days,unit_price,tax_rate,line_total,sort_order)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
        [inv.id,it.inventory_id,it.description,it.units,it.days,it.unit_price,it.tax_rate,line.toFixed(2),i]
      );
    }
    await recalc(inv.id);
    await db.query("UPDATE invoices SET status='accepted' WHERE id=$1",[orig.id]);
    const { rows:final } = await db.query("SELECT * FROM invoices WHERE id=$1",[inv.id]);
    res.status(201).json(final[0]);
  } catch (e) { next(e); }
});

router.post("/:id/send", async (req, res, next) => {
  try {
    const { extra_email } = req.body;
    const { rows } = await db.query(
      `SELECT i.*, c.name AS customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=$1`, [req.params.id]
    );
    const inv = rows[0];
    if (!inv) return res.status(404).json({ error:"Not found" });
    // Get all recipient emails
    const { rows: contacts } = await db.query(
      "SELECT value FROM contact_entries WHERE entity_id=$1 AND type='email' AND (is_primary=true OR is_billing=true)",
      [inv.customer_id]
    );
    let recipients = contacts.map(c=>c.value);
    if (!recipients.length) {
      const { rows: allEmails } = await db.query(
        "SELECT value FROM contact_entries WHERE entity_id=$1 AND type='email' LIMIT 1", [inv.customer_id]
      );
      recipients = allEmails.map(c=>c.value);
    }
    // Add extra recipients from invoice field
    if (inv.extra_recipients) recipients.push(...inv.extra_recipients.split(',').map(e=>e.trim()).filter(Boolean));
    // Add extra_email from request
    if (extra_email) recipients.push(...extra_email.split(',').map(e=>e.trim()).filter(Boolean));
    recipients = [...new Set(recipients)];
    await sendInvoiceEmail(inv, { name: inv.customer_name, email: recipients.join(',') });
    await db.query("UPDATE invoices SET status=CASE WHEN status='draft' THEN 'sent'::doc_status ELSE status END, sent_at=NOW() WHERE id=$1",[req.params.id]);
    res.json({ ok:true, recipients });
  } catch (e) { next(e); }
});

router.post("/:id/reminder", async (req, res, next) => {
  try {
    const { level=0, extra_email } = req.body;
    const s = await getSettings();
    const { rows } = await db.query(
      `SELECT i.*, c.name AS customer_name FROM invoices i JOIN customers c ON c.id=i.customer_id WHERE i.id=$1`, [req.params.id]
    );
    const inv = rows[0];
    if (!inv) return res.status(404).json({ error:"Not found" });
    const fees = [0, Number(s.reminder1_fee||10), Number(s.reminder2_fee||20), Number(s.reminder3_fee||30)];
    const fee = fees[level]||0;
    const daysOverdue = Math.max(0, Math.floor((Date.now()-new Date(inv.due_date))/86400000));
    const interest = Number(inv.total)*Number(s.late_interest_rate||4)/100*(daysOverdue/365);
    const { rows: contacts } = await db.query(
      "SELECT value FROM contact_entries WHERE entity_id=$1 AND type='email' AND (is_primary=true OR is_billing=true)", [inv.customer_id]
    );
    let recipients = contacts.map(c=>c.value);
    if (!recipients.length) {
      const { rows: allEmails } = await db.query("SELECT value FROM contact_entries WHERE entity_id=$1 AND type='email' LIMIT 1", [inv.customer_id]);
      recipients = allEmails.map(c=>c.value);
    }
    if (inv.extra_recipients) recipients.push(...inv.extra_recipients.split(',').map(e=>e.trim()).filter(Boolean));
    if (extra_email) recipients.push(...extra_email.split(',').map(e=>e.trim()).filter(Boolean));
    recipients = [...new Set(recipients)];
    await sendReminderEmail(inv, { name: inv.customer_name, email: recipients.join(',') }, level, fee, interest, s);
    await db.query("INSERT INTO reminders (invoice_id,level,fee,interest,sent_by) VALUES ($1,$2,$3,$4,$5)", [req.params.id,level,fee,interest.toFixed(2),req.user.id]);
    await db.query("UPDATE invoices SET reminder_level=$1,last_reminder_at=NOW() WHERE id=$2",[level,req.params.id]);
    res.json({ ok:true, recipients });
  } catch (e) { next(e); }
});

router.delete("/:id", async (req, res, next) => {
  try {
    await db.query("UPDATE invoices SET status='cancelled' WHERE id=$1",[req.params.id]);
    res.json({ ok:true });
  } catch (e) { next(e); }
});

module.exports = router;
