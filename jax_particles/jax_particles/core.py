# coding=utf-8
# Copyright 2022 The Google Research Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Core code for a 2D physics simulator for circular objects written in JAX."""
import jax.numpy as jnp


class Entity(object):

  def __init__(self):
    self.name = None
    self.color = (64, 64, 64)  # [3] int [0-255]
    self.color_alpha = 0.4  # float [0.0-1.0]
    self.radius = 0.05  # m
    self.density = 127  # kg/m^3 (gives mass of 1.0 at radius of 0.05)
    self.collideable = True  # True | False


class BaseEnvironment(object):
  """2D circle simulator with restitution."""

  def __init__(self):
    # to be modified if desired in the subclass's __init__()
    self.max_p = jnp.array([1.0, 1.0])  # x,y
    self.min_p = jnp.array([-1.0, -1.0])  # x,y
    self.dt = 0.1  # s
    self.damping = 1.0
    self.overlap_spring_constant = 20.0
    self.restitution = 0.0  # float [0.0-1.0]. higher is more elastic.
    self.same_position_check = True  # whether to include code to check for
                                     # entities at exactly the same position,
                                     # 3% hit to performance

    # to be populated in the subclass's __init__()
    self.entities = []  # [n] Entity, agents must be at the beginning
                        # and in the same order as actions passed to step()
    self.a_shape = None  # tuple representing the shape of the actions
    self.o_shape = None  # tuple representing the shape of the observations

    # auto-generated by _compile()
    self.radius = None  # [n,1] jnp.float32
    self.mass = None  # [n,1] jnp.float32
    self.collideable = None  # [n,1] jnp.float32

  def step(self, s, a):
    """Apply control, damping, boundary, and collision forces.

    Args:
      s: (p, v, misc), where p and v are [n_entities,2] jnp.float32,
         and misc is child defined
      a: [n_agents, dim_a] jnp.float32

    Returns:
      A state tuple (p, v, misc)
    """
    p, v, misc = s  # [n,2], [n,2], [a_shape]
    f = jnp.zeros_like(p)  # [n,2]
    n = p.shape[0]  # number of entities

    # Calculate control forces
    f_control = jnp.pad(a, ((0, n-a.shape[0]), (0, 0)),
                        mode="constant")  # [n, dim_a]
    f += f_control

    # Calculate damping forces
    f_damping = -1.0*self.damping*v  # [n,2]
    f = f + f_damping

    # Calculate boundary forces
    bounce = (((p+self.radius >= self.max_p) & (v >= 0.0)) |
              ((p-self.radius <= self.min_p) & (v <= 0.0)))  # [n,2]
    v_new = (-1.0*bounce + 1.0*~bounce)*v  # [n,2]
    f_boundary = self.mass*(v_new - v)/self.dt  # [n,2]
    f = f + f_boundary

    # Calculate shared quantities for later calculations
    # same: [n,n,1], True if i==j
    same = jnp.expand_dims(jnp.eye(n, dtype=jnp.bool_), axis=-1)
    # p2p: [n,n,2], p2p[i,j,:] is the vector from entity i to entity j
    p2p = p - jnp.expand_dims(p, axis=1)
    # dist: [n,n,1], p2p[i,j,0] is the distance between i and j
    dist = jnp.linalg.norm(p2p, axis=-1, keepdims=True)
    # overlap: [n,n,1], overlap[i,j,0] is the overlap between i and j
    overlap = ((jnp.expand_dims(self.radius, axis=1) +
                jnp.expand_dims(self.radius, axis=0)) -
               dist)
    if self.same_position_check:
      # ontop: [n,n,1], ontop[i,j,0] = True if i is at the exact location of j
      ontop = (dist == 0.0)
      # ontop_dir: [n,n,1], (1,0) above diagonal, (-1,0) below diagonal
      ontop_dir = jnp.stack([jnp.triu(jnp.ones((n, n)))*2-1, jnp.zeros((n, n))],
                            axis=-1)
      # contact_dir: [n,n,2], contact_dir[i,j,:] is the unit vector in the
      # direction of j from i
      contact_dir = (~ontop*p2p + (ontop*ontop_dir))/(~ontop*dist + ontop*1.0)
    else:
      # contact_dir: [n,n,2], contact_dir[i,j,:] is the unit vector in the
      # direction of j from i
      contact_dir = p2p/(dist+same)
    # collideable: [n,n,1], True if i and j are collideable
    collideable = (jnp.expand_dims(self.collideable, axis=1) &
                   jnp.expand_dims(self.collideable, axis=0))
    # overlap: [n,n,1], True if i,j overlap
    overlapping = overlap > 0

    # Calculate collision forces
    # Assume all entities collide with all entities, then mask out
    # non-collisions.
    #
    # For approaching, coliding entities, apply a forces
    # along the direction of collision that results in
    # relative velocities consistent with the coefficient of
    # restitution (c) and preservation of momentum in that
    # direction.
    # momentum: m_a*v_a + m_b*v_b = m_a*v'_a + m_b*v'_b
    # restitution: v'_b - v'_a = -c*(v_b-v_a)
    # solve for v'_a:
    #  v'_a = [m_a*v_a + m_b*v_b + m_b*c*(v_b-v_a)]/(m_a + m_b)
    #
    # v_contact_dir: [n,n] speed of i in dir of j
    v_contact_dir = jnp.sum(jnp.expand_dims(v, axis=-2)*contact_dir, axis=-1)
    # v_approach: [n,n] speed that i,j are approaching each other
    v_approach = jnp.transpose(v_contact_dir) + v_contact_dir
    # momentum: [n,n] joint momentum in direction of contact (i->j)
    momentum = self.mass*v_contact_dir - jnp.transpose(self.mass*v_contact_dir)
    # v_result: [n,n] speed of i in dir of j after collision
    v_result = ((momentum +
                 self.restitution*jnp.transpose(self.mass)*(-v_approach)) /
                (self.mass + jnp.transpose(self.mass)))
    # f_collision: [n,n] force on i in dir of j to realize acceleration
    f_collision = self.mass*(v_result - v_contact_dir)/self.dt
    # f_collision: [n,n,2] force on i to realize acceleration due to
    # collision with j
    f_collision = jnp.expand_dims(f_collision, axis=-1)*contact_dir
    # collision_mask: [n,n,1]
    collision_mask = (collideable & overlapping & ~same &
                      (jnp.expand_dims(v_approach, axis=-1) > 0))
    # f_collision: [n,2], sum of collision forces on i
    f_collision = jnp.sum(f_collision*collision_mask, axis=-2)
    f = f + f_collision

    # Calculate overlapping spring forces
    # This corrects for any overlap due to discrete steps.
    # f_overlap: [n,n,2], force in the negative contact dir due to overlap
    f_overlap = -1.0*contact_dir*overlap*self.overlap_spring_constant
    # overlapping_mask: [n,n,1], True if i,j are collideable, overlap,
    # and i != j
    overlapping_mask = collideable & overlapping & ~same
    # f_overlap: [n,2], sum of spring forces on i
    f_overlap = jnp.sum(f_overlap*overlapping_mask, axis=-2)
    f = f + f_overlap

    # apply forces
    v = v + (f/self.mass)*self.dt
    p = p + v*self.dt

    # update misc
    misc = self._update_misc((p, v, misc), a)  # pylint: disable=assignment-from-none

    return (p, v, misc)

  def init_state(self, rng):
    """Returns a state (a,p,misc) tuple."""
    raise NotImplementedError()

  def obs(self, s):
    """Returns an observation of shape o_shape."""
    raise NotImplementedError()

  def reward(self, s):
    """Returns a joint reward: np.float32."""
    raise NotImplementedError()

  def _compile(self):
    """To be called at the end of the child's constructor."""
    self.radius = jnp.array([[entity.radius]
                             for entity in self.entities])  # [n,1]
    self.mass = jnp.array([[(3.14159*entity.radius**2)*entity.density]
                           for entity in self.entities])  # [n,1]
    self.collideable = jnp.array([[entity.collideable]
                                  for entity in self.entities])  # [n,1]

  def _update_misc(self, s, a):  # pylint: disable=unused-argument
    """Returns the new 'misc' to be stored in the state."""
    return None
