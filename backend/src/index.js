require("dotenv").config();
const express = require("express");
const helmet  = require("helmet");
const cors    = require("cors");
const rateLimit = require("express-rate-limit");

const authRoutes      = require("./routes/auth");
const userRoutes      = require("./routes/users");
const customerRoutes  = require("./routes/customers");
const companyRoutes   = require("./routes/companies");
const inventoryRoutes = require("./routes/inventory");
const invoiceRoutes   = require("./routes/invoices");
const paymentRoutes   = require("./routes/payments");
const dashRoutes      = require("./routes/dashboard");
const settingsRoutes  = require("./routes/settings");

const app = express();
app.set("trust proxy", 1);
app.use(helmet());
app.use(cors({ origin: true, credentials: true }));
app.use(express.json({ limit: "5mb" })); // larger for logo upload

app.use("/api/auth",      rateLimit({ windowMs: 15*60*1000, max: 30 }));
app.use("/api",           rateLimit({ windowMs: 60*1000,    max: 300 }));

app.use("/api/auth",      authRoutes);
app.use("/api/users",     userRoutes);
app.use("/api/customers", customerRoutes);
app.use("/api/companies", companyRoutes);
app.use("/api/inventory", inventoryRoutes);
app.use("/api/invoices",  invoiceRoutes);
app.use("/api/payments",  paymentRoutes);
app.use("/api/dashboard", dashRoutes);
app.use("/api/settings",  settingsRoutes);

app.get("/api/health", (_req, res) => res.json({ ok: true }));

app.use((err, _req, res, _next) => {
  console.error(err);
  res.status(err.status || 500).json({ error: err.message || "Internal server error" });
});

const PORT = process.env.PORT || 4000;
app.listen(PORT, () => console.log(`FilmBill API :${PORT}`));
