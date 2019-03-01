import numpy as np
import resource
import random
from sys import stdout
import os, os.path
import argparse
import cPickle as Pickle
import scipy.optimize
from flarestack.core.injector import Injector, LowMemoryInjector, SparseMatrixInjector
from flarestack.core.llh import LLH, generate_dynamic_flare_class
from flarestack.shared import name_pickle_output_dir, fit_setup, \
    inj_dir_name, plot_output_dir, scale_shortener
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib as mpl
from flarestack.core.time_PDFs import TimePDF, Box, Steady
from flarestack.core.pull_corrector import BasePullCorrector


def time_smear(inj):
    inj_time = inj["Injection Time PDF"]
    max_length = inj_time["Max Offset"] - inj_time["Min Offset"]
    offset = np.random.random() * max_length + inj_time["Min Offset"]
    inj_time["Offset"] = offset
    return inj_time


class MinimisationHandler:
    """Generic Class to handle both dataset creation and llh minimisation from
    experimental data and Monte Carlo simulation. Initialised with a set of
    IceCube datasets, a list of sources, and independent sets of arguments for
    the injector and the likelihood.
    """
    subclasses = {}

    # Each MinimisationHandler must specify which LLH classes are compatible
    compatible_llh = []
    compatible_negative_n_s = False

    def __init__(self, mh_dict):

        sources = np.sort(np.load(mh_dict["catalogue"]), order="Distance (Mpc)")

        self.name = mh_dict["name"]
        self.pickle_output_dir = name_pickle_output_dir(self.name)
        self.injectors = dict()
        self.llhs = dict()
        self.seasons = mh_dict["datasets"]
        self.sources = sources
        self.mh_dict = mh_dict
        self.pull_correctors = dict()

        # Checks whether signal injection should be done with a sliding PDF
        # within a larger window, or remain fixed at the specified time

        inj = dict(mh_dict["inj kwargs"])

        try:
            self.time_smear = inj["Injection Time PDF"]["Time Smear?"]
        except KeyError:
            self.time_smear = False

        if self.time_smear:
            inj["Injection Time PDF"] = time_smear(inj)

        self.inj_kwargs = inj
        self.llh_dict = mh_dict["llh_dict"]

        # Check if the specified MinimisationHandler is compatible with the
        # chosen LLH class

        if self.llh_dict["name"] not in self.compatible_llh:
            raise ValueError("Specified LLH ({}) is not compatible with "
                             "selected MinimisationHandler".format(
                              self.llh_dict["name"]))
        else:
            print "Using", self.llh_dict["name"], "LLH class"

        # Checks if negative n_s is specified for use, and whether this is
        # compatible with the chosen MinimisationHandler

        try:
            self.negative_n_s = self.llh_dict["Fit Negative n_s?"]
        except KeyError:
            self.negative_n_s = False

        if self.negative_n_s and not self.compatible_negative_n_s:
            raise ValueError("MinimisationHandler has been instructed to \n"
                             "allow negative n_s, but this is not compatible \n"
                             "with the selected MinimisationHandler.")

        # Sets up whether what pull corrector should be used (default is
        # none), and whether an angular error floor should be applied (
        # default is a static floor.

        try:
            self.pull_name = self.llh_dict["pull_name"]
        except KeyError:
            self.pull_name = "no_pull"

        try:
            self.floor_name = self.llh_dict["floor_name"]
        except KeyError:
            self.floor_name = "static_floor"

        # For each season, we create an independent injector and llh using the
        # source list along with the respective sets of energy/time PDFs
        # provided.
        for season in self.seasons:
            self.llhs[season["Name"]] = self.add_likelihood(season)
            self.injectors[season["Name"]] = self.add_injector(season, sources)
            self.pull_correctors[season["Name"]] = BasePullCorrector.create(
                season, self.llh_dict["LLH Energy PDF"], self.floor_name,
                self.pull_name
            )

        p0, bounds, names = self.return_parameter_info(mh_dict)

        self.p0 = p0
        self.bounds = bounds
        self.param_names = names

    @classmethod
    def register_subclass(cls, mh_name):
        """Adds a new subclass of EnergyPDF, with class name equal to
        "energy_pdf_name".
        """
        def decorator(subclass):
            cls.subclasses[mh_name] = subclass
            return subclass

        return decorator

    @classmethod
    def create(cls, mh_dict):
        mh_name = mh_dict["mh_name"]

        if mh_name not in cls.subclasses:
            raise ValueError('Bad MinimisationHandler name {}'.format(mh_name))

        return cls.subclasses[mh_name](mh_dict)

    @classmethod
    def find_parameter_info(cls, mh_dict):
        mh_name = mh_dict["mh_name"]

        if mh_name not in cls.subclasses:
            raise ValueError('Bad MinimisationHandler name {}'.format(mh_name))

        return cls.subclasses[mh_name].return_parameter_info(mh_dict)

    def run_trial(self, scale):
        pass

    def run(self, n_trials, scale=1.):
        pass

    def iterate_run(self, scale=1, n_steps=5, n_trials=50):

        scale_range = np.linspace(0., scale, n_steps)[1:]

        self.run(n_trials*10, scale=0.0)

        for scale in scale_range:
            self.run(n_trials, scale)

    @staticmethod
    def return_parameter_info(mh_dict):
        seeds = []
        bounds = []
        names = []
        return seeds, names, bounds

    @staticmethod
    def return_injected_parameters(mh_dict):
        return {}

    def add_likelihood(self, season):
        return LLH.create(season, self.sources, self.llh_dict)

    def add_injector(self, season, sources):
        return Injector(season, sources, **self.inj_kwargs)

    @staticmethod
    def set_random_seed(seed):
        np.random.seed(seed)



