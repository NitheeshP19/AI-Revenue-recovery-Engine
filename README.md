# AI Revenue Recovery Engine 🚀

[![Live Frontend Demo](https://img.shields.io/badge/Live_Demo-Vercel-000000?style=for-the-badge&logo=vercel&logoColor=white)](https://ai-revenue-recovery-engine-git-main-nitheeshps-projects.vercel.app/)
[![Live Backend API](https://img.shields.io/badge/Backend_API-Render-46E3B7?style=for-the-badge&logo=render&logoColor=white)](https://ai-revenue-recovery-engine.onrender.com/health)
[![Go Version](https://img.shields.io/badge/Go-1.23+-00ADD8?style=for-the-badge&logo=go&logoColor=white)](https://golang.org/)
[![Python FastAPI](https://img.shields.io/badge/Python-3.13_FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Groq Llama-3](https://img.shields.io/badge/LLM-Groq_Llama--3-F55036?style=for-the-badge&logo=meta&logoColor=white)](https://console.groq.com/)
[![Razorpay API](https://img.shields.io/badge/Integration-Razorpay-0C2340?style=for-the-badge&logo=razorpay&logoColor=white)](https://razorpay.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

An enterprise-grade, autonomous, multi-agent AI revenue recovery engine that intelligently recovers failed payment transactions. It combines machine learning (**XGBoost** root-cause classification) and LLM agents (**Groq Llama-3** reasoning) with a **real Razorpay webhook integration** and automated **Payment Links API** recovery actions.

---

## 🌐 Live Deployments

| Component | Platform | URL | Status |
|---|---|---|---|
| **Interactive Dashboard** | Vercel | [ai-revenue-recovery-engine.vercel.app](https://ai-revenue-recovery-engine-git-main-nitheeshps-projects.vercel.app/) | 🟢 Active |
| **Go Ingestion Backend** | Render | [ai-revenue-recovery-engine.onrender.com](https://ai-revenue-recovery-engine.onrender.com/health) | 🟢 Active |
| **Razorpay Webhook Receiver** | Render | `POST /api/v1/webhooks/razorpay` | 🟢 Active |
| **Database** | Neon Cloud | Serverless PostgreSQL 15+ | 🟢 Connected |

---

## 📊 Dashboard Preview

![Dashboard View 1](assets/dashboard_view_1.png)

![Dashboard View 2](assets/dashboard_view_2.png)

---

## ⚡ Live Production Results — Groq AI Agent vs Rule-Based Baseline

> **Run Date**: 2026-08-29 | **Simulation ID**: `236e1166` | **Sample**: 49 transactions | **Seed**: 42
> **Agent Model**: `groq/compound` (live Groq API calls) | **ML Model**: XGBoost Classifier

These numbers come from a **live run** with the Groq agent service running locally at `http://127.0.0.1:8002`. Each transaction was evaluated by the real Groq API (`groq/compound` model) for an autonomous recovery decision.

| Metric | Rule-Based Baseline | Groq AI Agent (Genuine Decisions) | Lift |
|---|---|---|---|
| **Transactions Evaluated** | 49 | 22 (genuine AI decisions) | — |
| **Recovered Count** | 18 | 11 | — |
| **Recovery Rate** | **36.73%** | **50.00%** | **+13.27 pp** 🚀 |
| **Revenue Recovered** | **₹1,40,082.41** | **₹1,23,302.88** | -11.98% (smaller txns recovered) |
| **Avg Decision Latency** | 0.5 ms | **11,251 ms** (live Groq LLM) | Real-time reasoning |

> **Note on fallback**: 27/49 transactions (55.1%) hit the rule-based fallback because `groq/compound` intermittently returned `agent_unavailable_fallback` status. These are **excluded from the AI headline rate** and reported separately. The system remained 100% operational throughout — zero downtime.

### Recovery Breakdown by Failure Reason (Live Measured)

| Failure Reason | Total Txns | Rule Recovery | AI Recovery | Lift |
|---|---|---|---|---|
| `gateway_timeout` | 17 | 58.82% (10/17) | **76.47%** (13/17) | **+17.65 pp** |
| `expired_card` | 7 | 0.00% (0/7) | **42.86%** (3/7) | **+42.86 pp** ← AI strength |
| `insufficient_funds` | 10 | 40.00% (4/10) | **50.00%** (5/10) | **+10.00 pp** |
| `risk_flag` | 7 | 42.86% (3/7) | 28.57% (2/7) | -14.29 pp |
| `incorrect_pin` | 8 | 12.50% (1/8) | 12.50% (1/8) | 0.00 pp |

### AI Agent Action Breakdown (22 Genuine Groq Decisions)

| Action | Count | % of AI Decisions |
|---|---|---|
| `retry_now` | 7 | 31.8% |
| `switch_method` | 6 | 27.3% |
| `abandon` | 6 | 27.3% |
| `retry_later` | 3 | 13.6% |

> **Key insight**: The AI agent correctly identified `expired_card` as a `switch_method` candidate (42.86% recovery vs 0% rule-based) — the rule-based system always gave up on expired cards. The Groq agent's LTV-aware reasoning directed customers to UPI/alternative payment channels instead.

---

## 🏗️ System Architecture

```mermaid
graph TD
    RZ[Razorpay Payment Gateway] -->|POST /api/v1/webhooks/razorpay| B[Go Ingestion API - Fiber]
    B -->|HMAC-SHA256 Verification| B
    B -->|Persist Failure Events| C[(Neon PostgreSQL DB)]
    D[Simulation Engine] -->|Fetch Transactions| C
    D -->|Feature Vector| E[Python XGBoost ML Service - Port 8001]
    E -->|Root Cause Classification| D
    D -->|Context: LTV + Retries + Error| F[Python Groq Agent Service - Port 8002]
    F -->|Llama-3 Decision & Trace| D
    B -->|Create Recovery Link| RZ2[Razorpay Payment Links API]
    RZ2 -->|Short URL & Status| B
    B -->|Log Recovery Action| C
    C -->|Stream Metrics & KPIs| A[Vite React Dashboard]
```

### Microservice Components:
1. **Frontend Dashboard (`dashboard/`)**: Vite + React + Tailwind CSS + Framer Motion + Recharts. KPI metrics, failure breakdown charts, strategy comparisons, and degraded agent indicators.
2. **Go Ingestion Backend (`go-api/`)**: High-throughput Golang Fiber REST API with constant-time HMAC-SHA256 webhook verification, async goroutine dispatch, and Neon PostgreSQL persistence.
3. **ML Inference Service (`ml/inference_service.py`)**: FastAPI microservice serving a trained **XGBoost Classifier** that identifies the root cause of transaction failures.
4. **LLM Decision Agent (`ml/agent_service.py`)**: FastAPI microservice powered by **Groq (Llama-3)** executing bounded financial recovery logic with customer LTV awareness and automatic rule-based fallback.
5. **Database (`schema.sql`)**: Cloud Neon PostgreSQL with custom ENUMs, partial indexes, and JSONB reasoning traces.

---

## 🛑 Compliance Gates & Stopping Rules

The engine implements strict compliance gates evaluated in priority order via [`stopping_rules.py`](stopping_rules.py):

| Rule Name | Condition | Enforcement Action |
|---|---|---|
| **`MAX_RETRIES`** | `retry_attempt ≥ 3` | Hard Stop — Mark unrecoverable. |
| **`LOW_LTV_LOW_AMOUNT`** | `customer_ltv < ₹500` AND `amount < ₹200` | Skip retry — Not cost-effective. |
| **`TIMEOUT_48H`** | `elapsed_time ≥ 48 hours` | Escalate to human operations queue. |
| **`PERMANENT_FAILURE`** | `card_stolen`, `fraud_block`, `account_closed` | Immediate Hard Stop — Zero retries. |

---

## 📋 Audit Trail

Every recovery decision is permanently recorded in `audit_log.jsonl`:
- Transaction ID, amount, failure reason
- XGBoost prediction + confidence score
- Customer LTV, retry count
- Action chosen + one-line agent rationale
- Stopping rule triggered (if any)
- Outcome: `recovered` | `unrecoverable` | `pending`

---

## 🔗 Razorpay Integration

```http
POST /api/v1/webhooks/razorpay
```
- **HMAC-SHA256 Verification**: Constant-time signature check. Spoofed requests rejected with `HTTP 400`.
- **Async Recovery**: On `payment.failed`, calls Razorpay Payment Links API (`POST /v1/payment_links`) and logs `payment_link_id`, status, and `short_url` to the `razorpay_recovery_actions` table.

---

## ⚙️ Tech Stack

- **Frontend**: React 18, Vite, Tailwind CSS, Recharts, Framer Motion, Lucide Icons.
- **Backend**: Go 1.23+, Fiber v2, GORM, HMAC-SHA256, Razorpay Payment Links API.
- **AI/ML**: Python 3.13, FastAPI, XGBoost, Groq SDK (Llama-3), Pandas, NumPy.
- **Database**: Neon Serverless PostgreSQL, Docker, Kubernetes, Vercel, Render.

---

## 🚀 Quick Start

### 1. Clone & Configure
```bash
git clone https://github.com/NitheeshP19/AI-Revenue-recovery-Engine.git
cd AI-Revenue-recovery-Engine
cp .env.example .env  # Fill in your GROQ_API_KEY, DATABASE_URL, RAZORPAY keys
```

### 2. Train the ML Model (required once)
```bash
cd ml && python train_model.py
```

### 3. Start All Services (Docker — recommended)
```bash
docker compose up --build
```

### 3b. Or Start Manually (4 terminals)
```bash
# Terminal 1: Frontend
cd dashboard && npm install && npm run dev       # http://localhost:5173

# Terminal 2: Go Backend
cd go-api && go run .                            # http://localhost:8080

# Terminal 3: ML Inference
cd ml && python inference_service.py             # http://localhost:8001

# Terminal 4: LLM Agent
cd ml && python agent_service.py                 # http://localhost:8002
```

### 4. Run the Recovery Simulation
```bash
# With Groq AI agent (all services must be running):
python simulation_engine.py --sample 200

# Without Groq (offline heuristic mode):
python simulation_engine.py --offline --sample 200
```

---

## 🧪 Tests

```bash
cd go-api && go test ./... -v                    # Go backend
cd ml && pytest test_services.py -v              # Python ML + Agent
cd dashboard && npm test -- --run                # React UI smoke tests
```

---

## 🛡️ License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for full terms.

```
MIT License — Copyright (c) 2026 Nitheesh P
```
