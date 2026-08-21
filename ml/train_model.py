"""
===============================================================================
  AI Revenue Recovery System — ML Failure Classifier
  Phase 3 | Model Training Script
  Author  : Lead ML Engineer
  Version : 1.0.0
-------------------------------------------------------------------------------
  PURPOSE:
    Trains an XGBClassifier to predict `failure_reason_raw` from transaction
    features. The model serves as an ML verification layer that:
      • Cross-checks the gateway-reported failure reason
      • Produces per-class probability vectors the LLM agent uses as signals
      • Flags low-confidence predictions for human review

  SETUP:
    pip install xgboost scikit-learn pandas numpy

  SAFE EXPORT (no pickle / no joblib — XGBoost native JSON only):
    classifier.json    — XGBClassifier model weights (XGBoost native format)
    label_map.json     — {class_index: class_name} mapping for inference
    feature_cols.json  — Ordered feature list (MUST match inference_service.py)
===============================================================================
"""

# ── 0. Colab Install Block ─────────────────────────────────────────────────────
# Uncomment if running in a fresh Colab runtime:
# !pip install xgboost==2.1.1 scikit-learn pandas numpy --quiet

import json
import time
import numpy as np
import pandas as pd
import xgboost as xgb
from pathlib import Path
from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import classification_report, confusion_matrix, ConfusionMatrixDisplay
import matplotlib.pyplot as plt

print(f"XGBoost version : {xgb.__version__}")
print(f"NumPy   version : {np.__version__}")
print(f"Pandas  version : {pd.__version__}")

# ── 1. Configuration ───────────────────────────────────────────────────────────

SEED       = 42
CSV_PATH   = "failed_transactions.csv"   # upload to Colab before running
TEST_SIZE  = 0.20
N_CV_FOLDS = 5

# Canonical payment method categories (must exactly match inference_api.py)
PAYMENT_METHODS = ["UPI", "Credit Card", "Debit Card"]
TARGET_COL      = "failure_reason_raw"

np.random.seed(SEED)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION A — Feature Engineering
#  CRITICAL: Every transformation here MUST be replicated identically in
#  inference_api.py's `engineer_features()` function. Any drift = wrong preds.
# ══════════════════════════════════════════════════════════════════════════════

