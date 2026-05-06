const router = require("express").Router();
const bcrypt = require("bcrypt");
const crypto = require("crypto");
const db = require("../db");
const { authenticate, requireAdmin } = require("../middleware/auth");
const { sendUserInvite } = require("../services/email");

router.use(authenticate);

// GET all users (admin) or self (user)
router.get("/", async (req, res, next) => {
  try {
    if (req.user.role === 'admin') {
      const { rows } = await db.query(
        "SELECT id, email, name, role, active, created_at FROM users ORDER BY name"
      );
      res.json(rows);
    } else {
      const { rows } = await db.query(
        "SELECT id, email, name, role, active, created_at FROM users WHERE id=$1", [req.user.id]
      );
      res.json(rows);
    }
  } catch (e) { next(e); }
});

// POST invite new user (admin only)
router.post("/", requireAdmin, async (req, res, next) => {
  try {
    const { email, name, role = "user" } = req.body;
    const tempPassword = crypto.randomBytes(8).toString("hex");
    const hash = await bcrypt.hash(tempPassword, 12);
    const { rows } = await db.query(
      "INSERT INTO users (email, name, password, role) VALUES ($1,$2,$3,$4) RETURNING id, email, name, role",
      [email.toLowerCase(), name, hash, role]
    );
    const user = rows[0];
    const inviteUrl = `${process.env.APP_URL}/login`;
    await sendUserInvite(user, inviteUrl, tempPassword).catch(console.error);
    res.status(201).json(user);
  } catch (e) { next(e); }
});

// PUT update user — admin can edit anyone, user can only edit self
router.put("/:id", async (req, res, next) => {
  try {
    const isAdmin = req.user.role === 'admin';
    const isSelf  = req.params.id === req.user.id;
    if (!isAdmin && !isSelf) return res.status(403).json({ error: "Forbidden" });

    const { name, email, role, active } = req.body;
    // Only admins can change role or active status
    const newRole   = isAdmin ? role   : undefined;
    const newActive = isAdmin ? active : undefined;

    const { rows } = await db.query(
      `UPDATE users SET
         name=$1,
         email=$2,
         role=COALESCE($3, role),
         active=COALESCE($4, active)
       WHERE id=$5
       RETURNING id, email, name, role, active`,
      [name, email.toLowerCase(), newRole || null, newActive ?? null, req.params.id]
    );
    res.json(rows[0]);
  } catch (e) { next(e); }
});

// DELETE deactivate (admin only, cannot delete self)
router.delete("/:id", requireAdmin, async (req, res, next) => {
  try {
    if (req.params.id === req.user.id)
      return res.status(400).json({ error: "Cannot deactivate yourself" });
    await db.query("UPDATE users SET active=false WHERE id=$1", [req.params.id]);
    res.json({ ok: true });
  } catch (e) { next(e); }
});

module.exports = router;
