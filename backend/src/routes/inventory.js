const router = require("express").Router();
const db = require("../db");
const { authenticate } = require("../middleware/auth");

router.use(authenticate);

router.get("/", async (req, res, next) => {
  try {
    const { q, category, active } = req.query;
    const conditions = [];
    const params = [];
    if (q) { conditions.push(`(name ILIKE $${params.length+1} OR sku ILIKE $${params.length+1})`); params.push(`%${q}%`); }
    if (category) { conditions.push(`category = $${params.length+1}`); params.push(category); }
    if (active !== undefined) { conditions.push(`active = $${params.length+1}`); params.push(active === "true"); }
    const where = conditions.length ? "WHERE " + conditions.join(" AND ") : "";
    const { rows } = await db.query(`SELECT * FROM inventory ${where} ORDER BY category, name`, params);
    res.json(rows);
  } catch (e) { next(e); }
});

router.get("/:id", async (req, res, next) => {
  try {
    const { rows } = await db.query("SELECT * FROM inventory WHERE id=$1", [req.params.id]);
    if (!rows[0]) return res.status(404).json({ error: "Not found" });
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.post("/", async (req, res, next) => {
  try {
    const { name, sku, category, description, day_rate, week_rate, sale_price, tax_rate, unit, stock, notes } = req.body;
    const { rows } = await db.query(
      `INSERT INTO inventory (name,sku,category,description,day_rate,week_rate,sale_price,tax_rate,unit,stock,notes)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING *`,
      [name, sku || null, category, description, day_rate, week_rate || null, sale_price || null,
       tax_rate || 0, unit || "day", stock || 1, notes]
    );
    res.status(201).json(rows[0]);
  } catch (e) { next(e); }
});

router.put("/:id", async (req, res, next) => {
  try {
    const { name, sku, category, description, day_rate, week_rate, sale_price, tax_rate, unit, stock, active, notes } = req.body;
    const { rows } = await db.query(
      `UPDATE inventory SET name=$1,sku=$2,category=$3,description=$4,day_rate=$5,week_rate=$6,
       sale_price=$7,tax_rate=$8,unit=$9,stock=$10,active=$11,notes=$12 WHERE id=$13 RETURNING *`,
      [name, sku || null, category, description, day_rate, week_rate || null, sale_price || null,
       tax_rate || 0, unit, stock, active !== false, notes, req.params.id]
    );
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.delete("/:id", async (req, res, next) => {
  try {
    await db.query("UPDATE inventory SET active=false WHERE id=$1", [req.params.id]);
    res.json({ ok: true });
  } catch (e) { next(e); }
});

module.exports = router;
