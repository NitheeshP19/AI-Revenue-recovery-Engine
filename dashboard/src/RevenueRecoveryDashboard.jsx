import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from "recharts";
import {
  TrendingUp, Zap, Activity, DollarSign, Brain, RefreshCw,
  ArrowUpRight, CheckCircle2, XCircle, Clock,
  Cpu, Play, Square, BarChart2,
} from "lucide-react";

// ─────────────────────────────────────────────────────────────────────────────
//  MOCK DATA  (mirrors Phase 5 metrics_summary.json schema)
// ─────────────────────────────────────────────────────────────────────────────

const MOCK_METRICS = {
  meta: {
    simulation_id: "2960a161-265f-474e-8c9e-b1720f34ede2",
    generated_at: "2026-08-20T14:12:59Z",
    sample_size: 200,
    schema_version: "1.0.0",
  },
  kpis: {
    rule_recovery_rate_pct: 31.2,
    ai_recovery_rate_pct: 68.4,
    recovery_rate_lift_pp: 37.2,
    rule_revenue_recovered: 104118.00,
    ai_revenue_recovered: 142850.00,
    revenue_lift_pct: 37.20,
    rule_avg_latency_ms: 0.5,
    ai_avg_latency_ms: 42,
  },
  strategies: {
    rule_based: {
      name: "Rule-Based Baseline",
      total_transactions: 200,
      recovered_count: 62,
      recovery_rate_pct: 31.2,
      revenue_recovered: 104118.00,
      action_breakdown: { retry_later: 154, give_up: 43, retry_now: 3 },
    },
    ai_agent: {
      name: "AI Agent (Groq + Llama-3)",
      total_transactions: 200,
      recovered_count: 137,
      recovery_rate_pct: 68.4,
      revenue_recovered: 142850.00,
      action_breakdown: { retry_later: 89, switch_method: 62, retry_now: 34, give_up: 15 },
    },
  },
  failure_reason_breakdown: [
    { failure_reason: "gateway_timeout",    rule_recovery_rate_pct: 46.4, ai_recovery_rate_pct: 85.0, lift_pct: 38.6,  total: 69 },
    { failure_reason: "insufficient_funds", rule_recovery_rate_pct: 59.5, ai_recovery_rate_pct: 65.0, lift_pct: 5.5,   total: 42 },
    { failure_reason: "incorrect_pin",      rule_recovery_rate_pct: 3.0,  ai_recovery_rate_pct: 70.0, lift_pct: 67.0,  total: 33 },
    { failure_reason: "risk_flag",          rule_recovery_rate_pct: 27.6, ai_recovery_rate_pct: 45.0, lift_pct: 17.4,  total: 29 },
    { failure_reason: "expired_card",       rule_recovery_rate_pct: 0.0,  ai_recovery_rate_pct: 80.0, lift_pct: 80.0,  total: 27 },
  ],
};

