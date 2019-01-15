import numpy as np
import cPickle as Pickle
from flarestack.utils.dataset_loader import data_loader
from flarestack.core.energy_PDFs import EnergyPDF
import matplotlib.pyplot as plt
from flarestack.shared import weighted_quantile, floor_pickle, pull_pickle
from flarestack.core.astro import angular_distance
from flarestack.utils.make_SoB_splines import gamma_support_points


def get_mc(floor_dict):
    return data_loader(floor_dict["season"]["mc_path"])


def get_pulls(mc):
    x = np.degrees(angular_distance(
        mc["ra"], mc["dec"], mc["trueRa"], mc["trueDec"]))
    y = np.degrees(mc["sigma"]) * 1.177
    return x/y


n_step = 50
min_loge_gap = 0.2

# def create_quantile_floor(floor_dict):
#
#     path = floor_pickle(floor_dict)
#     print path
#
#     try:
#         floor_dim = int(floor_dict["floor_dim"])
#     except KeyError:
#         floor_dim = 0
#
#     if floor_dim == 0:
#         create_quantile_floor_0d(floor_dict)
#     else:
#         raise ValueError("Bad floor dimension{}".format(floor_dim))


def create_quantile_floor_0d(floor_dict):
    mc = get_mc(floor_dict)
    e_pdf = EnergyPDF.create(floor_dict["e_pdf_dict"])
    weights = e_pdf.weight_mc(mc)

    quantile_floor = weighted_quantile(
        mc["raw_sigma"], floor_dict["floor_quantile"], weights)

    save_path = floor_pickle(floor_dict)

    with open(save_path, "wb") as f:
        Pickle.dump(quantile_floor, f)

    print "Saved to", save_path


def create_quantile_floor_0d_e(floor_dict):
    mc = get_mc(floor_dict)
    e_pdf = EnergyPDF.create(floor_dict["e_pdf_dict"])

    default, bounds, name = e_pdf.return_energy_parameters()

    if len(name) != 1:
        raise Exception("Trying to scan just one energy parameter, "
                        "but selected energy pdf gave the following parameters:"
                        " {} {} {}".format(name, default, bounds))

    x_range = np.linspace(bounds[0][0], bounds[0][1], n_step)
    y_range = []

    for x in x_range:
        weights = e_pdf.weight_mc(mc, x)
        quantile_floor = weighted_quantile(
            mc["raw_sigma"], floor_dict["floor_quantile"], weights)
        y_range.append(quantile_floor)

    y_range = np.array(y_range)

    save_path = floor_pickle(floor_dict)

    res = [x_range, y_range]

    with open(save_path, "wb") as f:
        Pickle.dump(res, f)

    print "Saved to", save_path

    plot_path = floor_pickle(floor_dict)[:-3] + "pdf"

    plt.figure()
    plt.plot(x_range, np.degrees(y_range))
    plt.savefig(plot_path)
    plt.close()


def create_quantile_floor_1d(floor_dict):

    mc = get_mc(floor_dict)
    e_pdf = EnergyPDF.create(floor_dict["e_pdf_dict"])
    weights = e_pdf.weight_mc(mc)

    bins = np.linspace(2., 6., 30)

    x_range = 0.5 * (bins[1:] + bins[:-1])
    y_range = []

    for j, lower in enumerate(bins[:-1]):
        upper = bins[j + 1]
        mask = np.logical_and(
            mc["logE"] >= lower,
            mc["logE"] < upper
        )
        quantile_floor = weighted_quantile(
            mc["raw_sigma"][mask], floor_dict["floor_quantile"], weights[mask])

        y_range.append(quantile_floor)

    x_range = np.array([0.] + list(x_range) + [10.])
    y_range = np.array([y_range[0]] + list(y_range) + [y_range[-1]])

    save_path = floor_pickle(floor_dict)
    res = [x_range, y_range]

    with open(save_path, "wb") as f:
        Pickle.dump(res, f)
    print "Saved to", save_path

    plot_path = floor_pickle(floor_dict)[:-3] + "pdf"

    plt.figure()
    plt.plot(x_range, np.degrees(y_range))
    plt.savefig(plot_path)
    plt.close()


