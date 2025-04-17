# repeated_trade_war_sep.py
# U.S.–China repeated game with separate economic and political payoffs
# Requires: numpy, pandas, matplotlib

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# ------------------------------------------------------------------
# 1. Tunable parameters
# ------------------------------------------------------------------
ROUNDS           = 50
GAMMA_US         = 0.60          # U.S. discount factor
GAMMA_CN         = 0.90          # China discount factor
SHOCK_PROB       = 0.20          # probability of external shock
SHOCK_PENALTY    = -2            # shock hits economic payoff only
US_POL_BONUS     = 2             # political points if U.S. escalates
CN_POL_BONUS     = 5             # political points if China escalates
REPUTATION_DECAY = 0.90          # reputation multiplier after escalation

# ------------------------------------------------------------------
# 2. Economic payoff matrix  (US move, CN move) ➜ (econ_US, econ_CN)
#    (political bonuses are added later)
# ------------------------------------------------------------------
ECON_PAYOFF = {
    ('E', 'E'): (-5,  0),
    ('E', 'D'): (+1, -3),
    ('D', 'E'): (-3, +1),
    ('D', 'D'): (+4, +3)
}

def tit_for_tat(prev):
    """Repeat opponent’s previous move; default to D."""
    return prev if prev else 'D'

def china_strategy(prev_us, rnd, us_rep):
    """
    China cooperates unless U.S. reputation < 0.8 or game past midpoint,
    then switches to escalate.
    """
    if us_rep < 0.8 or rnd > ROUNDS // 2:
        return 'E'
    return tit_for_tat(prev_us)

# ------------------------------------------------------------------
# 3. Main simulation loop
# ------------------------------------------------------------------
hist = []
us_rep = cn_rep = 1.0
us_prev = 'E'
cn_prev = None

for rnd in range(1, ROUNDS + 1):
    us_move = tit_for_tat(cn_prev)
    cn_move = china_strategy(us_prev, rnd, us_rep)

    # Economic payoff
    us_econ, cn_econ = ECON_PAYOFF[(us_move, cn_move)]

    # External shock on economic component
    shock = np.random.rand() < SHOCK_PROB
    if shock:
        us_econ += SHOCK_PENALTY
        cn_econ += SHOCK_PENALTY

    # Political payoff
    us_pol = US_POL_BONUS if us_move == 'E' else 0
    cn_pol = CN_POL_BONUS if cn_move == 'E' else 0

    # Apply discounting
    us_econ_disc = us_econ * (GAMMA_US ** (rnd - 1))
    us_pol_disc  = us_pol  * (GAMMA_US ** (rnd - 1))
    cn_econ_disc = cn_econ * (GAMMA_CN ** (rnd - 1))
    cn_pol_disc  = cn_pol  * (GAMMA_CN ** (rnd - 1))

    # Reputation decay
    if us_move == 'E':
        us_rep *= REPUTATION_DECAY
    if cn_move == 'E':
        cn_rep *= REPUTATION_DECAY

    hist.append({
        'Round': rnd,
        'US_Move': us_move,
        'CN_Move': cn_move,
        'Shock': shock,
        'US_Econ': us_econ,
        'US_Pol': us_pol,
        'CN_Econ': cn_econ,
        'CN_Pol': cn_pol,
        'US_Econ_D': us_econ_disc,
        'US_Pol_D':  us_pol_disc,
        'CN_Econ_D': cn_econ_disc,
        'CN_Pol_D':  cn_pol_disc,
        'US_Rep': us_rep,
        'CN_Rep': cn_rep
    })

    us_prev, cn_prev = us_move, cn_move

df = pd.DataFrame(hist)

# ------------------------------------------------------------------
# 4. Plot cumulative economic vs political payoffs + reputations
# ------------------------------------------------------------------
fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

# cumulative economic
ax1.plot(df['Round'], df['US_Econ_D'].cumsum(), label='U.S. economic')
ax1.plot(df['Round'], df['CN_Econ_D'].cumsum(), label='China economic')
ax1.set_ylabel('Cumulative economic payoff')
ax1.legend(); ax1.grid(True)

# cumulative political
ax2.plot(df['Round'], df['US_Pol_D'].cumsum(), label='U.S. political')
ax2.plot(df['Round'], df['CN_Pol_D'].cumsum(), label='China political')
ax2.set_ylabel('Cumulative political payoff')
ax2.legend(); ax2.grid(True)

# reputation
ax3.plot(df['Round'], df['US_Rep'], label='U.S. reputation')
ax3.plot(df['Round'], df['CN_Rep'], label='China reputation')
ax3.set_ylabel('Reputation')
ax3.set_xlabel('Round')
ax3.set_yticks(np.linspace(0,1,6))
ax3.legend(); ax3.grid(True)

plt.tight_layout()
plt.show()
plt.savefig('repeated_trade_war_sep.png', dpi=300)

# ------------------------------------------------------------------
# 5. Console summary
# ------------------------------------------------------------------
print('Final cumulative discounted payoffs')
print('  U.S.  economic:', round(df["US_Econ_D"].sum(), 2),
      ' political:', round(df["US_Pol_D"].sum(), 2),
      ' total:',     round(df["US_Econ_D"].sum() + df["US_Pol_D"].sum(), 2))
print('  China economic:', round(df["CN_Econ_D"].sum(), 2),
      ' political:', round(df["CN_Pol_D"].sum(), 2),
      ' total:',     round(df["CN_Econ_D"].sum() + df["CN_Pol_D"].sum(), 2))
