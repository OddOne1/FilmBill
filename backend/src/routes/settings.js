const router = require("express").Router();
const db = require("../db");
const { authenticate, requireAdmin } = require("../middleware/auth");

router.use(authenticate);

router.get("/", async (_req, res, next) => {
  try {
    const { rows } = await db.query("SELECT key, value FROM settings ORDER BY key");
    const obj = {};
    rows.forEach(r => { obj[r.key] = r.value; });
    res.json(obj);
  } catch (e) { next(e); }
});

router.put("/", requireAdmin, async (req, res, next) => {
  try {
    for (const [key, value] of Object.entries(req.body)) {
      await db.query(
        "INSERT INTO settings (key,value) VALUES ($1,$2) ON CONFLICT (key) DO UPDATE SET value=$2",
        [key, String(value)]
      );
    }
    const { rows } = await db.query("SELECT key, value FROM settings ORDER BY key");
    const obj = {};
    rows.forEach(r => { obj[r.key] = r.value; });
    res.json(obj);
  } catch (e) { next(e); }
});

// ─── Inventory Categories ─────────────────────────────────────────────────────
router.get("/categories", async (_req, res, next) => {
  try {
    const { rows } = await db.query("SELECT * FROM inventory_categories ORDER BY sort_order, name");
    res.json(rows);
  } catch (e) { next(e); }
});

router.post("/categories", requireAdmin, async (req, res, next) => {
  try {
    const { name, sort_order = 99 } = req.body;
    const { rows } = await db.query(
      "INSERT INTO inventory_categories (name, sort_order) VALUES ($1,$2) RETURNING *",
      [name, sort_order]
    );
    res.status(201).json(rows[0]);
  } catch (e) { next(e); }
});

router.put("/categories/:id", requireAdmin, async (req, res, next) => {
  try {
    const { name, sort_order } = req.body;
    const { rows } = await db.query(
      "UPDATE inventory_categories SET name=$1, sort_order=$2 WHERE id=$3 RETURNING *",
      [name, sort_order, req.params.id]
    );
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.delete("/categories/:id", requireAdmin, async (req, res, next) => {
  try {
    await db.query("UPDATE inventory SET category='Sonstiges' WHERE category=(SELECT name FROM inventory_categories WHERE id=$1)", [req.params.id]);
    await db.query("DELETE FROM inventory_categories WHERE id=$1", [req.params.id]);
    res.json({ ok: true });
  } catch (e) { next(e); }
});

// ─── Text Templates ───────────────────────────────────────────────────────────
router.get("/templates", async (_req, res, next) => {
  try {
    const { rows } = await db.query("SELECT * FROM text_templates ORDER BY type, name");
    res.json(rows);
  } catch (e) { next(e); }
});

router.post("/templates", async (req, res, next) => {
  try {
    const { type, name, content } = req.body;
    const { rows } = await db.query(
      "INSERT INTO text_templates (type, name, content) VALUES ($1,$2,$3) RETURNING *",
      [type, name, content]
    );
    res.status(201).json(rows[0]);
  } catch (e) { next(e); }
});

router.put("/templates/:id", async (req, res, next) => {
  try {
    const { name, content } = req.body;
    const { rows } = await db.query(
      "UPDATE text_templates SET name=$1, content=$2 WHERE id=$3 RETURNING *",
      [name, content, req.params.id]
    );
    res.json(rows[0]);
  } catch (e) { next(e); }
});

router.delete("/templates/:id", async (req, res, next) => {
  try {
    await db.query("DELETE FROM text_templates WHERE id=$1", [req.params.id]);
    res.json({ ok: true });
  } catch (e) { next(e); }
});

module.exports = router;
