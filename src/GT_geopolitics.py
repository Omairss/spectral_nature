"""
multi_align_game.py
Minimal simulation of alignment choices:
    - Each partner chooses 'US' or 'CN'
    - Payoff = economic + geopolitical + reputation
    - US and China score the number (or GDP‑weighted sum) of partners on their side
"""
import itertools
import math
import pandas as pd

# ------------ configuration ------------
alpha = 2.0   # weight on economic utility
beta  = 5.0   # geopolitical penalty if SCS country sides with China
gamma = -3.0   # reputational bonus if democracy sides with US
delta = 3.0   # great‑power gain per ally

partners = pd.DataFrame(
  columns=['trade_us','trade_cn','scs','dem'],
  data = [
    # trade_us, trade_cn, scs, dem
    [757, 897, False, True ],   # EU
    [850,  96, False, True ],   # Canada
    [799, 113, False, True ],   # Mexico
    [218, 189, False, True ],   # Japan
    [118, 136, True, True],   # India
    [ 38, 127, True , True],   # Indonesia
    [134, 324, True, True ],   # South Korea
    [ 63, 220, True, True ],   # Australia
    [124, 175, True , False],   # Vietnam
  ],
  index=['EU','Canada','Mexico','Japan','India','Indonesia','S.Korea','Australia','Vietnam']
)

# ------------ helper to compute one alignment profile ------------
def score_profile(choices: dict):
    """
    choices: {'EU':'US', 'Canada':'CN', ...}
    returns tuple (partner_scores, US_score, CN_score)
    """
    partner_scores = {}
    us_allies, cn_allies = 0, 0
    for ctry, side in choices.items():
        row = partners.loc[ctry]
        econ = math.log(row['trade_us']) if side=='US' else math.log(row['trade_cn'])
        econ *= alpha
        geo  = -beta if (row['scs'] and side=='CN') else 0
        rep  =  gamma if (row['dem'] and side=='US') else 0
        partner_scores[ctry] = econ + geo + rep
        if side=='US':  us_allies += 1
        else:           cn_allies += 1
    us_score = delta * us_allies
    cn_score = delta * cn_allies
    return partner_scores, us_score, cn_score

# ------------ exhaustive Nash check (small set = 2^9 = 512 profiles) ------------
best_reply = {}
profile_payoffs = {}

for profile in itertools.product(['US','CN'], repeat=len(partners)):
    choice_map = dict(zip(partners.index, profile))
    p_scores, us_scr, cn_scr = score_profile(choice_map)
    profile_payoffs[tuple(profile)] = (p_scores, us_scr, cn_scr)

# simple inspection: which profile maximises total partner welfare?
best_profile = max(profile_payoffs.items(),
                   key=lambda item: sum(item[1][0].values()) )
print("Highest‑total‑welfare profile (partners’ view):")
print(dict(zip(partners.index, best_profile[0])))
print("Partner payoffs:", best_profile[1][0])
print("US grand payoff:", best_profile[1][1], "  China:", best_profile[1][2])