@MinimisationHandler.register_subclass('fixed_weights')
class FixedWeightMinimisationHandler(MinimisationHandler):
    """Class to perform generic minimisations using a 'fixed weights' matrix.
    Sources are assigned intrinsic weights based on their assumed luminosity
    and/or distance, which are fixed. In addition, time weighting is used
    assuming a fixed fluence per source. The detector acceptance continues to
    vary as a function of the parameters given in minimisation step.
    """

    compatible_llh = ["spatial", "fixed_energy", "standard",
                      "standard_overlapping", "standard_matrix"]
    compatible_negative_n_s = True

    def __init__(self, mh_dict):

        MinimisationHandler.__init__(self, mh_dict)

        self.fit_weights = False

        # Checks if minimiser should be seeded from a brute scan

        try:
            self.brute = self.llh_dict["brute_seed"]
        except KeyError:
            self.brute = False

        # self.clean_true_param_values()

    def clear(self):

        self.injectors.clear()
        self.llhs.clear()

        del self

    def dump_results(self, results, scale, seed):
        """Takes the results of a set of trials, and saves the dictionary as
        a pickle pkl_file. The flux scale is used as a parent directory, and the
        pickle pkl_file itself is saved with a name equal to its random seed.

        :param results: Dictionary of Minimisation results from trials
        :param scale: Scale of inputted flux
        :param seed: Random seed used for running of trials
        """

        write_dir = self.pickle_output_dir + scale_shortener(scale) + "/"

        # Tries to create the parent directory, unless it already exists
        try:
            os.makedirs(write_dir)
        except OSError:
            pass

        file_name = write_dir + str(seed) + ".pkl"

        print "Saving to", file_name

        with open(file_name, "wb") as f:
            Pickle.dump(results, f)

    def dump_injection_values(self, scale):


        inj_dict = self.return_injected_parameters(scale)

        inj_dir = inj_dir_name(self.name)

        # Tries to create the parent directory, unless it already exists
        try:
            os.makedirs(inj_dir)
        except OSError:
            pass

        file_name = inj_dir + scale_shortener(scale) + ".pkl"
        with open(file_name, "wb") as f:
            Pickle.dump(inj_dict, f)

    def run_trial(self, scale):

        raw_f = self.trial_function(scale)

        def llh_f(scale):
            return -np.sum(raw_f(scale))

        if self.brute:

            brute_range = [
                (max(x, -30), min(y, 30)) for (x, y) in self.bounds]

            start_seed = scipy.optimize.brute(
                llh_f, ranges=brute_range, finish=None, Ns=40)
        else:
            start_seed = self.p0

        res = scipy.optimize.minimize(
            llh_f, start_seed, bounds=self.bounds)

        vals = res.x
        flag = res.status
        # If the minimiser does not converge, repeat with brute force
        if flag == 1:
            vals = scipy.optimize.brute(llh_f, ranges=self.bounds,
                                        finish=None)

        best_llh = raw_f(vals)

        if not (res.x[0] > 0.0) and self.negative_n_s:

            bounds = list(self.bounds)
            bounds[0] = (-1000., -0.)
            start_seed = list(self.p0)
            start_seed[0] = -1.

            new_res = scipy.optimize.minimize(
                llh_f, start_seed, bounds=bounds)

            if new_res.status == 0:
                res = new_res

            vals = [res.x[0]]
            best_llh = res.fun

        ts = np.sum(best_llh)

        if ts == -0.0:
            ts = 0.0

        parameters = dict()

        for i, val in enumerate(vals):
            parameters[self.param_names[i]] = val

        res_dict = {
            "res": res,
            "Parameters": parameters,
            "TS": ts,
            "Flag": flag,
            "f": llh_f
        }

        return res_dict

    def run(self, n_trials, scale=1.):

        seed = int(random.random() * 10 ** 8)
        np.random.seed(seed)

        # param_vals = [[] for x in self.p0]
        param_vals = {}
        for key in self.param_names:
            param_vals[key] = []
        ts_vals = []
        flags = []

        print "Generating", n_trials, "trials!"

        for i in range(int(n_trials)):

            res_dict = self.run_trial(scale)

            for (key, val) in res_dict["Parameters"].iteritems():
                param_vals[key].append(val)

            ts_vals.append(res_dict["TS"])
            flags.append(res_dict["Flag"])

        mem_use = str(
            float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1.e6)
        print ""
        print 'Memory usage max: %s (Gb)' % mem_use

        n_inj = 0
        for inj in self.injectors.itervalues():
            for val in inj.ref_fluxes[scale_shortener(scale)].itervalues():
                n_inj += val
        print ""
        print "Injected with an expectation of", n_inj, "events."

        print ""
        print "FIT RESULTS:"
        print ""

        for (key, param) in sorted(param_vals.iteritems()):
            if len(param) > 0:
                print "Parameter", key, ":", np.mean(param), \
                    np.median(param), np.std(param)
        print "Test Statistic:", np.mean(ts_vals), np.median(ts_vals), np.std(
            ts_vals)
        print ""

        print "FLAG STATISTICS:"
        for i in sorted(np.unique(flags)):
            print "Flag", i, ":", flags.count(i)

        results = {
            "TS": ts_vals,
            "Parameters": param_vals,
            "Flags": flags,
        }

        self.dump_results(results, scale, seed)

        self.dump_injection_values(scale)

    def make_season_weight(self, params, season):

        src = np.sort(self.sources, order="Distance (Mpc)")
        dist_weight = src["Distance (Mpc)"] ** -2

        llh = self.llhs[season["Name"]]
        acc = []

        time_weights = []

        for source in src:
            time_weights.append(llh.time_pdf.effective_injection_time(
                source))
            acc.append(llh.acceptance(source, params))

        time_weights = np.array(time_weights)

        acc = np.array(acc).T[0]

        w = acc * dist_weight * time_weights

        w = w[:, np.newaxis]

        return w

    def make_weight_matrix(self, params):

        # Creates a matrix fixing the fraction of the total signal that
        # is expected in each Source+Season pair. The matrix is
        # normalised to 1, so that for a given total n_s, the expectation
        # for the ith season for the jth source is given by:
        #  n_exp = n_s * weight_matrix[i][j]

        weights_matrix = np.ones([len(self.seasons), len(self.sources)])

        for i, season in enumerate(self.seasons):
            w = self.make_season_weight(params, season)

            for j, ind_w in enumerate(w):
                weights_matrix[i][j] = ind_w

        return weights_matrix

    def trial_function(self, scale=1.):

        llh_functions = dict()
        n_all = dict()

        for season in self.seasons:
            dataset = self.injectors[season["Name"]].create_dataset(
                scale, self.pull_correctors[season["Name"]])
            llh_f = self.llhs[season["Name"]].create_llh_function(
                dataset, self.pull_correctors[season["Name"]],
                self.make_season_weight
            )
            llh_functions[season["Name"]] = llh_f
            n_all[season["Name"]] = len(dataset)

        def f_final(raw_params):

            # If n_s is less than or equal to 0, set gamma to be 3.7 (equal to
            # atmospheric background). This is continuous at n_s=0, but fixes
            # relative weights of sources/seasons for negative n_s values.

            params = list(raw_params)

            if (len(params) > 1) and (params[0] < 0):
                params[1] = 3.7

            # Calculate relative contribution of each source/season

            weights_matrix = self.make_weight_matrix(params)
            weights_matrix /= np.sum(weights_matrix)

            # Having created the weight matrix, loops over each season of
            # data and evaluates the TS function for that season

            ts_val = 0
            for i, season in enumerate(self.seasons):
                w = weights_matrix[i][:, np.newaxis]
                ts_val += np.sum(llh_functions[season["Name"]](params, w))

            return ts_val

        return f_final

    def scan_likelihood(self, scale=1.):
        """Generic wrapper to perform a likelihood scan a background scramble
        with an injection of signal given by scale.

        :param scale: Flux scale to inject
        """

        res_dict = self.run_trial(scale)

        res = res_dict["res"]
        g = res_dict["f"]

        bounds = list(self.bounds)
        if self.negative_n_s:
            bounds[0] = (-30, 30)

        # Scan 1D Likelihood

        plt.figure(figsize=(8, 4 + 2*len(self.p0)))

        u_ranges = []

        for i, bound in enumerate(bounds):
            ax = plt.subplot(len(self.p0), 1, 1 + i)

            best = list(res.x)
            min_llh = np.sum(float(g(best)))

            factor = 0.9
            best[i] = bound[1]

            while (g(best) > (min_llh + 5.0)):
                best[i] *= factor

            ur = min(bound[1], max(best[i], 0))

            u_ranges.append(ur)

            n_range = np.linspace(max(bound[0], -100), ur, 1e2)

            # n_range = np.linspace(-30, 30, 1e2)
            y = []

            for n in n_range:

                best[i] = n

                new = g(best)/2.0
                try:
                    y.append(new[0][0])
                except IndexError:
                    y.append(new)

            plt.plot(n_range, y - min(y))
            plt.xlabel(self.param_names[i])
            plt.ylabel(r"$\Delta \log(\mathcal{L}/\mathcal{L}_{0})$")

            print "PARAM:", self.param_names[i]
            min_y = np.min(y)
            print "Minimum value of", min_y,

            min_index = y.index(min_y)
            min_n = n_range[min_index]
            print "at", min_n

            print "One Sigma interval between",

            l_y = np.array(y[:min_index])
            try:
                l_y = min(l_y[l_y > (min_y + 0.5)])
                l_lim = n_range[y.index(l_y)]
                print l_lim,
            except ValueError:
                l_lim = min(n_range)
                print "<"+str(l_lim),

            print "and"

            u_y = np.array(y[min_index:])
            try:
                u_y = min(u_y[u_y > (min_y + 0.5)])
                u_lim = n_range[y.index(u_y)]
                print u_lim
            except ValueError:
                u_lim = max(n_range)
                print ">" + str(u_lim)

            ax.axvspan(l_lim, u_lim, facecolor="grey",
                        alpha=0.2)
            ax.set_ylim(bottom=0.0)

        path = plot_output_dir(self.name) + "llh_scan.pdf"

        title = os.path.basename(
                    os.path.dirname(self.name[:-1])
                ).replace("_", " ") + " Likelihood Scans"

        plt.suptitle(title)

        try:
            os.makedirs(os.path.dirname(path))
        except OSError:
            pass

        plt.savefig(path)
        plt.close()

        print "Saved to", path

        # Scan 2D likelihood

        if "Gamma" in self.param_names:

            gamma_index = self.param_names.index("Gamma")

            gamma_bounds = bounds[gamma_index]

            x = np.linspace(gamma_bounds[0], gamma_bounds[1])

            mask = np.array(["n_s" in b for b in self.param_names])

            n_s_bounds = np.array(self.bounds)[mask]

            for j, bound in enumerate(n_s_bounds):
                best = list(res.x)
                plt.figure()
                ax = plt.subplot(111)

                index = np.arange(len(self.param_names))[mask][j]

                plt.xlabel(r"Spectral Index ($\gamma$)")

                param_name = np.array(self.param_names)[mask][j]

                plt.ylabel(param_name)

                y = np.linspace(max(bound[0], -100),
                                np.array(u_ranges)[index], 1e2)

                X, Y = np.meshgrid(x, y[::-1])
                Z = []

                for gamma in x:
                    best[gamma_index] = gamma
                    z_row = []

                    for n in y:
                        best[index] = n
                        z_row.append((g(best) - g(res.x))/2.0)

                    Z.append(z_row[::-1])

                Z = np.array(Z).T

                levels = 0.5 * np.array([1.0, 2.0, 5.0])**2

                plt.imshow(Z, aspect="auto", cmap="jet_r",
                           extent=(x[0], x[-1], y[0], y[-1]),
                           interpolation='bilinear')
                cbar = plt.colorbar()
                CS = ax.contour(X, Y, Z, levels=levels, colors="white")

                fmt = {}
                strs = [r'1$\sigma$', r'2$\sigma$', r'5$\sigma$']
                for l, s in zip(CS.levels, strs):
                    fmt[l] = s

                ax.clabel(CS, fmt=fmt, inline=1, fontsize=10, levels=levels,
                          color="white")
                cbar.set_label(r"$\Delta \log(\mathcal{L}/\mathcal{L}_{0})$",
                               rotation=90)

                path = plot_output_dir(self.name) + (param_name + "_")[4:] + \
                       "contour_scan.pdf"

                title = os.path.basename(
                    os.path.dirname(self.name[:-1])
                ).replace("_", " ") + " Contour Scans"

                plt.scatter(res.x[gamma_index], res.x[index],  color="white",
                            marker="*")

                plt.grid(color="white", linestyle="--", alpha=0.5)

                plt.suptitle(title)

                plt.savefig(path)
                plt.close()

                print "Saved to", path

        return res_dict

    def neutrino_lightcurve(self):

        for source in self.sources:

            f, (ax0, ax1) = plt.subplots(1, 2,
                                       gridspec_kw={'width_ratios': [19, 1]})

            logE = []
            time = []
            sig = []

            for season in self.seasons:

                time_key = season["MJD Time Key"]

                # Generate a scrambled dataset, and save it to the datasets
                # dictionary. Loads the llh for the season.

                data = self.injectors[season["Name"]].create_dataset(scale=0)
                llh = self.llhs[season["Name"]]

                mask = llh.select_spatially_coincident_data(data, [source])
                spatial_coincident_data = data[mask]

                t_mask = np.logical_and(
                    np.greater(
                        spatial_coincident_data[time_key],
                        llh.time_pdf.sig_t0(source)),
                    np.less(
                        spatial_coincident_data[time_key],
                        llh.time_pdf.sig_t1(source))
                )

                coincident_data = spatial_coincident_data[t_mask]

                SoB = llh.estimate_significance(coincident_data, source)

                mask = SoB > 1.

                y = np.log10(SoB[mask])

                if np.sum(mask) > 0:

                    logE += list(10 ** (coincident_data["logE"][mask] - 3))
                    time += list(coincident_data[time_key][mask])
                    sig += list(y)

                ax0.axvline(max(llh.time_pdf.sig_t0(source), llh.time_pdf.t0),
                            color="k", linestyle="--", alpha=0.5)
                ax0.axvline(min(llh.time_pdf.sig_t1(source), llh.time_pdf.t1),
                            color="k", linestyle="--", alpha=0.5)

            cmap = cm.get_cmap('jet')
            norm = mpl.colors.Normalize(vmin=min(logE), vmax=max(logE),
                                        clip=True)
            m = cm.ScalarMappable(norm=norm, cmap=cmap)

            for i, val in enumerate(sig):
                x = time[i]
                ax0.plot([x, x], [0, val], color=m.to_rgba(logE[i]))

            if hasattr(self, "res_dict"):
                params = self.res_dict[source["Name"]]["Parameters"]
                if len(params) > 1:
                    ax0.axvspan(params[2], params[3], facecolor="grey",
                                      alpha=0.2)
            ax0.set_xlabel("Arrival Time (MJD)")
            ax0.set_ylabel("Log(Signal/Background)")

            cb1 = mpl.colorbar.ColorbarBase(ax1, cmap=cmap,
                                            norm=norm,
                                            orientation='vertical')
            ax1.set_ylabel("Muon Energy Proxy (TeV)")

            ax0.set_ylim(bottom=0)
            plt.suptitle(source["Name"])
            # plt.tight_layout()

            path = plot_output_dir(self.name) + source["Name"] + \
                   "_neutrino_lightcurve.pdf"

            try:
                os.makedirs(os.path.dirname(path))
            except OSError:
                pass

            plt.savefig(path)
            plt.close()

    @staticmethod
    def return_parameter_info(mh_dict):
        params = [[1.], [(0, 1000.)], ["n_s"]]

        params = [
            params[i] + x for i, x in enumerate(
                LLH.get_parameters(mh_dict["llh_dict"])
            )
        ]

        return params[0], params[1], params[2]

    def return_injected_parameters(self, scale):

        n_inj = 0
        for source in self.sources:
            name = source["Name"]

            for inj in self.injectors.itervalues():
                try:
                    n_inj += inj.ref_fluxes[scale_shortener(scale)][name]

                # If source not overlapping season, will not be in dict
                except KeyError:
                    pass

        inj_params = {
            "n_s": n_inj
        }
        inj_params.update(LLH.get_injected_parameters(self.mh_dict))

        return inj_params


