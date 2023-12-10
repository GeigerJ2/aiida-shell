#!/usr/bin/env runaiida
"""Simulation of Lennard-Jones fluid using LAMMPS."""
from aiida import orm
from aiida_shell import launch_shell_job

script = """
# 3d Lennard-Jones melt

variable      x index 1
variable      y index 1
variable      z index 1

variable      xx equal 20*$x
variable      yy equal 20*$y
variable      zz equal 20*$z

units         lj
atom_style    atomic

lattice       fcc 0.8442
region        box block 0 ${xx} 0 ${yy} 0 ${zz}
create_box    1 box
create_atoms  1 box
mass          1 1.0

velocity      all create 1.44 87287 loop geom

pair_style    lj/cut 2.5
pair_coeff    1 1 1.0 1.0 2.5

neighbor      0.3 bin
neigh_modify  delay 0 every 20 check no

fix           1 all nve
run           100
"""

results, node = launch_shell_job(
    'lmp',
    arguments='-in {script}',
    nodes={
        'script': orm.SinglefileData.from_string(script),
    },
)
print(f'Calculation terminated: {node.process_state}')
print('Outputs:')
for key, node in results.items():
    print(f'{key}: {node.__class__.__name__}<{node.pk}>')
