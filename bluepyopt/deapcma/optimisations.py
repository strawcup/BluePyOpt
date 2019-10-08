"""Optimisation class"""

"""
Copyright (c) 2016, EPFL/Blue Brain Project

 This file is part of BluePyOpt <https://github.com/BlueBrain/BluePyOpt>

 This library is free software; you can redistribute it and/or modify it under
 the terms of the GNU Lesser General Public License version 3.0 as published
 by the Free Software Foundation.

 This library is distributed in the hope that it will be useful, but WITHOUT
 ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
 details.

 You should have received a copy of the GNU Lesser General Public License
 along with this library; if not, write to the Free Software Foundation, Inc.,
 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
"""

# pylint: disable=R0912, R0914

import bluepyopt.optimisations

import copyreg
import functools
import logging
import numpy
import pickle
import random
import types
from itertools import cycle

from deap import base
from deap import tools
from functools import partial

from .cma_es import cma_es

logger = logging.getLogger('__main__')


def _update_history_and_hof(halloffame, history, population):
    """
    Update the hall of fame with the generated individuals

    Note: History and Hall-of-Fame behave like dictionaries
    """
    if halloffame is not None:
        halloffame.update(population)

    history.update(population)


def _record_stats(stats, logbook, gen, population, evals, sigma):
    """Update the statistics with the new population"""
    record = stats.compile(population) if stats is not None else {}
    logbook.record(gen=gen, nevals=evals, sigma=sigma, **record)
    return record


class Fitness(base.Fitness):
    """Single objective fitness"""
    def __init__(self, values=()):
        self.weights = [-1.]
        super(Fitness, self).__init__(values)


class WSListIndividual(list):
    """Individual consisting of list with weighted sum field"""

    def __init__(self, *args, **kwargs):
        """Constructor"""
        self.fitness = Fitness()
        # all_values contains the list of individual objective values
        self.all_values = []
        super(WSListIndividual, self).__init__(*args, **kwargs)


