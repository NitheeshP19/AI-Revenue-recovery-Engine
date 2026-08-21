import pandas as pd
import numpy as np
import uuid

# ==============================================================================
# AI Revenue Recovery — Synthetic Data Generator
# Generates 5,000 realistic failed transactions for ML Training
# ==============================================================================

np.random.seed(42)
NUM_RECORDS = 5000

def generate_synthetic_data(n):
    data = []
    payment_methods = ['UPI', 'Credit Card', 'Debit Card', 'Net Banking', 'Wallet']
    failure_reasons = ['insufficient_funds', 'gateway_timeout', 'incorrect_pin', 'expired_card', 'risk_flag']
    
    for _ in range(n):
        method = np.random.choice(payment_methods, p=[0.4, 0.3, 0.2, 0.05, 0.05])
        
        # Correlate failure reason with payment method
        if method == 'UPI':
            reason = np.random.choice(failure_reasons, p=[0.2, 0.6, 0.1, 0.0, 0.1])
        elif method in ['Credit Card', 'Debit Card']:
            reason = np.random.choice(failure_reasons, p=[0.3, 0.1, 0.3, 0.2, 0.1])
        else:
            reason = np.random.choice(failure_reasons)
            
        retries = np.random.choice([0, 1, 2, 3, 4], p=[0.6, 0.2, 0.1, 0.05, 0.05])
        ltv = round(np.random.uniform(0, 50000), 2)
        amount = round(np.random.uniform(10, 15000), 2)
        time_since = np.random.randint(0, 1440) if retries > 0 else 0
        
        # Determine the "Ideal" Recovery Action (Target Variable for ML Simulation)
        if reason == 'expired_card':
            action = 'switch_method'
        elif reason == 'incorrect_pin':
            action = 'switch_method' if retries >= 2 else 'retry_later'
        elif reason == 'gateway_timeout':
            action = 'retry_now' if retries == 0 else 'retry_later'
        elif reason == 'insufficient_funds':
            action = 'retry_later' if ltv > 1000 else 'give_up'
        elif reason == 'risk_flag':
            action = 'give_up'
        else:
            action = 'give_up'
            
        # Hard override for excessive retries
        if retries >= 3:
            action = 'give_up'
            
        data.append({
            'transaction_id': str(uuid.uuid4()),
            'customer_id': str(uuid.uuid4()),
            'amount': amount,
            'payment_method': method,
            'failure_reason_raw': reason,
            'customer_ltv': ltv,
            'recent_retries': retries,
            'time_since_last_attempt_mins': time_since,
            'ideal_recovery_action': action
        })
        
    return pd.DataFrame(data)

if __name__ == "__main__":
    print(f"Generating {NUM_RECORDS} synthetic records...")
    df = generate_synthetic_data(NUM_RECORDS)
    df.to_csv('failed_transactions.csv', index=False)
    print("✅ Successfully generated failed_transactions.csv")
    print("\nAction Breakdown:")
    print(df['ideal_recovery_action'].value_counts(normalize=True) * 100)