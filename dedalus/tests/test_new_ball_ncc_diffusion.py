

import numpy as np
from dedalus.core import coords, distributor, basis, field, operators, problems, solvers, timesteppers, arithmetic, timesteppers_sphere
from dedalus.tools import logging
from dedalus.tools.parsing import split_equation
from dedalus.extras.flow_tools import GlobalArrayReducer
from scipy import sparse
import dedalus_sphere
import time
from mpi4py import MPI

import logging
logger = logging.getLogger(__name__)

# Parameters
radius = 1
Lmax = 3
L_dealias = 1
Nmax = 31
N_dealias = 1
dt = 0.01
t_end = 3
ts = timesteppers.CNAB2

# Bases
c = coords.SphericalCoordinates('phi', 'theta', 'r')
d = distributor.Distributor((c,))
b = basis.BallBasis(c, (2*(Lmax+1), Lmax+1, Nmax+1), radius=radius, dealias=(L_dealias, L_dealias, N_dealias))
b_S2 = b.S2_basis()
phi, theta, r = b.local_grids((1, 1, 1))

# Fields
T = field.Field(dist=d, bases=(b,), dtype=np.complex128)
tau = field.Field(dist=d, bases=(b_S2,), dtype=np.complex128)

prefactor = field.Field(dist=d, bases=(b.radial_basis,), dtype=np.complex128)
#prefactor['g'] = 1/(1+r**4)
prefactor['g'] = 1.

forcing = field.Field(dist=d, bases=(b,), dtype=np.complex128)
forcing['g'] = 1/(1+r**2)

# Parameters and operators
ez = field.Field(dist=d, bases=(b,), tensorsig=(c,), dtype=np.complex128)
ez['g'][1] = -np.sin(theta)
ez['g'][2] =  np.cos(theta)
div = lambda A: operators.Divergence(A, index=0)
lap = lambda A: operators.Laplacian(A, c)
grad = lambda A: operators.Gradient(A, c)
dot = lambda A, B: arithmetic.DotProduct(A, B)
cross = lambda A, B: arithmetic.CrossProduct(A, B)
ddt = lambda A: operators.TimeDerivative(A)

# Problem
def eq_eval(eq_str):
    return [eval(expr) for expr in split_equation(eq_str)]
problem = problems.IVP([T, tau])
problem.add_equation(eq_eval("ddt(T) - prefactor*lap(T) = forcing"))
problem.add_equation(eq_eval("T(r=1) = 0"))
logger.info("Problem built")

# Solver
solver = solvers.InitialValueSolver(problem, ts)
solver.stop_sim_time = t_end

# Add taus
alpha_BC = 0

def C(N, ell, deg):
    ab = (alpha_BC,ell+deg+0.5)
    cd = (2,       ell+deg+0.5)
    return dedalus_sphere.jacobi.coefficient_connection(N+1 - ell//2,ab,cd)

def BC_rows(N, ell, num_comp):
    N_list = (np.arange(num_comp)+1)*(N - ell//2 + 1)
    return N_list

for subproblem in solver.subproblems:
    ell = subproblem.group[1]
    L = subproblem.L_min
    N0, = BC_rows(Nmax, ell, 1)
    tau_columns = np.zeros((L.shape[0], 1))
    tau_columns[:N0,0] = (C(Nmax, ell, 0))[:,-1]
    L[:,-1] = tau_columns
    L.eliminate_zeros()
    subproblem.expand_matrices(['M','L'])

# Analysis
t_list = []
E_list = []
weight_theta = b.local_colatitude_weights(1)
weight_r = b.local_radial_weights(1)
reducer = GlobalArrayReducer(d.comm_cart)
vol_test = np.sum(weight_r*weight_theta+0*T['g'])*np.pi/(Lmax+1)/L_dealias
vol_test = reducer.reduce_scalar(vol_test, MPI.SUM)
vol_correction = 4*np.pi/3/vol_test

# Main loop
start_time = time.time()
while solver.ok:
    if solver.iteration % 10 == 0:
        E0 = np.sum(vol_correction*weight_r*weight_theta*T['g'].real**2)
        E0 = 0.5*E0*(np.pi)/(Lmax+1)/L_dealias
        E0 = reducer.reduce_scalar(E0, MPI.SUM)
        logger.info("t = %f, E = %.15e" %(solver.sim_time, E0))
        t_list.append(solver.sim_time)
        E_list.append(E0)
    solver.step(dt)
end_time = time.time()
logger.info('Run time: %f', (end_time-start_time))

