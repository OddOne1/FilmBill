const router = require("express").Router();
const db = require("../db");
const { authenticate } = require("../middleware/auth");

router.use(authenticate);

router.get("/", async (req, res, next) => {
  try {
    const year = new Date().getFullYear();

    const [statusStats, overdueCount, monthlyRevenue, topItems, userStats, recentInvoices] = await Promise.all([
      db.query(`SELECT doc_type, status, COUNT(*) AS count, COALESCE(SUM(total),0) AS total
                FROM invoices WHERE status != 'cancelled' GROUP BY doc_type, status`),
      db.query(`SELECT COUNT(*) AS count FROM invoices
                WHERE status NOT IN ('paid','cancelled') AND due_date < CURRENT_DATE AND doc_type='invoice'`),
      db.query(`SELECT DATE_TRUNC('month', created_at) AS month, COALESCE(SUM(total),0) AS revenue
                FROM invoices WHERE doc_type='invoice' AND status IN ('paid','partial','sent')
                AND created_at >= DATE_TRUNC('year', NOW())
                GROUP BY month ORDER BY month`),
      db.query(`SELECT ii.description, SUM(ii.line_total) AS revenue, COUNT(*) AS times
                FROM invoice_items ii JOIN invoices i ON i.id=ii.invoice_id
                WHERE i.status != 'cancelled' AND i.doc_type='invoice'
                GROUP BY ii.description ORDER BY revenue DESC LIMIT 5`),
      db.query(`SELECT u.id, u.name,
                  COUNT(CASE WHEN i.doc_type='invoice' THEN 1 END)::int AS invoice_count,
                  COALESCE(SUM(CASE WHEN i.doc_type='invoice' THEN i.total ELSE 0 END),0) AS invoice_total,
                  COUNT(CASE WHEN i.doc_type='quote' THEN 1 END)::int AS quote_count,
                  COALESCE(SUM(CASE WHEN i.doc_type='quote' THEN i.total ELSE 0 END),0) AS quote_total
                FROM users u
                LEFT JOIN invoices i ON i.issued_by=u.id
                  AND EXTRACT(YEAR FROM i.created_at)=$1
                  AND i.status != 'cancelled'
                WHERE u.active=true
                GROUP BY u.id, u.name ORDER BY invoice_total DESC`, [year]),
      db.query(`SELECT i.id, i.doc_no, i.doc_type, i.status, i.total, i.due_date, i.issue_date, i.subject,
                  c.name AS customer_name
                FROM invoices i JOIN customers c ON c.id=i.customer_id
                WHERE i.status != 'cancelled'
                ORDER BY i.created_at DESC LIMIT 10`),
    ]);

    res.json({
      byStatus: statusStats.rows,
      overdueCount: overdueCount.rows[0].count,
      monthlyRevenue: monthlyRevenue.rows,
      topItems: topItems.rows,
      userStats: userStats.rows,
      recentInvoices: recentInvoices.rows,
      year,
    });
  } catch (e) { next(e); }
});

module.exports = router;
