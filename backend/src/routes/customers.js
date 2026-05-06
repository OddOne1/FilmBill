const router = require("express").Router();
const db = require("../db");
const { authenticate } = require("../middleware/auth");

router.use(authenticate);

async function getContacts(entityType, entityId) {
  const { rows } = await db.query(
    "SELECT * FROM contact_entries WHERE entity_type=$1 AND entity_id=$2 ORDER BY sort_order, type",
    [entityType, entityId]
  );
  return rows;
}

async function saveContacts(entityType, entityId, contacts = []) {
  await db.query("DELETE FROM contact_entries WHERE entity_type=$1 AND entity_id=$2", [entityType, entityId]);
  for (let i = 0; i < contacts.length; i++) {
    const c = contacts[i];
    await db.query(
      `INSERT INTO contact_entries (entity_type,entity_id,type,label,value,is_primary,is_billing,is_delivery,sort_order)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
      [entityType, entityId, c.type, c.label||'Arbeit', c.value, !!c.is_primary, !!c.is_billing, !!c.is_delivery, i]
    );
  }
}

router.get("/", async (req, res, next) => {
  try {
    const { q, company_id } = req.query;
    const conditions = [];
    const params = [];
    if (q) {
      conditions.push(`(cu.name ILIKE $${params.length+1} OR co.name ILIKE $${params.length+1} OR EXISTS (SELECT 1 FROM contact_entries ce WHERE ce.entity_id=cu.id AND ce.value ILIKE $${params.length+1}))`);
      params.push(`%${q}%`);
    }
    if (company_id) { conditions.push(`cu.company_id=$${params.length+1}`); params.push(company_id); }
    const where = conditions.length ? "WHERE " + conditions.join(" AND ") : "";
    const { rows } = await db.query(
      `SELECT cu.*, co.name AS company_name FROM customers cu
       LEFT JOIN companies co ON co.id=cu.company_id ${where} ORDER BY cu.name`, params
    );
    // Attach primary contact for list view
    for (const c of rows) {
      const contacts = await getContacts('customer', c.id);
      c.contacts = contacts;
      c.primary_email = contacts.find(x=>x.type==='email'&&x.is_primary)?.value || contacts.find(x=>x.type==='email')?.value || '';
      c.primary_phone = contacts.find(x=>x.type==='phone'&&x.is_primary)?.value || contacts.find(x=>x.type==='phone')?.value || '';
    }
    res.json(rows);
  } catch (e) { next(e); }
});

router.get("/:id", async (req, res, next) => {
  try {
    const { rows } = await db.query(
      `SELECT cu.*, co.name AS company_name FROM customers cu
       LEFT JOIN companies co ON co.id=cu.company_id WHERE cu.id=$1`, [req.params.id]
    );
    if (!rows[0]) return res.status(404).json({ error: "Not found" });
    rows[0].contacts = await getContacts('customer', req.params.id);
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.post("/", async (req, res, next) => {
  try {
    const { company_id, name, notes, contacts = [] } = req.body;
    const { rows } = await db.query(
      "INSERT INTO customers (company_id,name,notes) VALUES ($1,$2,$3) RETURNING *",
      [company_id||null, name, notes]
    );
    await saveContacts('customer', rows[0].id, contacts);
    rows[0].contacts = await getContacts('customer', rows[0].id);
    res.status(201).json(rows[0]);
  } catch (e) { next(e); }
});

router.put("/:id", async (req, res, next) => {
  try {
    const { company_id, name, notes, contacts } = req.body;
    const { rows } = await db.query(
      "UPDATE customers SET company_id=$1,name=$2,notes=$3 WHERE id=$4 RETURNING *",
      [company_id||null, name, notes, req.params.id]
    );
    if (contacts) await saveContacts('customer', req.params.id, contacts);
    rows[0].contacts = await getContacts('customer', req.params.id);
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.delete("/:id", async (req, res, next) => {
  try {
    await db.query("DELETE FROM contact_entries WHERE entity_type='customer' AND entity_id=$1", [req.params.id]);
    await db.query("DELETE FROM customers WHERE id=$1", [req.params.id]);
    res.json({ ok: true });
  } catch (e) { next(e); }
});

module.exports = router;