def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deterministic, stateless feature engineering pipeline.

    Transformations applied:
      1. retry_velocity  — rate of retries per unit time; captures "impatient
                           customer" vs "system-driven retry" behaviour.
      2. log_amount      — log1p of transaction amount; compresses the heavy
                           right tail of the lognormal amount distribution.
      3. ltv_tier        — ordinal bucketing of customer_ltv into 3 tiers;
                           provides non-linear LTV signal without overfitting.
      4. pm_*            — one-hot encoding of payment_method; each method has
                           distinct failure-reason correlations (see Phase 1
                           FAILURE_PROB_MATRIX).

    Returns:
        DataFrame with original columns plus engineered columns. Does NOT drop
        the raw source columns — the FEATURE_COLS list controls what XGBoost sees.
    """
    df = df.copy()

    # ── Feature 1: retry_velocity ──────────────────────────────────────────────
    # +1 smoothing prevents division by zero when time_since_last_attempt = 0.
    # A high value = many retries in a short window → likely gateway-side issue.
    df["retry_velocity"] = (
        df["recent_retries"] / (df["time_since_last_attempt_mins"] + 1.0)
    )

    # ── Feature 2: log_amount ─────────────────────────────────────────────────
    # log1p is safe for amount=0 (shouldn't occur in our data, but defensive).
    df["log_amount"] = np.log1p(df["amount"])

    # ── Feature 3: ltv_tier (ordinal) ─────────────────────────────────────────
    # Tier 0 = low value (<= 500), Tier 1 = mid (500–2000), Tier 2 = premium (>2000)
    # These thresholds align with the bimodal LTV distribution in the generator.
    df["ltv_tier"] = pd.cut(
        df["customer_ltv"],
        bins=[-1, 500, 2_000, float("inf")],
        labels=[0, 1, 2],
    ).astype(int)

    # ── Feature 4: One-hot encode payment_method ───────────────────────────────
    # Explicit column list ensures inference produces the same shape even if
    # a batch only contains 1 or 2 methods (avoids missing-column KeyError).
    for method in PAYMENT_METHODS:
        col = f"pm_{method.replace(' ', '_')}"
        df[col] = (df["payment_method"] == method).astype(int)

    return df


# FEATURE_COLS is the single source of truth for model input dimensionality.
# This list is saved to feature_cols.json and loaded verbatim in inference_api.py.
FEATURE_COLS = [
    "amount",               # raw transaction amount (INR)
    "log_amount",           # log-compressed amount
    "customer_ltv",         # customer lifetime value snapshot
    "ltv_tier",             # ordinal LTV bucket (0/1/2)
    "recent_retries",       # number of prior attempts
    "time_since_last_attempt_mins",  # minutes since last try
    "retry_velocity",       # recent_retries / (time + 1) — engineered
    "pm_UPI",               # one-hot: is UPI?
    "pm_Credit_Card",       # one-hot: is Credit Card?
    "pm_Debit_Card",        # one-hot: is Debit Card?
]


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION B — Data Loading & Preprocessing
# ══════════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 65)
print("  PHASE 3 — ML Failure Classifier Training")
print("=" * 65)

print(f"\n[1/7] Loading dataset from '{CSV_PATH}'...")
df_raw = pd.read_csv(CSV_PATH)
print(f"      Loaded {len(df_raw):,} rows × {df_raw.shape[1]} columns")
print(f"      Target distribution:\n{df_raw[TARGET_COL].value_counts()}\n")

# Apply feature engineering
print("[2/7] Applying feature engineering...")
df = engineer_features(df_raw)

# Verify all feature columns exist
missing = [c for c in FEATURE_COLS if c not in df.columns]
assert not missing, f"Missing engineered columns: {missing}"
print(f"      Feature matrix shape: {df[FEATURE_COLS].shape}")
print(f"      Features: {FEATURE_COLS}")


# ── Target Encoding ────────────────────────────────────────────────────────────
print("\n[3/7] Encoding target variable (failure_reason_raw)...")
le = LabelEncoder()
y = le.fit_transform(df[TARGET_COL])
X = df[FEATURE_COLS].astype(np.float32)

class_names   = le.classes_           # e.g. ["expired_card", "gateway_timeout", ...]
n_classes     = len(class_names)

print(f"      Classes ({n_classes}): {list(class_names)}")
print(f"      Class → index: { {c: int(i) for i, c in enumerate(class_names)} }")

# Train / test split — stratified to preserve class proportions
X_train, X_test, y_train, y_test = train_test_split(
    X, y,
    test_size=TEST_SIZE,
    random_state=SEED,
    stratify=y,
)
print(f"      Train: {len(X_train):,} | Test: {len(X_test):,}")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION C — Model Training
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n[4/7] Training XGBClassifier (seed={SEED})...")
t0 = time.perf_counter()

model = xgb.XGBClassifier(
    # ── Tree parameters ────────────────────────────────────────────────────────
    n_estimators       = 400,        # number of boosting rounds
    max_depth          = 6,          # tree depth; balanced bias-variance
    min_child_weight   = 3,          # min sum of instance weight per leaf
    gamma              = 0.1,        # min loss reduction to split further
    subsample          = 0.85,       # row sampling per tree (bagging)
    colsample_bytree   = 0.85,       # column sampling per tree
    # ── Learning parameters ────────────────────────────────────────────────────
    learning_rate      = 0.08,       # shrinkage — slower but more robust
    # ── Objective ─────────────────────────────────────────────────────────────
    objective          = "multi:softprob",   # returns P(class) per sample
    num_class          = n_classes,
    eval_metric        = "mlogloss",
    # ── Regularisation ────────────────────────────────────────────────────────
    reg_alpha          = 0.05,       # L1 — drives sparse feature weights
    reg_lambda         = 1.5,        # L2 — shrinks all weights
    # ── Reproducibility & performance ─────────────────────────────────────────
    random_state       = SEED,
    n_jobs             = -1,         # use all CPU cores
    tree_method        = "hist",     # fastest CPU training algorithm
    device             = "cpu",      # Colab GPU optional: change to "cuda"
)

model.fit(
    X_train, y_train,
    eval_set    = [(X_train, y_train), (X_test, y_test)],
    verbose     = 100,       # print every 100 rounds
)

train_duration = time.perf_counter() - t0
print(f"\n      Training complete in {train_duration:.2f}s")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION D — Evaluation
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n[5/7] Evaluating on hold-out test set ({len(X_test):,} samples)...")

y_pred      = model.predict(X_test)
y_pred_prob = model.predict_proba(X_test)

print("\n" + "=" * 65)
print("  CLASSIFICATION REPORT")
print("=" * 65)
print(classification_report(y_test, y_pred, target_names=class_names, digits=4))

# ── Cross-validation F1 (macro) ───────────────────────────────────────────────
print(f"[*] Running {N_CV_FOLDS}-fold stratified cross-validation (macro-F1)...")
cv_scores = cross_val_score(
    model, X, y,
    cv      = StratifiedKFold(n_splits=N_CV_FOLDS, shuffle=True, random_state=SEED),
    scoring = "f1_macro",
    n_jobs  = -1,
)
print(f"    CV macro-F1: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

# ── Top 3 Most Important Features ─────────────────────────────────────────────
importances = model.feature_importances_
feat_pairs  = sorted(zip(FEATURE_COLS, importances), key=lambda x: x[1], reverse=True)

print("\n" + "=" * 65)
print("  TOP 3 MOST IMPORTANT FEATURES (gain-based)")
print("=" * 65)
for rank, (feat, imp) in enumerate(feat_pairs[:3], start=1):
    bar = "█" * int(imp * 50)
    print(f"  {rank}. {feat:<35} {imp:.4f}  {bar}")

print("\n  Full feature importance ranking:")
for feat, imp in feat_pairs:
    bar = "░" * int(imp * 50)
    print(f"     {feat:<35} {imp:.4f}  {bar}")

# ── Confusion Matrix Plot ──────────────────────────────────────────────────────
try:
    cm  = confusion_matrix(y_test, y_pred)
    fig, ax = plt.subplots(figsize=(8, 6))
    disp = ConfusionMatrixDisplay(confusion_matrix=cm, display_labels=class_names)
    disp.plot(ax=ax, colorbar=True, cmap="Blues")
    ax.set_title("Failure Reason Classifier — Confusion Matrix (Test Set)", pad=12)
    plt.tight_layout()
    plt.savefig("confusion_matrix.png", dpi=120)
    plt.show()
    print("\n[*] Confusion matrix saved → confusion_matrix.png")
except Exception as e:
    print(f"[!] Confusion matrix plot skipped: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION E — Safe Export (XGBoost native JSON — no pickle / no joblib)
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n[6/7] Saving model artefacts (XGBoost native JSON)...")

# ── classifier.json ────────────────────────────────────────────────────────────
# XGBoost's native JSON format is:
#   • Portable across XGBoost versions (unlike pickle)
#   • Inspectable as plain text (no binary blobs)
#   • Used directly by xgb.Booster.load_model() and XGBClassifier.load_model()
MODEL_PATH = "classifier.json"
model.save_model(MODEL_PATH)
print(f"    [SAVED] {MODEL_PATH}")

# ── label_map.json ─────────────────────────────────────────────────────────────
# Maps integer class indices → human-readable failure reason strings.
# Saved as JSON (not pickle) so the inference service can load it with json.load().
label_map = {str(i): cls for i, cls in enumerate(class_names)}
LABEL_MAP_PATH = "label_map.json"
with open(LABEL_MAP_PATH, "w", encoding="utf-8") as f:
    json.dump(label_map, f, indent=2, ensure_ascii=False)
print(f"    [SAVED] {LABEL_MAP_PATH}  →  {label_map}")

# ── feature_cols.json ──────────────────────────────────────────────────────────
# The exact ordered feature list the model was trained on.
# inference_api.py reads this file to guarantee column order alignment.
FEATURE_COLS_PATH = "feature_cols.json"
with open(FEATURE_COLS_PATH, "w", encoding="utf-8") as f:
    json.dump(FEATURE_COLS, f, indent=2)
print(f"    [SAVED] {FEATURE_COLS_PATH}")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION F — Smoke Test (round-trip sanity check)
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n[7/7] Running round-trip smoke test (load → predict)...")

# Load from disk exactly as inference_api.py will
smoke_model = xgb.XGBClassifier()
smoke_model.load_model(MODEL_PATH)

with open(LABEL_MAP_PATH) as f:
    smoke_label_map = json.load(f)

# Take one sample from each class for the smoke test
for cls_idx, cls_name in enumerate(class_names):
    sample_mask = y_test == cls_idx
    if sample_mask.sum() == 0:
        continue
    sample = X_test[sample_mask].iloc[[0]]
    pred_idx  = int(smoke_model.predict(sample)[0])
    pred_prob = float(smoke_model.predict_proba(sample)[0][pred_idx])
    pred_name = smoke_label_map[str(pred_idx)]
    match = "OK" if pred_idx == cls_idx else "MISMATCH"
    print(f"    [{match}] True: {cls_name:<22} | Pred: {pred_name:<22} | Conf: {pred_prob:.4f}")

print("\n" + "=" * 65)
print("  TRAINING COMPLETE")
print("=" * 65)
print(f"\n  Exported 3 files for the local inference service:")
print(f"    1. {MODEL_PATH}")
print(f"    2. {LABEL_MAP_PATH}")
print(f"    3. {FEATURE_COLS_PATH}")
print("=" * 65)