const LIVE_FEED_POOL = [
  { failure_reason: "gateway_timeout",    amount: 3963.28,  payment_method: "UPI",         ai_action: "retry_now",     ai_recovered: true,  ai_reasoning_trace: "UPI gateway timeout detected. Gateway health is 'healthy' with <2% error rate. Immediate retry has 85% success probability. Customer LTV $12.4k — high value preservation required." },
  { failure_reason: "expired_card",       amount: 8241.50,  payment_method: "Credit Card",  ai_action: "switch_method", ai_recovered: true,  ai_reasoning_trace: "Card expiry confirmed. Retry actions yield 0% recovery. Customer has UPI as preferred alternative. Switching payment channel — 80% success probability." },
  { failure_reason: "insufficient_funds", amount: 1542.00,  payment_method: "Debit Card",   ai_action: "retry_later",   ai_recovered: true,  ai_reasoning_trace: "Insufficient funds detected. Salary credit window aligns with 24–48h retry. Customer LTV $4.2k — rescheduling with push notification in 30 minutes." },
  { failure_reason: "gateway_timeout",    amount: 4200.00,  payment_method: "UPI",         ai_action: "switch_method", ai_recovered: true,  ai_reasoning_trace: "Customer has high LTV ($4.2k) and UPI timed out twice; rerouting payment method via instant SMS link instead of immediate retry" },
  { failure_reason: "incorrect_pin",      amount: 425.80,   payment_method: "UPI",         ai_action: "switch_method", ai_recovered: true,  ai_reasoning_trace: "Incorrect PIN entered twice. UPI PIN reset is a friction barrier. Rerouting to Credit Card via instant SMS payment link — 70% recovery probability." },
  { failure_reason: "risk_flag",          amount: 14200.00, payment_method: "Credit Card",  ai_action: "retry_later",   ai_recovered: false, ai_reasoning_trace: "Risk flag triggered — fraud scoring elevated. Cooling period of 45 minutes recommended before retry. Customer flagged for manual review escalation." },
  { failure_reason: "gateway_timeout",    amount: 2750.00,  payment_method: "Debit Card",   ai_action: "retry_now",     ai_recovered: true,  ai_reasoning_trace: "Transient gateway timeout on Razorpay. Gateway recovering. Customer has only retried once — retry budget not exhausted. Immediate retry dispatched." },
  { failure_reason: "insufficient_funds", amount: 880.25,   payment_method: "UPI",         ai_action: "retry_later",   ai_recovered: true,  ai_reasoning_trace: "UPI balance insufficient. Customer salary cycle expected in 12 hours. Scheduling automated retry at 09:00 AM with WhatsApp reminder." },
  { failure_reason: "expired_card",       amount: 19500.00, payment_method: "Credit Card",  ai_action: "switch_method", ai_recovered: true,  ai_reasoning_trace: "Card expired 3 days ago. High-value transaction $19.5k — VIP customer. Routing to Net Banking alternative with zero retry delay." },
  { failure_reason: "incorrect_pin",      amount: 340.00,   payment_method: "Debit Card",   ai_action: "switch_method", ai_recovered: false, ai_reasoning_trace: "Third incorrect PIN attempt — account lock risk elevated. Switching to UPI with pre-filled amount link to eliminate PIN requirement." },
  { failure_reason: "risk_flag",          amount: 5600.00,  payment_method: "UPI",         ai_action: "give_up",       ai_recovered: false, ai_reasoning_trace: "Risk flag persists after second evaluation. ML model confidence: 91% fraud probability. Decision: give_up — escalating to manual human review queue." },
];

// ─────────────────────────────────────────────────────────────────────────────
//  UTILITY HELPERS
// ─────────────────────────────────────────────────────────────────────────────

function formatCurrency(val) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(val);
}

function shortId(id) {
  return id ? id.slice(0, 8).toUpperCase() : "UNKNOWN";
}

function generateTxId() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
//  ANIMATED COUNTER
// ─────────────────────────────────────────────────────────────────────────────