@MinimisationHandler.register_subclass('large_catalogue')
class LargeCatalogueMinimisationHandler(FixedWeightMinimisationHandler):
    """Class to perform generic minimisations using a 'fixed weights' matrix.
    However, unlike the 'fixed_weight' class, it is optimised for large
    numbers of sources. It uses a custom 'LowMemoryInjector' which is slower
    but much less burdensome for memory.
    """

    def add_injector(self, season, sources):
        return LowMemoryInjector(season, sources, **self.inj_kwargs)

@MinimisationHandler.register_subclass('sparse_matrix')
class SparceMatrixMinimisationHandler(FixedWeightMinimisationHandler):
    """Class to perform generic minimisations using a 'fixed weights' matrix.
    However, unlike the 'fixed_weight' class, it is optimised for large
    numbers of sources. It uses a custom 'LowMemoryInjector' which is slower
    but much less burdensome for memory.
    """

    def add_injector(self, season, sources):
        return SparseMatrixInjector(season, sources, **self.inj_kwargs)


@MinimisationHandler.register_subclass('fit_weights')
class FitWeightMinimisationHandler(FixedWeightMinimisationHandler):
    compatible_llh = ["spatial", "fixed_energy", "standard"]
    compatible_negative_n_s = False

    def __init__(self, mh_dict):
        FixedWeightMinimisationHandler.__init__(self, mh_dict)

        if self.negative_n_s:
            raise ValueError(
                "Attempted to mix fitting weights with negative n_s.")

    def trial_function(self, scale=1.):
        llh_functions = dict()
        n_all = dict()

        for season in self.seasons:
            dataset = self.injectors[season["Name"]].create_dataset(
                scale, self.pull_correctors[season["Name"]])
            llh_f = self.llhs[season["Name"]].create_llh_function(
                dataset, self.pull_correctors[season["Name"]])
            llh_functions[season["Name"]] = llh_f
            n_all[season["Name"]] = len(dataset)

        def f_final(params):

            # Creates a matrix fixing the fraction of the total signal that
            # is expected in each Source+Season pair. The matrix is
            # normalised to 1, so that for a given total n_s, the expectation
            # for the ith season for the jth source is given by:
            #  n_exp = n_s * weight_matrix[i][j]

            weights_matrix = self.make_weight_matrix(params)

            for i, row in enumerate(weights_matrix.T):
                if np.sum(row) > 0:
                    row /= np.sum(row)

            # Having created the weight matrix, loops over each season of
            # data and evaluates the TS function for that season

            ts_val = 0
            for i, season in enumerate(self.seasons):
                w = weights_matrix[i][:, np.newaxis]
                ts_val += llh_functions[season["Name"]](params, w)

            return ts_val

        return f_final

    @staticmethod
    def source_param_name(source):
        return "n_s (" + source["Name"] + ")"

    @staticmethod
    def return_parameter_info(mh_dict):
        sources = np.load(mh_dict["catalogue"])
        p0 = [1. for _ in sources]
        bounds = [(0., 1000.) for _ in sources]
        names = [FitWeightMinimisationHandler.source_param_name(x)
                 for x in sources]
        params = [p0, bounds, names]

        params = [
            params[i] + x for i, x in enumerate(
                LLH.get_parameters(mh_dict["llh_dict"])
            )
        ]

        return params[0], params[1], params[2]

    def return_injected_parameters(self, scale):

        inj_params = {}

        for source in self.sources:
            name = source["Name"]
            key = self.source_param_name(source)
            n_inj = 0
            for inj in self.injectors.itervalues():
                try:
                    n_inj += inj.ref_fluxes[scale_shortener(scale)][name]

                # If source not overlapping season, will not be in dict
                except KeyError:
                    pass

            inj_params[key] = n_inj

        inj_params.update(LLH.get_injected_parameters(self.mh_dict))

        return inj_params


