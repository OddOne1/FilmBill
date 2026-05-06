const router = require("express").Router();
const db = require("../db");
const { authenticate } = require("../middleware/auth");

router.use(authenticate);

router.get("/invoice/:invoiceId", async (req, res, next) => {
  try {
    const { rows } = await db.query(
      "SELECT p.*, u.name AS recorded_by_name FROM payments p LEFT JOIN users u ON u.id=p.recorded_by WHERE p.invoice_id=$1 ORDER BY p.paid_at DESC",
      [req.params.invoiceId]
    );
    res.json(rows);
  } catch (e) { next(e); }
});

router.post("/", async (req, res, next) => {
  try {
    const { invoice_id, amount, method, reference, notes, paid_at } = req.body;
    const { rows } = await db.query(
      `INSERT INTO payments (invoice_id, amount, method, reference, notes, paid_at, recorded_by)
       VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING *`,
      [invoice_id, amount, method || "bank_transfer", reference, notes, paid_at || new Date(), req.user.id]
    );
    // update invoice amount_paid and status
    const { rows: totals } = await db.query(
      "SELECT COALESCE(SUM(amount),0) AS paid FROM payments WHERE invoice_id=$1", [invoice_id]
    );
    const paid = Number(totals[0].paid);
    const { rows: inv } = await db.query("SELECT total FROM invoices WHERE id=$1", [invoice_id]);
    const total = Number(inv[0]?.total || 0);
    const status = paid >= total ? "paid" : paid > 0 ? "partial" : "sent";
    await db.query(
      "UPDATE invoices SET amount_paid=$1, status=$2, paid_at=CASE WHEN $2='paid' THEN NOW() ELSE paid_at END WHERE id=$3",
      [paid.toFixed(2), status, invoice_id]
    );
    res.status(201).json(rows[0]);
  } catch (e) { next(e); }
});

router.delete("/:id", async (req, res, next) => {
  try {
    const { rows } = await db.query("DELETE FROM payments WHERE id=$1 RETURNING invoice_id", [req.params.id]);
    if (rows[0]) {
      const { rows: totals } = await db.query(
        "SELECT COALESCE(SUM(amount),0) AS paid FROM payments WHERE invoice_id=$1", [rows[0].invoice_id]
      );
      const paid = Number(totals[0].paid);
      const { rows: inv } = await db.query("SELECT total FROM invoices WHERE id=$1", [rows[0].invoice_id]);
      const total = Number(inv[0]?.total || 0);
      const status = paid >= total ? "paid" : paid > 0 ? "partial" : "sent";
      await db.query("UPDATE invoices SET amount_paid=$1, status=$2 WHERE id=$3", [paid.toFixed(2), status, rows[0].invoice_id]);
    }
    res.json({ ok: true });
  } catch (e) { next(e); }
});

module.exports = router;