class DEAPOptimisationCMA(bluepyopt.optimisations.Optimisation):
    """DEAP Optimisation class"""

    def __init__(self,
                 evaluator=None,
                 use_scoop=False,
                 swarm_size=1,
                 centroid=None,
                 sigma=0.4,
                 lr_scale=1.,
                 seed=1,
                 map_function=None,
                 hof=None,
                 **kargs):
        """Constructor

        Args:
            evaluator (Evaluator): Evaluator object
            swarm_size (int): Number of CMA-ES to run in parrallel
            centroid (list): list of initial guesses used as the starting 
            points of the CMA-ES
            sigma (float): initial standard deviation of the distribution
            lr_scale (float): scaling parameter for the learning rate of the CMA
            seed (float): Random number generator seed
            map_function (function): Function used to map (parallelise) the
                evaluation function calls
            hof (hof): Hall of Fame object
        """

        super(DEAPOptimisationCMA, self).__init__(evaluator=evaluator)

        self.problem_size = len(self.evaluator.params)

        self.use_scoop = use_scoop
        self.seed = seed
        self.map_function = map_function
        self.swarm_size = swarm_size
        self.lr_scale = lr_scale

        self.cma_params = kargs

        self.sigma = sigma
        logger.info("Global sigma set to: {}".format(self.sigma))

        self.hof = hof
        if self.hof is None:
            self.hof = tools.HallOfFame(10)

        # Create a DEAP toolbox
        self.toolbox = base.Toolbox()

        # Bounds of the parameters
        lbounds = numpy.array([p.lower_bound for p in self.evaluator.params])
        ubounds = numpy.array([p.upper_bound for p in self.evaluator.params])

        # Instantiate functions converting individuals from the original
        # parameter space to (and from) a normalized space bounded
        # to [-1.;1]
        self.to_norm = []
        self.to_space = []
        bounds_radius = (ubounds - lbounds) / 2.
        bounds_mean = (ubounds + lbounds) / 2.
        for r, m in zip(bounds_radius, bounds_mean):
            self.to_norm.append(
                partial(lambda x, bm, br: (x - bm) / br, bm=m, br=r))
            self.to_space.append(
                partial(lambda x, bm, br: (x * br) + bm, bm=m, br=r))
        
        if centroid is not None:
            self.centroid = [f(x) for f,x in zip(self.to_norm, centroid)]

        self.setup_deap()

    def setup_deap(self):
        """Set up optimisation"""

        # Set random seed
        random.seed(self.seed)
        numpy.random.seed(self.seed)

        # Register the evaluation function for the individuals
        self.toolbox.register("evaluate", self.evaluator.evaluate_with_lists)

        def _reduce_method(meth):
            """Overwrite reduce"""
            return getattr, (meth.__self__, meth.__func__.__name__)

        copyreg.pickle(types.MethodType, _reduce_method)

        if self.use_scoop:
            if self.map_function:
                raise Exception(
                    'Impossible to use scoop is providing self '
                    'defined map function: %s' %
                    self.map_function)

            from scoop import futures
            self.toolbox.register("map", futures.map)

        elif self.map_function:
            self.toolbox.register("map", self.map_function)

    def run(self,
            offspring_size=None,
            max_ngen=10,
            cp_frequency=1,
            continue_cp=False,
            cp_filename=None,
            stats=None):
        """ Implementation of a single objective population of CMA-ES
            (using the termination criteria presented in *Hansen, 2009,
            Benchmarking a BI-Population CMA-ES on the BBOB-2009
            Function Testbed*).

        Args:
            offspring_size(int): number of offspring in each CMA strategy
            max_ngen(int): Total number of generation to run
            cp_frequency(int): generations between checkpoints
            cp_filename(string): path to checkpoint filename
            continue_cp(bool): whether to continue
            stats(deap.tools.Statistics): generation of statistics
        """

        if stats is None:
            stats = tools.Statistics(key=lambda ind: ind.fitness.values)
            stats.register("avg", numpy.mean)
            stats.register("std", numpy.std)
            stats.register("min", numpy.min)
            stats.register("max", numpy.max)

        if continue_cp:
            # A file name has been given, then load the data from the file
            cp = pickle.load(open(cp_filename, "br"))
            t = cp["generation"]
            self.hof = cp["halloffame"]
            logbook = cp["logbook"]
            history = cp["history"]
            random.setstate(cp["rndstate"])
            numpy.random.set_state(cp["np_rndstate"])
            swarm = cp["swarm"]
            for c in swarm:
                c.active = True
                for k in c.conditions:
                    c.conditions[k] = False
                c.MAXITER = max_ngen+1
        
        else:
            history = tools.History()
            logbook = tools.Logbook()
            logbook.header = ["gen", "nevals"] + stats.fields + ["sigma"]

            if offspring_size is not None and offspring_size != 0:
                self.cma_params['lambda_'] = offspring_size

            logger.info("Offspring size per CMA strategy set to: {}".format(
                offspring_size))

            if self.centroid is None:
                # Generate a random centroid in the parameter space
                starters = cycle([WSListIndividual((numpy.random.rand(
                        self.problem_size) * 2.) - 1.) for c in self.swarm_size])
            else:
                starters = cycle(self.centroid)
            
            swarm = []
            for i in range(self.swarm_size):

                # Instantiate a CMA strategy centered on this centroid
                swarm.append(cma_es(centroid=next(starters),
                                    sigma=self.sigma,
                                    lr_scale=self.lr_scale,
                                    max_ngen=max_ngen+1,
                                    IndCreator=WSListIndividual,
                                    cma_params=self.cma_params))
            t = 1

        # Run until a termination criteria is met for every CMA strategy
        active_cma = numpy.sum([c.active for c in swarm])
        tot_pop = []
        while active_cma:

            logger.info("Generation {}".format(t))
            logger.info("Number of active CMA strategy: {} / {}".format(
                active_cma, len(swarm)))

            # Generate the new populations and contain it to bounds
            n_out = 0
            for c in swarm:
                n_out += c.generate_new_pop(lbounds=-1., ubounds=1.)
            logger.info("Number of individuals outside of bounds: {} ({:.2f}%)"
                        "".format(n_out, 100. * n_out / swarm[0].lambda_ /
                                  self.swarm_size))

            # Get all the individuals in the original space for evaluation
            to_evaluate = []
            for c in swarm:
                to_evaluate += c.get_population(self.to_space)
            fitnesses = self.toolbox.map(self.toolbox.evaluate, to_evaluate)
            fitnesses = list(map(list, fitnesses))
            nevals = len(to_evaluate)
            for c in swarm:
                if c.active:
                    c.set_fitness(fitnesses[:len(c.population)])
                    fitnesses = fitnesses[len(c.population):]

            # Update the hall of fame, history and logbook
            tot_pop = []
            for c in swarm:
                tot_pop += c.get_population(self.to_space)
            mean_sigma = numpy.mean([c.sigma for c in swarm])
            _update_history_and_hof(self.hof, history, tot_pop)
            _ = _record_stats(stats, logbook, t, tot_pop, nevals, mean_sigma)
            logger.info(logbook.stream)

            # Update the CMA strategy using the new fitness
            for c in swarm:
                c.update_strategy()

            t += 1

            # Check for each CMA if a termination criteria is reached
            
            for c in swarm:
                c.check_termination(t)
            active_cma = numpy.sum([c.active for c in swarm])

            if (cp_filename and cp_frequency and
                    t % cp_frequency == 0):
                cp = dict(population=tot_pop,
                          generation=t,
                          halloffame=self.hof,
                          history=history,
                          logbook=logbook,
                          rndstate=random.getstate(),
                          np_rndstate=numpy.random.get_state(),
                          swarm=swarm)
                pickle.dump(cp, open(cp_filename, "wb"))
                logger.debug('Wrote checkpoint to %s', cp_filename)

        return tot_pop, self.hof, logbook, history