"""T-023 variants: smaller n0 multipliers and availability-share priors for population rows. runs/pop3_<T>_<cp>.pkl"""
import pickle
import time

import bt
import pandas as pd

from fantasy.model import inseason as I

if __name__ == "__main__":
    for T in (2025, 2026):
        pg, tg, fg = bt.load(T)
        prior = pd.read_csv(bt.OUT / f"priors_{T}.csv")
        for cp in bt.checkpoints(tg)[1:]:
            t0 = time.time()
            res = {}
            for mult in (0.02, 0.05, 0.1, 0.25):
                for sh in (None, 0.5, 0.6):
                    bt.set_pop(T, mult)
                    if sh is not None:
                        I.POP_PRIOR = {**I.POP_PRIOR, "share": sh}
                    res[f"m{mult:g}_s{sh}"] = bt.run(T, cp, prior, pg, tg, fg)
            pickle.dump(res, open(bt.RUNS / f"pop3_{T}_{cp}.pkl", "wb"))
            print(T, cp, f"{time.time() - t0:.0f}s", flush=True)