function AnimatedCounter({ target, prefix = "", suffix = "", decimals = 0, duration = 1800 }) {
  const [display, setDisplay] = useState(0);
  const startTime = useRef(null);
  const rafRef = useRef(null);

  useEffect(() => {
    startTime.current = null;
    const animate = (ts) => {
      if (!startTime.current) startTime.current = ts;
      const progress = Math.min((ts - startTime.current) / duration, 1);
      const ease = 1 - Math.pow(1 - progress, 3);
      setDisplay(+(target * ease).toFixed(decimals));
      if (progress < 1) rafRef.current = requestAnimationFrame(animate);
    };
    rafRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, duration, decimals]);

  return (
    <span>
      {prefix}{typeof display === "number" ? display.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) : display}{suffix}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ACTION BADGE
// ─────────────────────────────────────────────────────────────────────────────

const ACTION_STYLES = {
  retry_now:     "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  retry_later:   "bg-amber-500/15  text-amber-300  border-amber-500/30",
  switch_method: "bg-cyan-500/15   text-cyan-300   border-cyan-500/30",
  give_up:       "bg-rose-500/15   text-rose-300   border-rose-500/30",
};

function ActionBadge({ action }) {
  const labels = { retry_now: "Retry Now", retry_later: "Retry Later", switch_method: "Switch Method", give_up: "Give Up" };
  return (
    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${ACTION_STYLES[action] || "bg-slate-700 text-slate-300 border-slate-600"}`}>
      {labels[action] || action}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  CUSTOM RECHARTS TOOLTIP
// ─────────────────────────────────────────────────────────────────────────────

function DarkTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-slate-950/90 backdrop-blur-md border border-slate-800 rounded-xl px-4 py-3 shadow-2xl text-sm ring-1 ring-white/10">
      <p className="text-slate-300 font-semibold mb-2 capitalize">{label?.replace(/_/g, " ")}</p>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2 mb-1">
          <span className="w-2 h-2 rounded-full" style={{ background: p.fill || p.color }} />
          <span className="text-slate-400">{p.name}:</span>
          <span className="text-white font-bold">{p.value.toFixed(1)}%</span>
        </div>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  KPI CARD
// ─────────────────────────────────────────────────────────────────────────────

function KpiCard({ icon: Icon, label, value, sub, accent, delay = 0, badge }) {
  const accentMap = {
    emerald: { icon: "text-emerald-400", glow: "shadow-emerald-500/10", border: "border-emerald-500/20", bg: "bg-emerald-500/10" },
    cyan:    { icon: "text-cyan-400",    glow: "shadow-cyan-500/10",    border: "border-cyan-500/20",    bg: "bg-cyan-500/10" },
    violet:  { icon: "text-violet-400",  glow: "shadow-violet-500/10",  border: "border-violet-500/20",  bg: "bg-violet-500/10" },
    amber:   { icon: "text-amber-400",   glow: "shadow-amber-500/10",   border: "border-amber-500/20",   bg: "bg-amber-500/10" },
    rose:    { icon: "text-rose-400",    glow: "shadow-rose-500/10",    border: "border-rose-500/20",    bg: "bg-rose-500/10" },
  };
  const a = accentMap[accent] || accentMap.emerald;

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay }}
      className={`relative rounded-2xl border ${a.border} bg-slate-900/70 backdrop-blur-md p-5 shadow-xl ${a.glow} flex flex-col gap-3 overflow-hidden`}
    >
      {/* Subtle radial glow */}
      <div className={`absolute -top-8 -right-8 w-32 h-32 rounded-full ${a.bg} blur-2xl pointer-events-none`} />

      <div className="flex items-center justify-between">
        <div className={`w-9 h-9 rounded-xl ${a.bg} flex items-center justify-center`}>
          <Icon size={18} className={a.icon} />
        </div>
        {badge && (
          <span className="flex items-center gap-1 bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-[11px] font-bold px-2 py-0.5 rounded-full">
            <ArrowUpRight size={10} />{badge}
          </span>
        )}
      </div>

      <div>
        <p className="text-slate-400 text-xs font-medium uppercase tracking-wider mb-1">{label}</p>
        <p className="text-white text-2xl font-bold tracking-tight">{value}</p>
        {sub && <p className="text-slate-500 text-xs mt-1">{sub}</p>}
      </div>
    </motion.div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  RECOVERY COMPARISON BAR CHART
// ─────────────────────────────────────────────────────────────────────────────

function RecoveryChart({ data }) {
  const chartData = data.map((d) => ({
    name: d.failure_reason.replace(/_/g, "_"),
    "Rule-Based": d.rule_recovery_rate_pct,
    "AI Agent": d.ai_recovery_rate_pct,
    lift: d.lift_pct,
  }));

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, delay: 0.3 }}
      className="rounded-2xl border border-slate-700/60 bg-slate-900/70 backdrop-blur-md p-6 shadow-xl"
    >
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-white font-semibold text-base flex items-center gap-2">
            <BarChart2 size={16} className="text-cyan-400" />
            Recovery Rate — A/B Strategy Benchmark
          </h2>
          <p className="text-slate-500 text-xs mt-0.5">Grouped comparison by failure category</p>
        </div>
        <div className="flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-slate-600" />Rule-Based</span>
          <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-emerald-500" />AI Agent</span>
        </div>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} barCategoryGap="30%" barGap={4}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fill: "#64748b", fontSize: 11 }}
            tickFormatter={(v) => v.replace(/_/g, " ")}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#64748b", fontSize: 11 }}
            domain={[0, 100]}
            tickFormatter={(v) => `${v}%`}
            axisLine={false}
            tickLine={false}
            width={38}
          />
          <Tooltip content={<DarkTooltip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
          <Bar dataKey="Rule-Based" radius={[4, 4, 0, 0]} fill="#475569" />
          <Bar dataKey="AI Agent"   radius={[4, 4, 0, 0]}>
            {chartData.map((entry, idx) => (
              <Cell
                key={idx}
                fill={entry["AI Agent"] >= entry["Rule-Based"] ? "#10b981" : "#f59e0b"}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* Lift Indicators */}
      <div className="grid grid-cols-5 gap-2 mt-4">
        {chartData.map((d) => (
          <div key={d.name} className="flex flex-col items-center gap-1">
            <span className={`text-xs font-bold ${d.lift >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
              {d.lift >= 0 ? "+" : ""}{d.lift.toFixed(1)}pp
            </span>
            <span className="text-[10px] text-slate-600 text-center capitalize leading-tight">
              {d.name.replace(/_/g, " ")}
            </span>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  ACTION BREAKDOWN AREA CHART
// ─────────────────────────────────────────────────────────────────────────────

function ActionBreakdownChart({ strategies }) {
  const labels = ["retry_now", "retry_later", "switch_method", "give_up"];
  const data = labels.map((action) => ({
    action: action.replace(/_/g, " "),
    "Rule-Based": strategies.rule_based.action_breakdown[action] || 0,
    "AI Agent":   strategies.ai_agent.action_breakdown[action] || 0,
  }));

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, delay: 0.4 }}
      className="rounded-2xl border border-slate-700/60 bg-slate-900/70 backdrop-blur-md p-6 shadow-xl"
    >
      <div className="mb-5">
        <h2 className="text-white font-semibold text-base flex items-center gap-2">
          <Activity size={16} className="text-violet-400" />
          Decision Action Distribution
        </h2>
        <p className="text-slate-500 text-xs mt-0.5">How each strategy allocates actions across 200 transactions</p>
      </div>

      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} layout="vertical" barCategoryGap="25%" barGap={3}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
          <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis
            type="category"
            dataKey="action"
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={84}
          />
          <Tooltip content={<DarkTooltip />} cursor={{ fill: "rgba(255,255,255,0.03)" }} />
          <Bar dataKey="Rule-Based" fill="#475569" radius={[0, 4, 4, 0]} />
          <Bar dataKey="AI Agent"   fill="#8b5cf6" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </motion.div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  LIVE AGENT REASONING FEED
// ─────────────────────────────────────────────────────────────────────────────

function ReasoningFeed({ events }) {
  const bottomRef = useRef(null);
  const [isAtBottom, setIsAtBottom] = useState(true);

  useEffect(() => {
    if (isAtBottom) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [events, isAtBottom]);

  const handleScroll = (e) => {
    const { scrollTop, scrollHeight, clientHeight } = e.target;
    // If user is within 80px of the bottom, keep auto-scroll active
    const atBottom = scrollHeight - scrollTop - clientHeight < 80;
    setIsAtBottom(atBottom);
  };

  const REASON_COLORS = {
    gateway_timeout:    "text-cyan-400 bg-cyan-500/10 border-cyan-500/30",
    insufficient_funds: "text-amber-400 bg-amber-500/10 border-amber-500/30",
    expired_card:       "text-rose-400 bg-rose-500/10 border-rose-500/30",
    incorrect_pin:      "text-violet-400 bg-violet-500/10 border-violet-500/30",
    risk_flag:          "text-orange-400 bg-orange-500/10 border-orange-500/30",
  };

  return (
    <div 
      className="flex flex-col gap-2.5 max-h-[520px] overflow-y-auto pr-1 scrollbar-thin scrollbar-thumb-slate-700 scrollbar-track-transparent"
      onScroll={handleScroll}
    >
      <AnimatePresence initial={false}>
        {events.map((ev) => (
          <motion.div
            key={ev._id}
            initial={{ opacity: 0, x: -16, scale: 0.97 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.3 }}
            className="rounded-xl border border-slate-700/50 bg-slate-800/50 backdrop-blur-sm p-4"
          >
            {/* Header row */}
            <div className="flex items-center justify-between gap-2 mb-2">
              <div className="flex items-center gap-2 min-w-0">
                {ev.ai_recovered
                  ? <CheckCircle2 size={14} className="text-emerald-400 shrink-0" />
                  : <XCircle size={14} className="text-rose-400 shrink-0" />
                }
                <span className="text-slate-300 font-mono text-xs truncate">{shortId(ev.transaction_id)}</span>
                <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${REASON_COLORS[ev.failure_reason] || "text-slate-400 bg-slate-700 border-slate-600"} shrink-0`}>
                  {ev.failure_reason.replace(/_/g, "_")}
                </span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <ActionBadge action={ev.ai_action} />
                <span className="text-white font-bold text-sm">
                  {formatCurrency(ev.amount)}
                </span>
              </div>
            </div>

            {/* Reasoning trace */}
            <div className="flex items-start gap-2 mt-2 bg-slate-900/60 rounded-lg p-3 border border-slate-700/40">
              <Brain size={12} className="text-cyan-400 mt-0.5 shrink-0" />
              <p className="text-slate-400 text-[11px] leading-relaxed">{ev.ai_reasoning_trace}</p>
            </div>

            {/* Footer */}
            <div className="flex items-center gap-3 mt-2">
              <span className="text-slate-600 text-[10px] flex items-center gap-1">
                <Clock size={9} />{ev._time}
              </span>
              <span className="text-slate-600 text-[10px]">{ev.payment_method}</span>
              {ev.ai_latency_ms > 0 && (
                <span className="text-slate-600 text-[10px] flex items-center gap-1">
                  <Zap size={9} />{ev.ai_latency_ms}ms
                </span>
              )}
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
      <div ref={bottomRef} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  STRATEGY COMPARISON ROW
// ─────────────────────────────────────────────────────────────────────────────

function StrategyComparisonRow({ strategies, kpis }) {
  const cols = [
    { label: "Recovery Rate",   rule: `${kpis.rule_recovery_rate_pct}%`,     ai: `${kpis.ai_recovery_rate_pct}%`,    better: "ai" },
    { label: "Revenue Saved",   rule: formatCurrency(kpis.rule_revenue_recovered), ai: formatCurrency(kpis.ai_revenue_recovered), better: "ai" },
    { label: "Avg Latency",     rule: `${kpis.rule_avg_latency_ms}ms`,       ai: `${kpis.ai_avg_latency_ms}ms`,      better: "rule" },
    { label: "Transactions",    rule: strategies.rule_based.total_transactions, ai: strategies.ai_agent.total_transactions, better: "equal" },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, delay: 0.5 }}
      className="rounded-2xl border border-slate-700/60 bg-slate-900/70 backdrop-blur-md p-6 shadow-xl"
    >
      <h2 className="text-white font-semibold text-base flex items-center gap-2 mb-5">
        <TrendingUp size={16} className="text-emerald-400" />
        Strategy Performance Matrix
      </h2>
      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="text-center text-xs text-slate-500 font-medium uppercase tracking-wider pb-2 border-b border-slate-800">Rule-Based Baseline</div>
        <div className="text-center text-xs text-cyan-400 font-medium uppercase tracking-wider pb-2 border-b border-cyan-500/30">AI Agent (Groq)</div>
      </div>
      <div className="flex flex-col gap-3">
        {cols.map((col) => (
          <div key={col.label} className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
            <div className={`text-right font-bold text-sm ${col.better === "rule" ? "text-emerald-300" : "text-slate-400"}`}>
              {col.rule}
            </div>
            <div className="text-center text-[10px] text-slate-600 font-medium px-2">{col.label}</div>
            <div className={`text-left font-bold text-sm ${col.better === "ai" ? "text-emerald-300" : "text-slate-400"}`}>
              {col.ai}
            </div>
          </div>
        ))}
      </div>
    </motion.div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  MAIN DASHBOARD
// ─────────────────────────────────────────────────────────────────────────────

export default function RevenueRecoveryDashboard() {
  const metrics = MOCK_METRICS;
  const { kpis, strategies, failure_reason_breakdown, meta } = metrics;

  // Live feed state
  const [feedEvents, setFeedEvents] = useState(() =>
    MOCK_METRICS.failure_reason_breakdown.slice(0, 3).map((_, i) => ({
      ...LIVE_FEED_POOL[i],
      _id: generateTxId(),
      transaction_id: generateTxId(),
      _time: new Date().toLocaleTimeString(),
      ai_latency_ms: Math.floor(Math.random() * 60 + 20),
    }))
  );
  const [streaming, setStreaming] = useState(false);
  const intervalRef = useRef(null);

  const appendEvent = useCallback(() => {
    const template = LIVE_FEED_POOL[Math.floor(Math.random() * LIVE_FEED_POOL.length)];
    setFeedEvents((prev) => [
      ...prev.slice(-19), // Keep max 20
      {
        ...template,
        _id: generateTxId(),
        transaction_id: generateTxId(),
        _time: new Date().toLocaleTimeString(),
        ai_latency_ms: Math.floor(Math.random() * 80 + 18),
        amount: parseFloat((template.amount * (0.7 + Math.random() * 0.8)).toFixed(2)),
        ai_recovered: Math.random() > 0.3,
      },
    ]);
  }, []);

  // Only manage the interval lifecycle here — no direct setState call in effect body.
  // The first event on "Start" is fired directly from the onClick handler below.
  useEffect(() => {
    if (!streaming) {
      clearInterval(intervalRef.current);
      return;
    }
    intervalRef.current = setInterval(appendEvent, 2200);
    return () => clearInterval(intervalRef.current);
  }, [streaming, appendEvent]);

  const totalRevenue = kpis.ai_revenue_recovered;
  const liftBadge = `+${kpis.recovery_rate_lift_pp.toFixed(1)}pp lift`;

  return (
    <div className="min-h-screen bg-slate-950 text-white font-sans">
      {/* Background radial gradients */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-emerald-500/5 rounded-full blur-[120px]" />
        <div className="absolute bottom-0 right-1/4 w-[500px] h-[500px] bg-cyan-500/5 rounded-full blur-[100px]" />
        <div className="absolute top-1/2 left-0 w-[400px] h-[400px] bg-violet-500/4 rounded-full blur-[100px]" />
      </div>

      <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">

        {/* ── HEADER ──────────────────────────────────────────────────── */}
        <motion.header
          initial={{ opacity: 0, y: -16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8"
        >
          <div>
            <div className="flex items-center gap-2 mb-1">
              <div className="w-7 h-7 rounded-lg bg-emerald-500/20 flex items-center justify-center">
                <Cpu size={14} className="text-emerald-400" />
              </div>
              <span className="text-xs text-slate-500 font-medium uppercase tracking-widest">AI Revenue Recovery</span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-white">
              Recovery Intelligence Dashboard
            </h1>
            <p className="text-slate-500 text-sm mt-1">
              Phase 5 Simulation · {meta.sample_size} transactions · Seed #{meta.random_seed}
            </p>
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 bg-slate-800/60 border border-slate-700/50 rounded-xl px-3 py-2 text-xs">
              <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
              <span className="text-slate-400">Groq Llama-3 · Active</span>
            </div>
            <div className="text-xs text-slate-600">
              {new Date(meta.generated_at).toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric" })}
            </div>
          </div>
        </motion.header>

        {/* ── KPI CARDS ────────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-8">
          <KpiCard
            icon={DollarSign}
            label="Total Revenue Recovered"
            value={<AnimatedCounter target={totalRevenue} prefix="$" decimals={2} />}
            sub={`vs $${kpis.rule_revenue_recovered.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} rule-based`}
            accent="emerald"
            badge={`+${kpis.revenue_lift_pct}%`}
            delay={0.1}
          />
          <KpiCard
            icon={TrendingUp}
            label="AI Recovery Rate vs. Baseline"
            value={<><AnimatedCounter target={kpis.ai_recovery_rate_pct} decimals={1} />%</>}
            sub={`vs ${kpis.rule_recovery_rate_pct}% rule-based baseline`}
            accent="cyan"
            badge={liftBadge}
            delay={0.2}
          />
          <KpiCard
            icon={Activity}
            label="Autonomous Decisions"
            value={<AnimatedCounter target={strategies.ai_agent.total_transactions} />}
            sub="Events evaluated end-to-end"
            accent="violet"
            delay={0.25}
          />
          <KpiCard
            icon={Zap}
            label="Avg Decision Latency"
            value={<><AnimatedCounter target={kpis.ai_avg_latency_ms} />ms</>}
            sub="Via Groq Llama-3 inference"
            accent="amber"
            delay={0.3}
          />
        </div>

        {/* ── CHARTS ROW ───────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5 mb-6">
          <div className="lg:col-span-2">
            <RecoveryChart data={failure_reason_breakdown} />
          </div>
          <ActionBreakdownChart strategies={strategies} />
        </div>

        {/* ── LOWER ROW: LIVE FEED + STRATEGY MATRIX ───────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">

          {/* Live Agent Reasoning Feed */}
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.5 }}
            className="lg:col-span-2 rounded-2xl border border-slate-700/60 bg-slate-900/70 backdrop-blur-md p-6 shadow-xl"
          >
            <div className="flex items-center justify-between mb-5">
              <div>
                <h2 className="text-white font-semibold text-base flex items-center gap-2">
                  <Brain size={16} className="text-cyan-400" />
                  Live Agent Reasoning Feed
                </h2>
                <p className="text-slate-500 text-xs mt-0.5">Real-time autonomous decision events with reasoning traces</p>
              </div>

              <button
                onClick={() => {
                  // Fire the first event immediately on Start so the feed responds
                  // instantly, then the interval takes over. This avoids calling
                  // setState directly inside useEffect (set-state-in-effect rule).
                  if (!streaming) appendEvent();
                  setStreaming((s) => !s);
                }}
                className={`flex items-center gap-2 text-xs font-semibold px-3 py-1.5 rounded-lg border transition-all duration-200 ${
                  streaming
                    ? "bg-rose-500/15 border-rose-500/30 text-rose-300 hover:bg-rose-500/25"
                    : "bg-emerald-500/15 border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/25"
                }`}
              >
                {streaming ? <><Square size={11} />Stop Simulation</> : <><Play size={11} />Stream Simulated Events</>}
              </button>
            </div>

            <ReasoningFeed events={feedEvents} />
          </motion.div>

          {/* Right column: Strategy Matrix + Recovery Matrix */}
          <div className="flex flex-col gap-5">
            <StrategyComparisonRow strategies={strategies} kpis={kpis} />

            {/* Recovery Probability Matrix */}
            <motion.div
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.65 }}
              className="rounded-2xl border border-slate-700/60 bg-slate-900/70 backdrop-blur-md p-5 shadow-xl"
            >
              <h3 className="text-white font-semibold text-sm flex items-center gap-2 mb-4">
                <RefreshCw size={13} className="text-violet-400" />
                Stochastic Recovery Matrix
              </h3>
              <div className="flex flex-col gap-2">
                {[
                  { reason: "gateway_timeout",    retry_now: "85%", retry_later: "72%", switch: "55%" },
                  { reason: "insufficient_funds", retry_now: "5%",  retry_later: "65%", switch: "50%" },
                  { reason: "expired_card",       retry_now: "0%",  retry_later: "0%",  switch: "80%" },
                  { reason: "incorrect_pin",      retry_now: "10%", retry_later: "10%", switch: "70%" },
                  { reason: "risk_flag",          retry_now: "12%", retry_later: "45%", switch: "35%" },
                ].map((row) => (
                  <div key={row.reason} className="grid grid-cols-4 gap-1 items-center text-[10px]">
                    <span className="text-slate-500 truncate capitalize">{row.reason.replace(/_/g," ")}</span>
                    <span className="text-center text-emerald-400 font-mono bg-emerald-500/5 rounded px-1 py-0.5">{row.retry_now}</span>
                    <span className="text-center text-amber-400 font-mono bg-amber-500/5 rounded px-1 py-0.5">{row.retry_later}</span>
                    <span className="text-center text-cyan-400 font-mono bg-cyan-500/5 rounded px-1 py-0.5">{row.switch}</span>
                  </div>
                ))}
                <div className="grid grid-cols-4 gap-1 mt-1">
                  <span />
                  <span className="text-center text-[9px] text-slate-600">retry_now</span>
                  <span className="text-center text-[9px] text-slate-600">retry_later</span>
                  <span className="text-center text-[9px] text-slate-600">switch</span>
                </div>
              </div>
            </motion.div>
          </div>
        </div>

        {/* ── FOOTER ───────────────────────────────────────────────────── */}
        <motion.footer
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8 }}
          className="mt-8 text-center text-slate-700 text-xs"
        >
          AI Revenue Recovery System · Dashboard · Simulation ID: {meta.simulation_id.slice(0, 8)}
        </motion.footer>
      </div>
    </div>
  );
}
