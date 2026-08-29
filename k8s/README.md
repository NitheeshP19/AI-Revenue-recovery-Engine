# AI Revenue Recovery Engine — Kubernetes Deployment Guide

## Current Status

> [!IMPORTANT]
> The manifests in this directory are **illustrative infrastructure-as-code**, not a tested production deployment. They are provided to demonstrate Kubernetes fluency and multi-service orchestration patterns.
>
> **What works**: `kubectl apply -f k8s/` will create the namespace, ConfigMaps, Deployments, and Services without errors.
>
> **What needs further work before production use**:
> - Replace the in-cluster PostgreSQL StatefulSet with your Neon Cloud URL (no persistent volume needed).
> - Populate `k8s/secret.yaml` with real base64-encoded values (never commit real secrets to git — use Sealed Secrets or external-secrets-operator).
> - Add an Ingress controller (NGINX or Traefik) with TLS termination.
> - Add a HorizontalPodAutoscaler for the Go API and ML inference services under load.

## Directory Structure

```
k8s/
├── README.md                     ← this file
├── namespace.yaml                ← airevrecovery namespace
├── configmap.yaml                ← non-secret app config
├── secret.yaml                   ← secret template (fill before applying)
├── go-api-deployment.yaml        ← Go ingestion API (Deployment + Service)
├── ml-inference-deployment.yaml  ← Python XGBoost inference (Deployment + Service)
├── agent-deployment.yaml         ← Python Groq agent (Deployment + Service)
└── services.yaml                 ← ClusterIP services for inter-pod comms
```

## Quick Start (Local / Minikube)

```bash
# 1. Fill in secrets first
cp k8s/secret.yaml k8s/secret.local.yaml
# Edit k8s/secret.local.yaml with your real base64 values
# (k8s/secret.local.yaml is in .gitignore)

# 2. Apply in order (namespace first, then config, then workloads)
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.local.yaml
kubectl apply -f k8s/go-api-deployment.yaml
kubectl apply -f k8s/ml-inference-deployment.yaml
kubectl apply -f k8s/agent-deployment.yaml
kubectl apply -f k8s/services.yaml

# 3. Check pod health
kubectl -n airevrecovery get pods
kubectl -n airevrecovery get svc

# Dry-run validation (no cluster needed):
kubectl apply --dry-run=client -f k8s/
```

## Resource Limits

Each service has explicit CPU/memory requests and limits. These are conservative starting points — tune based on actual profiling:

| Service | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---|---|---|---|---|
| go-api | 100m | 500m | 128Mi | 256Mi |
| ml-inference | 200m | 1000m | 256Mi | 512Mi |
| agent | 100m | 500m | 128Mi | 256Mi |

## Health Probes

All deployments include:
- **Readiness probe**: `GET /health` — pod receives traffic only when healthy.
- **Liveness probe**: `GET /health` — pod is restarted if unhealthy for 3 consecutive checks.
