const router = require("express").Router();
const db = require("../db");
const { authenticate } = require("../middleware/auth");

router.use(authenticate);

async function getContacts(entityId) {
  const { rows } = await db.query(
    "SELECT * FROM contact_entries WHERE entity_type='company' AND entity_id=$1 ORDER BY sort_order, type",
    [entityId]
  );
  return rows;
}

async function saveContacts(entityId, contacts = []) {
  await db.query("DELETE FROM contact_entries WHERE entity_type='company' AND entity_id=$1", [entityId]);
  for (let i = 0; i < contacts.length; i++) {
    const c = contacts[i];
    await db.query(
      `INSERT INTO contact_entries (entity_type,entity_id,type,label,value,is_primary,is_billing,is_delivery,sort_order)
       VALUES ('company',$1,$2,$3,$4,$5,$6,$7,$8)`,
      [entityId, c.type, c.label||'Arbeit', c.value, !!c.is_primary, !!c.is_billing, !!c.is_delivery, i]
    );
  }
}

router.get("/", async (_req, res, next) => {
  try {
    const { rows } = await db.query(
      `SELECT c.*, COUNT(cu.id)::int AS customer_count FROM companies c
       LEFT JOIN customers cu ON cu.company_id=c.id GROUP BY c.id ORDER BY c.name`
    );
    for (const c of rows) {
      c.contacts = await getContacts(c.id);
      c.primary_email = c.contacts.find(x=>x.type==='email'&&x.is_primary)?.value || c.contacts.find(x=>x.type==='email')?.value || '';
    }
    res.json(rows);
  } catch (e) { next(e); }
});

router.get("/:id", async (req, res, next) => {
  try {
    const { rows } = await db.query("SELECT * FROM companies WHERE id=$1", [req.params.id]);
    if (!rows[0]) return res.status(404).json({ error: "Not found" });
    rows[0].contacts = await getContacts(req.params.id);
    const { rows: customers } = await db.query("SELECT * FROM customers WHERE company_id=$1 ORDER BY name", [req.params.id]);
    res.json({ ...rows[0], customers });
  } catch (e) { next(e); }
});

router.post("/", async (req, res, next) => {
  try {
    const { name, notes, vat, contacts = [] } = req.body;
    const { rows } = await db.query(
      "INSERT INTO companies (name,notes,vat) VALUES ($1,$2,$3) RETURNING *",
      [name, notes, vat]
    );
    await saveContacts(rows[0].id, contacts);
    rows[0].contacts = await getContacts(rows[0].id);
    res.status(201).json(rows[0]);
  } catch (e) { next(e); }
});

router.put("/:id", async (req, res, next) => {
  try {
    const { name, notes, vat, contacts } = req.body;
    const { rows } = await db.query(
      "UPDATE companies SET name=$1,notes=$2,vat=$3 WHERE id=$4 RETURNING *",
      [name, notes, vat, req.params.id]
    );
    if (contacts) await saveContacts(req.params.id, contacts);
    rows[0].contacts = await getContacts(req.params.id);
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.delete("/:id", async (req, res, next) => {
  try {
    await db.query("UPDATE customers SET company_id=NULL WHERE company_id=$1", [req.params.id]);
    await db.query("DELETE FROM contact_entries WHERE entity_type='company' AND entity_id=$1", [req.params.id]);
    await db.query("DELETE FROM companies WHERE id=$1", [req.params.id]);
    res.json({ ok: true });
  } catch (e) { next(e); }
});

module.exports = router;
