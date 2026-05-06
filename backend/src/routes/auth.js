const router = require("express").Router();
const bcrypt = require("bcrypt");
const jwt = require("jsonwebtoken");
const crypto = require("crypto");
const db = require("../db");
const { sendPasswordReset } = require("../services/email");
const { authenticate } = require("../middleware/auth");

// POST /api/auth/login
router.post("/login", async (req, res, next) => {
  try {
    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ error: "Email and password required" });

    const { rows } = await db.query(
      "SELECT * FROM users WHERE email = $1 AND active = true",
      [email.toLowerCase().trim()]
    );
    const user = rows[0];
    if (!user) return res.status(401).json({ error: "Invalid credentials" });

    const match = await bcrypt.compare(password, user.password);
    if (!match) return res.status(401).json({ error: "Invalid credentials" });

    const token = jwt.sign(
      { id: user.id, email: user.email, name: user.name, role: user.role },
      process.env.JWT_SECRET,
      { expiresIn: "8h" }
    );
    res.json({ token, user: { id: user.id, email: user.email, name: user.name, role: user.role } });
  } catch (e) { next(e); }
});

// GET /api/auth/me
router.get("/me", authenticate, async (req, res, next) => {
  try {
    const { rows } = await db.query(
      "SELECT id, email, name, role FROM users WHERE id = $1", [req.user.id]
    );
    res.json(rows[0] || null);
  } catch (e) { next(e); }
});

// POST /api/auth/forgot-password
router.post("/forgot-password", async (req, res, next) => {
  try {
    const { email } = req.body;
    const { rows } = await db.query("SELECT * FROM users WHERE email = $1", [email?.toLowerCase()]);
    const user = rows[0];
    if (user) {
      const token = crypto.randomBytes(32).toString("hex");
      await db.query(
        "UPDATE users SET reset_token=$1, reset_expires=$2 WHERE id=$3",
        [token, new Date(Date.now() + 3600000), user.id]
      );
      const url = `${process.env.APP_URL}/reset-password?token=${token}`;
      await sendPasswordReset(user, url).catch(console.error);
    }
    res.json({ ok: true }); // always 200 to prevent email enumeration
  } catch (e) { next(e); }
});

// POST /api/auth/reset-password
router.post("/reset-password", async (req, res, next) => {
  try {
    const { token, password } = req.body;
    if (!token || !password || password.length < 8)
      return res.status(400).json({ error: "Invalid request" });

    const { rows } = await db.query(
      "SELECT * FROM users WHERE reset_token=$1 AND reset_expires > NOW()", [token]
    );
    const user = rows[0];
    if (!user) return res.status(400).json({ error: "Token expired or invalid" });

    const hash = await bcrypt.hash(password, 12);
    await db.query(
      "UPDATE users SET password=$1, reset_token=NULL, reset_expires=NULL WHERE id=$2",
      [hash, user.id]
    );
    res.json({ ok: true });
  } catch (e) { next(e); }
});

// POST /api/auth/change-password (authenticated)
router.post("/change-password", authenticate, async (req, res, next) => {
  try {
    const { currentPassword, newPassword } = req.body;
    if (!newPassword || newPassword.length < 8)
      return res.status(400).json({ error: "Password must be at least 8 characters" });

    const { rows } = await db.query("SELECT * FROM users WHERE id=$1", [req.user.id]);
    const match = await bcrypt.compare(currentPassword, rows[0].password);
    if (!match) return res.status(401).json({ error: "Current password incorrect" });

    const hash = await bcrypt.hash(newPassword, 12);
    await db.query("UPDATE users SET password=$1 WHERE id=$2", [hash, req.user.id]);
    res.json({ ok: true });
  } catch (e) { next(e); }
});

module.exports = router;
