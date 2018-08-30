"""File containing links to data samples used (GFU).

This is the sample that was used for the second TXS analysis up to March 2018
"""

from shared import dataset_dir
from icecube_pointsource_7_year import ps_7year
import numpy as np

gfu_data_dir = dataset_dir + "gfu_v002-p02/"

gfu_dict = {
    "Data Sample": "gfu_v002-p02",
    "sinDec bins": np.unique(np.concatenate([
        np.linspace(-1., -0.9, 2 + 1),
        np.linspace(-0.9, -0.2, 8 + 1),
        np.linspace(-0.2, 0.2, 15 + 1),
        np.linspace(0.2, 0.9, 12 + 1),
        np.linspace(0.9, 1., 2 + 1),
    ])),
    "MJD Time Key": "time",
    "Name": "GFU_v002_p02",
    "exp_path": [
        gfu_data_dir + "SplineMPEmax.MuEx.IC86-2015.npy",
        gfu_data_dir + "SplineMPEmax.MuEx.IC86-2016.npy",
        gfu_data_dir + "SplineMPEmax.MuEx.IC86-2017.npy"
    ],
    "mc_path": gfu_data_dir + "SplineMPEmax.MuEx.MC.npy",
    "grl_path": gfu_data_dir + "SplineMPEmax.MuEx.GRL.npy"
}

gfu_v002_p02 = [gfu_dict]

txs_sample_v2 = ps_7year + gfu_v002_p02