def create_quantile_floor_1d_e(floor_dict):

    mc = get_mc(floor_dict)
    e_pdf = EnergyPDF.create(floor_dict["e_pdf_dict"])

    default, bounds, name = e_pdf.return_energy_parameters()

    if name != ["gamma"]:
        raise Exception("Trying to scan gamma parameter, "
                        "but selected energy pdf gave the following parameters:"
                        " {} {} {}".format(name, default, bounds))

    e_range = np.linspace(bounds[0][0], bounds[0][1], n_step)

    bins = np.linspace(2., 6., 20)

    z_range = []

    for j, lower in enumerate(bins[:-1]):
        upper = bins[j + 1]
        mask = np.logical_and(
            mc["logE"] >= lower,
            mc["logE"] < upper
        )

        cut_mc = mc[mask]

        vals = []

        for e in e_range:
            weights = e_pdf.weight_mc(cut_mc, e)

            quantile_floor = weighted_quantile(
                cut_mc["raw_sigma"], floor_dict["floor_quantile"], weights)

            vals.append(quantile_floor)

        z_range.append(vals)

    x_range = 0.5 * (bins[1:] + bins[:-1])
    x_range = np.array([0.] + list(x_range) + [10.])

    z_range = np.array([z_range[0]] + z_range + [z_range[-1]])

    save_path = floor_pickle(floor_dict)
    res = [x_range, e_range, z_range]

    with open(save_path, "wb") as f:
        Pickle.dump(res, f)
    print "Saved to", save_path

    from scipy.interpolate import RectBivariateSpline

    spline = RectBivariateSpline(
        x_range, e_range, np.log(np.degrees(z_range)),
        kx=1, ky=1, s=0)

    Z = []
    for x in x_range:
        Z.append(spline(x, e_range)[0])
    Z = np.array(Z)

    plot_path = floor_pickle(floor_dict)[:-3] + "pdf"

    ax = plt.subplot(111)
    X, Y = np.meshgrid(x_range, e_range)
    # cbar = ax.pcolor(X, Y, np.log(np.degrees(z_range.T)), cmap="viridis", )
    cbar = ax.pcolor(X, Y, Z.T, cmap="viridis", )
    plt.colorbar(cbar, label="Log(Angular Error Floor/deg)")
    plt.ylabel(name[0])
    plt.xlabel("Log(Energy proxy)")
    plt.savefig(plot_path)
    plt.close()


def create_pull_0d_e(pull_dict):
    mc = get_mc(pull_dict)
    pulls = get_pulls(mc)
    e_pdf = EnergyPDF.create(pull_dict["e_pdf_dict"])

    default, bounds, name = e_pdf.return_energy_parameters()

    if name != ["gamma"]:
        raise Exception("Trying to scan gamma parameter, "
                        "but selected energy pdf gave the following parameters:"
                        " {} {} {}".format(name, default, bounds))

    res_dict = dict()

    x_range = np.array(list(gamma_support_points))

    y_range = []

    for x in x_range:
        weights = e_pdf.weight_mc(mc, x)
        median_pull = weighted_quantile(
            pulls, 0.5, weights)
        y_range.append(median_pull)
        res_dict[x] = median_pull

    y_range = np.array(y_range)

    save_path = pull_pickle(pull_dict)
    plot_path = save_path[:-3] + "pdf"

    print x_range, y_range

    plt.figure()
    plt.plot(x_range, y_range)
    plt.axhline(1.0, linestyle="--")
    plt.ylabel("Median Pull")
    plt.xlabel(name[0])
    plt.savefig(plot_path)
    plt.close()

    with open(save_path, "wb") as f:
        Pickle.dump(res_dict, f)

    print "Saved to", save_path



def create_pull_1d(pull_dict):
    mc = get_mc(pull_dict)
    pulls = get_pulls(mc)
    e_pdf = EnergyPDF.create(pull_dict["e_pdf_dict"])
    weights = e_pdf.weight_mc(mc)

    bins = np.linspace(2., 6., 15)

    x_range = 0.5 * (bins[1:] + bins[:-1])
    y_range = []

    for j, lower in enumerate(bins[:-1]):
        upper = bins[j + 1]
        mask = np.logical_and(
            mc["logE"] >= lower,
            mc["logE"] < upper
        )
        median_pull = weighted_quantile(
            pulls[mask], 0.5, weights[mask])
        y_range.append(median_pull)

    x_range = np.array([0.] + list(x_range) + [10.])
    y_range = np.array([y_range[0]] + list(y_range) + [y_range[-1]])

    save_path = pull_pickle(pull_dict)

    res = [x_range, y_range]

    with open(save_path, "wb") as f:
        Pickle.dump(res, f)

    print "Saved to", save_path

    plot_path = save_path[:-3] + "pdf"

    plt.figure()
    plt.plot(x_range, y_range)
    plt.axhline(1.0, linestyle="--")
    plt.ylabel("Median Pull")
    plt.xlabel("Log(Energy Proxy/GeV)")
    plt.savefig(plot_path)
    plt.close()
