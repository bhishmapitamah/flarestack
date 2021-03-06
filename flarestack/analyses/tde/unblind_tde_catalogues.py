"""Script to unblind the TDE catalogues. Draws the background TS values
generated by compare_spectral_indices.py, in order to
quantify the significance of the result. Produces relevant post-unblinding
plots.
"""
from __future__ import print_function
from builtins import input
import numpy as np
from flarestack.core.unblinding import create_unblinder
from flarestack.data.icecube.gfu.gfu_v002_p01 import txs_sample_v1
from flarestack.utils.custom_dataset import custom_dataset
from flarestack.analyses.tde.shared_TDE import tde_catalogue_name, \
    tde_catalogues

analyses = dict()

# Initialise Injectors/LLHs

# Shared

llh_energy = {
    "Name": "Power Law",
    "Gamma": 2.0,
}

# llh_time = {
#     "Name": "FixedEndBox"
# }
llh_time = {
    "Name": "Steady"
}
# llh_time = {
#     "Name": "Box",
#     "Pre-Window": 0,
#     "Post-Window": 50
# }

unblind_llh = {
    "name": "standard_matrix",
    "LLH Energy PDF": llh_energy,
    "LLH Time PDF": llh_time,
}

name_root = "analyses/tde/unblind_stacked_TDEs/"
bkg_ts_root = "analyses/tde/compare_spectral_indices/Emin=100/"

cat_res = dict()

res = []

for j, cat in enumerate(tde_catalogues[1:]):

    name = name_root + cat.replace(" ", "") + "/"

    bkg_ts = bkg_ts_root + cat.replace(" ", "") + "/fit_weights/"

    cat_path = tde_catalogue_name(cat)
    catalogue = np.load(cat_path)

    unblind_dict = {
        "name": name,
        "mh_name": "fixed_weights",
        "datasets": custom_dataset(txs_sample_v1, catalogue,
                                   unblind_llh["LLH Time PDF"]),
        "catalogue": cat_path,
        "llh_dict": unblind_llh,
        "background TS": bkg_ts
    }

    # ub = create_unblinder(unblind_dict, mock_unblind=False)
    ub = create_unblinder(unblind_dict, mock_unblind=True)
    input("prompt")

    res.append((cat, ub.ts))

for x in res:
    print(x)
