"""bluepyopt.optimisations tests"""

import nose.tools as nt

import bluepyopt.optimisations
import bluepyopt.ephys.examples as examples

from nose.plugins.attrib import attr

import deap.tools

@attr('unit')
def test_DEAPOptimisationCMA_normspace():
    "deapext.optimisation: Testing CMADEAPOptimisation normspace"

    evaluator = examples.simplecell.cell_evaluator
    optimisation = bluepyopt.deapext.CMADEAPoptimisations.CMADEAPOptimisation(
        evaluator=evaluator)

    x = [n*0.1 for n in range(len(evaluator.params))]
    y = [f2(f1(_)) for _,f1,f2 in zip(x, optimisation.to_norm,
                                      optimisation.to_space)]
    for a, b in zip(x, y):
        nt.assert_almost_equal(a, b)

@attr('unit')
def test_DEAPOptimisationCMA_run():
    "deapext.optimisation: Testing CMADEAPOptimisation run from centroid"

    evaluator = examples.simplecell.cell_evaluator
    x = [n * 0.1 for n in range(len(evaluator.params))]

    try:
        optimisation = bluepyopt.deapext.CMADEAPoptimisations.CMADEAPOptimisation(
            evaluator=evaluator,
            centroids=[x])

        pop, hof, log, hist = optimisation.run(max_ngen=1)
        raised = False
    except:
        raised = True

    nt.assert_equal(raised, False)
