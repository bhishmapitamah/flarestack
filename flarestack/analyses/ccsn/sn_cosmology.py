from __future__ import division
from builtins import str
from flarestack.utils.neutrino_cosmology import calculate_transient, \
    sfr_madau, sfr_clash_candels, get_diffuse_flux_at_1GeV
from flarestack.analyses.ccsn.ccsn_limits import limits
from flarestack.core.energy_PDFs import EnergyPDF
from astropy import units as u
from astropy.cosmology import Planck15 as cosmo
import numpy as np
from flarestack.misc.convert_diffuse_flux_contour import contour_95, \
    upper_contour, lower_contour, global_fit_e_range
import matplotlib.pyplot as plt
from flarestack.shared import plot_output_dir
import os


def ccsn_clash_candels(z):
    """Best fit k from paper https://arxiv.org/pdf/1509.06574.pdf"""
    # Why divide by h^3???
    return 0.0091 * sfr_clash_candels(z) * cosmo.h**2. / cosmo.h**3


def ccsn_madau(z):
    """"Best fit k from http://arxiv.org/pdf/1403.0007v3.pdf"""
    return 0.0068 * sfr_madau(z)


def get_sn_fraction(sn_type):
    """Return SN rates for specific types. These are taken from
    https://arxiv.org/pdf/1509.06574.pdf, and are assumed to be fixed
    fractions of the overall SN rate. Acceptable types are:
        SNIIn
        SNIIP
        SNIb
        SNIc
        SNIbc (equal to Ib + Ic)

    :param sn_type: Type of SN
    :return: fraction represented by that subtype
    """
    if sn_type == "IIn":
        return 0.064
    elif sn_type == "IIP":
        return 0.52
    elif sn_type == "Ib":
        return 0.069
    elif sn_type == "Ic":
        return 0.176
    elif sn_type == "Ibc":
        return 0.069 + 0.176
    else:
        raise Exception("SN sn_type " + str(sn_type) + " not recognised!")


def get_sn_type_rate(fraction=1.0, sn_type=None, rate=ccsn_clash_candels):
    """Return SN rates for given fraction of the CCSN rate, or specific types.
    The types are taken from https://arxiv.org/pdf/1509.06574.pdf, and are
    assumed to be fixed fractions of the overall SN rate. Acceptable types are:
        IIn
        IIP
        Ib
        Ic
        Ibc (equal to Ib + Ic)

    :param fraction: Fraction of SN
    :param sn_type: Type of SN
    :param rate: CCSN rate to be used (Clash Candels by default)
    :return: corresponding rate
    """

    if (fraction != 1.0) and (sn_type is not None):
        raise Exception("Type and fraction both specified!")
    elif sn_type is not None:
        return lambda x: get_sn_fraction(sn_type) * rate(x)
    else:
        return lambda x: fraction * rate(x)


if __name__ == "__main__":

    e_pdf_dict_template = {
        "Name": "Power Law",
        "E Min": 10 ** 2,
        "E Max": 10 ** 7,
    }

    results = [
        ["IIn", 1.0],
        ["IIP", 1.0],
        ["Ibc", 1.0]
    ]

    norms = dict()

    for [name, nu_bright] in results:

        def f(x):
            return get_sn_type_rate(sn_type=name)(x) * nu_bright

        e_pdf_dict = dict(e_pdf_dict_template)

        energy_pdf = EnergyPDF.create(e_pdf_dict)

        e_pdf_dict["Source Energy (erg)"] = limits[name]["Fixed Energy (erg)"]
        # e_pdf_dict["Source Energy (erg)"] = ccsn_energy_limit(name,
        # diffuse_gamma)
        norms[name] = calculate_transient(e_pdf_dict, f, name, zmax=6.0,
                                          nu_bright_fraction=nu_bright,
                                          diffuse_fit="Joint")

    base_dir = plot_output_dir("analyses/ccsn/")

    e_range = np.logspace(2.73, 5.64, 3)

    try:
        os.makedirs(base_dir)
    except OSError:
        pass

    def z(energy, norm):
        return norm * energy ** -0.5

    plt.figure()

    # Plot 95% contour

    plt.fill_between(
        global_fit_e_range,
        global_fit_e_range ** 2 * upper_contour(global_fit_e_range, contour_95),
        global_fit_e_range ** 2 * lower_contour(global_fit_e_range, contour_95),
        color="k", label='IceCube diffuse flux\nApJ 809, 2015',
        alpha=.5,
    )

    diffuse_norm, diffuse_gamma = get_diffuse_flux_at_1GeV("Joint")

    plt.plot(global_fit_e_range,
             diffuse_norm * global_fit_e_range ** (2. - diffuse_gamma),
             color="k")

    for i,(name, norm) in enumerate(norms.items()):
        # plt.plot(e_range, z(e_range, norm), label=name)
        plt.errorbar(e_range, z(e_range, norm).value,
                     yerr=.25 * np.array([x.value for x in z(e_range, norm)]),
                     uplims=True, color=["b", "r", "orange"][i],
                     label="Supernovae Type {0}".format(name))

    plt.yscale("log")
    plt.xscale("log")
    plt.legend()
    plt.title(r"Diffuse Flux Global Best Fit ($\nu_{\mu} + \bar{\nu}_{\mu})$")
    plt.ylabel(r"$E^{2}\frac{dN}{dE}$ [GeV cm$^{-2}$ s$^{-1}$ sr$^{-1}$]")
    plt.xlabel(r"$E_{\nu}$ [GeV]")
    plt.grid(True, linestyle=":")
    plt.tight_layout()
    plt.savefig(base_dir + "diffuse_flux_global_fit.pdf")
    plt.close()