@MinimisationHandler.register_subclass("flare")
class FlareMinimisationHandler(FixedWeightMinimisationHandler):

    compatible_llh = ["spatial", "fixed_energy", "standard"]
    compatible_negative_n_s = False

    def __init__(self, mh_dict):
        MinimisationHandler.__init__(self, mh_dict)
        # For each season, we create an independent likelihood, using the
        # source list along with the sets of energy/time
        # PDFs provided in llh_kwargs.
        for season in self.seasons:

            tpdf = self.llhs[season["Name"]].time_pdf

            # Check to ensure that no weird new untested time PDF is used
            # with the flare search method, since uniform time PDFs over the
            # duration of a given flare is an assumption baked into the PDF
            # construction. New time PDFs could be added, but the Flare class
            #  + LLH would need to be tested first and probably modified.

            if np.sum([isinstance(tpdf, x) for x in [Box, Steady]]) == 0:
                raise ValueError("Attempting to use a time PDF that is not a "
                                 "Box or a Steady time PDF class. The flare "
                                 "search method is only compatible with "
                                 "time PDFs that are uniform over "
                                 "fixed periods.")

    def run_trial(self, scale):

        time_key = self.seasons[0]["MJD Time Key"]

        datasets = dict()

        full_data = dict()

        livetime_calcs = dict()

        time_dict = {
            "Name": "FixedEndBox"
        }

        results = {
            "Parameters": dict(),
            "Flag": []
        }

        # Loop over each data season

        for season in self.seasons:

            # Generate a scrambled dataset, and save it to the datasets
            # dictionary. Loads the llh for the season.

            data = self.injectors[season["Name"]].create_dataset(scale)
            llh = self.llhs[season["Name"]]

            livetime_calcs[season["Name"]] = TimePDF.create(time_dict, season)

            full_data[season["Name"]] = data

            # Loops over each source in catalogue

            for source in self.sources:

                # Identify spatially- and temporally-coincident data

                mask = llh.select_spatially_coincident_data(data, [source])
                spatial_coincident_data = data[mask]

                t_mask = np.logical_and(
                    np.greater(
                        spatial_coincident_data[time_key],
                        llh.time_pdf.sig_t0(source)),
                    np.less(
                        spatial_coincident_data[time_key],
                        llh.time_pdf.sig_t1(source))
                )

                coincident_data = spatial_coincident_data[t_mask]

                # If there are events in the window...

                if len(coincident_data) > 0:

                    # Creates empty dictionary to save info

                    name = source["Name"]
                    if name not in datasets.keys():
                        datasets[name] = dict()

                    new_entry = dict(season)
                    new_entry["Coincident Data"] = coincident_data
                    new_entry["Start (MJD)"] = llh.time_pdf.t0
                    new_entry["End (MJD)"] = llh.time_pdf.t1

                    # Identify significant events (S/B > 1)

                    significant = llh.find_significant_events(
                        coincident_data, source)

                    new_entry["Significant Times"] = significant[time_key]

                    new_entry["N_all"] = len(data)

                    datasets[name][season["Name"]] = new_entry

        stacked_ts = 0.0

        # Minimisation of each source

        for (source, source_dict) in datasets.iteritems():

            src = self.sources[self.sources["Name"] == source][0]
            p0, bounds, names = self.source_fit_parameter_info(self.mh_dict,
                                                               src)

            # Create a full list of all significant times

            all_times = []
            n_tot = 0
            for season_dict in source_dict.itervalues():
                new_times = season_dict["Significant Times"]
                all_times.extend(new_times)
                n_tot += len(season_dict["Coincident Data"])

            all_times = np.array(sorted(all_times))

            # Minimum flare duration (days)
            min_flare = 0.25
            # Conversion to seconds
            min_flare *= 60 * 60 * 24

            # Length of search window in livetime

            search_window = np.sum([
                llh.time_pdf.effective_injection_time(src)
                for llh in self.llhs.itervalues()]
            )

            # If a maximum flare length is specified, sets that here

            if "Max Flare" in self.llh_dict["LLH Time PDF"].keys():
                # Maximum flare given in days, here converted to seconds
                max_flare = self.llh_dict["LLH Time PDF"]["Max Flare"] * (
                        60 * 60 * 24
                )
            else:
                max_flare = search_window

            # Loop over all flares, and check which combinations have a
            # flare length between the maximum and minimum values

            pairs = []

            # print "There are", len(all_times), "significant neutrinos",
            # print "out of", n_tot, "neutrinos"

            for x in all_times:
                for y in all_times:
                    if y > x:
                        pairs.append((x, y))

            # If there is are no pairs meeting this criteria, skip

            if len(pairs) == 0:
                print "Continuing because no pairs"
                continue

            all_res = []
            all_ts = []
            all_f = []
            all_pairs = []

            # Loop over each possible significant neutrino pair

            for i, pair in enumerate(pairs):
                t_start = pair[0]
                t_end = pair[1]

                # Calculate the length of the neutrino flare in livetime

                flare_time = np.array(
                    (t_start, t_end),
                    dtype=[
                        ("Start Time (MJD)", np.float),
                        ("End Time (MJD)", np.float),
                    ]
                )

                flare_length = np.sum([
                    time_pdf.effective_injection_time(flare_time)
                    for time_pdf in livetime_calcs.itervalues()]
                )

                # If the flare is between the minimum and maximum length

                if flare_length < min_flare:
                    # print "Continuing because flare too short"
                    continue
                elif flare_length > max_flare:
                    # print "Continuing because flare too long"
                    continue

                stdout.write("\r" + str(i) + " of " + str(len(pairs)))
                stdout.flush()

                # Marginalisation term is length of flare in livetime
                # divided by max flare length in livetime. Accounts
                # for the additional short flares that can be fitted
                # into a given window

                overall_marginalisation = flare_length / max_flare

                # Each flare is evaluated accounting for the
                # background on the sky (the non-coincident
                # data), which is given by the number of
                # neutrinos on the sky during the given
                # flare. (NOTE THAT IT IS NOT EQUAL TO THE
                # NUMBER OF NEUTRINOS IN THE SKY OVER THE
                # ENTIRE SEARCH WINDOW)

                n_all = np.sum([np.sum(~np.logical_or(
                    np.less(data[time_key], t_start),
                    np.greater(data[time_key], t_end)))
                                for data in full_data.itervalues()])

                llhs = dict()

                # Loop over data seasons

                for (name, season_dict) in sorted(source_dict.iteritems()):

                    llh = self.llhs[season_dict["Name"]]

                    # Check that flare overlaps with season

                    inj_time = llh.time_pdf.effective_injection_time(
                        flare_time
                    )

                    if not inj_time > 0:
                        # print "Continuing because no overlap"
                        continue

                    coincident_data = season_dict["Coincident Data"]

                    data = full_data[name]

                    n_season = np.sum(~np.logical_or(
                        np.less(data[time_key], t_start),
                        np.greater(data[time_key], t_end)))

                    # Removes non-coincident data

                    flare_veto = np.logical_or(
                        np.less(coincident_data[time_key], t_start),
                        np.greater(coincident_data[time_key], t_end)
                    )

                    # Checks to make sure that there are
                    # neutrinos in the sky at all. There should
                    # be, due to the definition of the flare window.

                    if n_all > 0:
                        pass
                    else:
                        raise Exception("Events are leaking somehow!")

                    # Creates the likelihood function for the flare

                    flare_f = llh.create_flare_llh_function(
                        coincident_data, flare_veto, n_all, src, n_season)

                    llhs[season_dict["Name"]] = {
                        "f": flare_f,
                        "flare length": flare_length
                    }

                # From here, we have normal minimisation behaviour

                def f_final(params):

                    # Marginalisation is done once, not per-season

                    ts = 2 * np.log(overall_marginalisation)

                    for llh_dict in llhs.itervalues():
                        ts += llh_dict["f"](params)

                    return -ts

                res = scipy.optimize.fmin_l_bfgs_b(
                    f_final, p0, bounds=bounds,
                    approx_grad=True)

                all_res.append(res)
                all_ts.append(-res[1])
                all_f.append(f_final)
                all_pairs.append(pair)

            max_ts = max(all_ts)
            stacked_ts += max_ts
            index = all_ts.index(max_ts)

            best_start = all_pairs[index][0]
            best_end = all_pairs[index][1]

            best_time = np.array(
                (best_start, best_end),
                dtype=[
                    ("Start Time (MJD)", np.float),
                    ("End Time (MJD)", np.float),
                ]
            )

            best_length = np.sum([
                time_pdf.effective_injection_time(best_time)
                for time_pdf in livetime_calcs.itervalues()]
            ) / (60 * 60 * 24)

            best = [x for x in all_res[index][0]] + [
                best_start, best_end, best_length
            ]

            p0, bounds, names = self.source_parameter_info(self.mh_dict, src)

            names += [self.source_param_name(x, src)
                      for x in ["t_start", "t_end", "length"]]

            for i, x in enumerate(best):
                key = names[i]
                results["Parameters"][key] = x

            results["Flag"] += [all_res[index][2]["warnflag"]]

            del all_res, all_f, all_times

        results["TS"] = stacked_ts

        del datasets, full_data, livetime_calcs

        return results

    # def dump_injection_values(self, scale):

        # inj_dict = dict()
        # for source in self.sources:
        #     name = source["Name"]
        #     n_inj = 0
        #     for inj in self.injectors.itervalues():
        #         try:
        #             n_inj += inj.ref_fluxes[scale_shortener(scale)][name]
        #
        #         # If source not overlapping season, will not be in dict
        #         except KeyError:
        #             pass
        #
        #     default = {
        #         "n_s": n_inj
        #     }
        #
        #     if "Gamma" in self.param_names:
        #         try:
        #             default["Gamma"] = self.inj_kwargs["Injection Energy PDF"][
        #                 "Gamma"]
        #         except KeyError:
        #             default["Gamma"] = np.nan

            # if self.flare:
            #     fs = [inj.time_pdf.sig_t0(source)
            #           for inj in self.injectors.itervalues()]
            #     true_fs = min(fs)
            #     fe = [inj.time_pdf.sig_t1(source)
            #           for inj in self.injectors.itervalues()]
            #     true_fe = max(fe)
            #
            #     if self.time_smear:
            #         inj_time = self.inj_kwargs["Injection Time PDF"]
            #         offset = inj_time["Offset"]
            #         true_fs -= offset
            #         true_fe -= offset
            #
            #         min_offset = inj_time["Min Offset"]
            #         max_offset = inj_time["Max Offset"]
            #         med_offset = 0.5*(max_offset + min_offset)
            #
            #         true_fs += med_offset
            #         true_fe += med_offset
            #
            #     true_l = true_fe - true_fs
            #
            #     sim = [
            #         list(np.random.uniform(true_fs, true_fe,
            #                           np.random.poisson(n_inj)))
            #         for _ in range(1000)
            #     ]
            #
            #     s = []
            #     e = []
            #     l = []
            #
            #     for data in sim:
            #         if data != []:
            #             s.append(min(data))
            #             e.append(max(data))
            #             l.append(max(data) - min(data))
            #
            #     if len(s) > 0:
            #         med_s = np.median(s)
            #         med_e = np.median(e)
            #         med_l = np.median(l)
            #     else:
            #         med_s = np.nan
            #         med_e = np.nan
            #         med_l = np.nan
            #
            #     print med_s, med_e, med_l
            #
            #     default["Flare Start"] = med_s
            #     default["Flare End"] = med_e
            #     default["Flare Length"] = med_l


    # def run(self, n_trials, scale=1.):
    #     """Runs iterations of a flare search, and dumps results as pickle files.
    #     For stacking of multiple sources, due to computational constraints,
    #     each flare is minimised entirely independently. The TS values from
    #     each flare is then summed to give an overall TS value. The results
    #     for each source are stored separately.
    #
    #     :param n_trials: Number of trials to perform
    #     :param scale: Flux scale
    #     """
    #
    #     # Selects the key corresponding to time for the given IceCube dataset
    #     # (enables use of different data samples)
    #
    #     seed = int(random.random() * 10 ** 8)
    #     np.random.seed(seed)
    #
    #     print "Running", n_trials, "trials"
    #
    #     # Initialises lists for all values that will need to be stored,
    #     # in order to verify that the minimisation is working successfuly
    #
    #     param_vals = {}
    #     for key in self.param_names:
    #         param_vals[key] = []
    #     ts_vals = []
    #     flags = []
    #
    #     print "Generating", n_trials, "trials!"
    #
    #     for i in range(int(n_trials)):
    #
    #         res_dict = self.run_trial(scale)
    #
    #         for (key, val) in res_dict["Parameters"].iteritems():
    #             param_vals[key].append(val)
    #
    #         ts_vals.append(res_dict["TS"])
    #         flags.append(res_dict["Flag"])
    #
    #     # results = {
    #     #     "TS": [],
    #     #     "Parameters": []
    #     # }
    #     #
    #     # # for source in self.sources:
    #     # #     results[source["Name"]] = {
    #     # #         "TS": [],
    #     # #         "Parameters": []
    #     # #     }
    #     #
    #     # # Loop over trials
    #     #
    #     # for _ in range(int(n_trials)):
    #     #
    #     #     res_dict = self.run_trial(scale)
    #     #
    #     #     for j, val in enumerate(list(res_dict["Parameters"])):
    #     #         key = self.param_names[j]
    #     #         param_vals[key].append(val)
    #     #
    #     #     ts_vals.append(res_dict["TS"])
    #     #     flags.append(res_dict["Flag"])
    #     #
    #     #     results["TS"].append(res_dict["TS"])
    #
    #     mem_use = str(
    #         float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1.e6)
    #     print ""
    #     print 'Memory usage max: %s (Gb)' % mem_use
    #
    #     full_ts = results["TS"]
    #
    #     print "Combined Test Statistic:"
    #     print np.mean(full_ts), np.median(full_ts), np.std(
    #           full_ts)
    #
    #     for source in self.sources:
    #         print "Results for", source["Name"]
    #
    #         combined_res = results[source["Name"]]
    #
    #         full_ts = combined_res["TS"]
    #
    #         full_params = np.array(combined_res["Parameters"])
    #
    #         for i, column in enumerate(full_params):
    #             print self.param_names[i], ":", np.mean(column),\
    #                 np.median(column), np.std(column)
    #
    #         print "Test Statistic", np.mean(full_ts), np.median(full_ts), \
    #             np.std(full_ts), "\n"
    #
    #     self.dump_results(results, scale, seed)
    #
    #     self.dump_injection_values(scale)

    def check_flare_background_rate(self):

        results = [[] for x in self.seasons]
        total = [[] for x in self.seasons]

        for i in range(int(1000)):

            # Loop over each data season

            for j, season in enumerate(sorted(self.seasons)):

                # Generate a scrambled dataset, and save it to the datasets
                # dictionary. Loads the llh for the season.

                data = self.injectors[season["Name"]].create_dataset(0.0)
                llh = self.llhs[season["Name"]]

                # Loops over each source in catalogue

                for source in sorted(self.sources, order="Distance (Mpc)"):

                    # Identify spatially- and temporally-coincident data

                    mask = llh.select_spatially_coincident_data(data, [source])
                    spatial_coincident_data = data[mask]



                    t_mask = np.logical_and(
                        np.greater(spatial_coincident_data["timeMJD"],
                                   llh.time_pdf.sig_t0(source)),
                        np.less(spatial_coincident_data["timeMJD"],
                                llh.time_pdf.sig_t1(source))
                    )

                    coincident_data = spatial_coincident_data[t_mask]
                    total[j].append(len(coincident_data))
                    # If there are events in the window...

                    if len(coincident_data) > 0:

                        # Identify significant events (S/B > 1)

                        significant = llh.find_significant_events(
                            coincident_data, source)

                        results[j].append(len(significant))
                    else:
                        results[j].append(0)

        for j, season in enumerate(sorted(self.seasons)):
            res = results[j]
            tot = total[j]

            print season["Name"],"Significant events", np.mean(res), \
                np.median(res), np.std(res)
            print season["Name"], "All events", np.mean(tot), np.median(tot), \
                np.std(tot)

            llh = self.llhs[season["Name"]]

            for source in self.sources:

                print "Livetime", llh.time_pdf.effective_injection_time(source)

    @staticmethod
    def source_param_name(param, source):
        return param + " (" + str(source["Name"]) + ")"


    @staticmethod
    def source_fit_parameter_info(mh_dict, source):

        p0 = [1.]
        bounds = [(0., 1000.)]
        names = [FlareMinimisationHandler.source_param_name("n_s", source)]

        llh_p0, llh_bounds, llh_names = LLH.get_parameters(
            mh_dict["llh_dict"])

        p0 += llh_p0
        bounds += llh_bounds
        names += [FlareMinimisationHandler.source_param_name(x, source)
                  for x in llh_names]

        return p0, bounds, names

    @staticmethod
    def source_parameter_info(mh_dict, source):

        p0, bounds, names = \
            FlareMinimisationHandler.source_fit_parameter_info(
                mh_dict, source
            )

        p0 += [np.nan for _ in range(3)]
        bounds += [(np.nan, np.nan)for _ in range(3)]
        names += [FlareMinimisationHandler.source_param_name(x, source)
                  for x in ["t_start", "t_end", "length"]]

        return p0, bounds, names

    @staticmethod
    def return_parameter_info(mh_dict):
        p0, bounds, names = [], [], []
        sources = np.load(mh_dict["catalogue"])
        for source in sources:
            res = FlareMinimisationHandler.source_parameter_info(
                mh_dict, source
            )

            for i, x in enumerate(res):
                [p0, bounds, names][i] += x

        return p0, bounds, names

    def return_injected_parameters(self, scale):

        inj_params = {}

        for source in self.sources:
            name = source["Name"]
            key = self.source_param_name("n_s", source)
            n_inj = 0
            for inj in self.injectors.itervalues():
                try:
                    n_inj += inj.ref_fluxes[scale_shortener(scale)][name]

                # If source not overlapping season, will not be in dict
                except KeyError:
                    pass

            inj_params[key] = n_inj

            ts = min([inj.time_pdf.sig_t0(source)
                      for inj in self.injectors.itervalues()])
            te = max([inj.time_pdf.sig_t1(source)
                      for inj in self.injectors.itervalues()])

            inj_params[self.source_param_name("length", source)] = te - ts

            if self.time_smear:
                inj_params[self.source_param_name("t_start", source)] = np.nan
                inj_params[self.source_param_name("t_end", source)] = np.nan
            else:
                inj_params[[self.source_param_name("t_start", source)]] = ts
                inj_params[[self.source_param_name("t_end", source)]] = te

            for (key, val) in LLH.get_injected_parameters(
                    self.mh_dict).iteritems():
                inj_params[self.source_param_name(key, source)] = val

        return inj_params

    def add_likelihood(self, season):
        return generate_dynamic_flare_class(season, self.sources, self.llh_dict)


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--file", help="Path for analysis pkl_file")
    cfg = parser.parse_args()

    with open(cfg.file) as f:
        mh_dict = Pickle.load(f)

    mh = MinimisationHandler.create(mh_dict)
    
    if "fixed_scale" in mh_dict.keys():
        fixed_scale = mh_dict["fixed_scale"]
        print "Only scanning at scale", fixed_scale
        mh.run(n_trials=mh_dict["n_trials"], scale=fixed_scale)
    else:
        mh.iterate_run(mh_dict["scale"], n_steps=mh_dict["n_steps"],
                       n_trials=mh_dict["n_trials"])
