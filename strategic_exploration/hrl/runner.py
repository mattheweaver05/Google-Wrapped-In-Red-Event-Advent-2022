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

import copy
import logging
import random
from collections import defaultdict
from strategic_exploration.hrl import abstract_state as AS
from strategic_exploration.hrl.action import EndEpisode, Teleport, DefaultAction
from strategic_exploration.hrl.explorer import Explorer
from strategic_exploration.hrl.graph_update import Visit, Traverse
from strategic_exploration.hrl.justification import Justification
from strategic_exploration.hrl.rl import Experience


class EpisodeRunner(object):
  """Responsible for keeping track of state for a single episode.

    Implements main algorithm:
        - Uses worker to follow the provided path.
        - On paths that end inside the feasible set, explores to find new
          abstract states

    Returns whole concrete state episode at at end of episode.
    """

  @classmethod
  def from_config(cls, config, path, graph, worker, num_actions):
    """Creates an EpisodeRunner from Config.

    See constructor for
        documentation.

        Args: config (Config) path (list[DirectedEdge]) graph (AbstractGraph)
        worker (Worker) num_actions (int)
    """
    explorer = Explorer.from_config(config.explorer, num_actions)
    return cls(path, graph, worker, num_actions, config.worker_reward_thresh,
               explorer, config.enable_teleporting)

  def __init__(self, path, graph, worker, num_actions, worker_reward_thresh,
               explorer, enable_teleporting):
    """Runs a single episode using the worker to follow path.

        Args:
            path (list[DirectedEdge]): path to follow graph (AbstractGraph)
            worker (Worker): worker to use
            num_actions (int): number of different actions
            worker_reward_thresh (float): worker episode is success if the
              episodic reward exceeds this thresh.
            explorer (Explorer): used for discovering new abstract states
            enable_teleporting (bool): allows teleporting on non-test episodes
    """
    self._plan = path
    self._graph = graph
    self._num_actions = num_actions
    self._worker = worker
    self._worker_reward_thresh = worker_reward_thresh
    self._worker_rewards = []
    self._explorer = explorer
    self._episode = []
    self._graph_updates = []
    self._edge_trajectories = defaultdict(Trajectory)
    self._saved_path = copy.copy(path)
    self._teleported = False
    self._enable_teleporting = enable_teleporting

    # Flag to prevent the following bug:
    # Runner A traverses edge (s, s') sucessfully non-greedily
    # Runner B traverses edge (s, s') successfully
    # Manager updates on B
    # Manager updates on A to set (s, s') to be reliable, setting the
    # teleport for s' to be the non-greedy path
    # Patch: only set teleport if path generated by runner was generated
    # when the edge was already evaluating or reliable through the entire
    # episode
    # Incorrect optimization: It's not possible to just observe the state
    # of the edge at the beginning of the worker episode, because the edge
    # could transition between evaluating --> training --> evaluating -->
    # reliable.
    self._allow_setting_teleport = True

  def act(self, state, test=False):
    """Returns action for the current state.

        Args: state (State)
            test (bool): if True, no teleporting is used

        Returns:
            action (Action)
            justification (Justification)
        """
    if len(self._plan) == 0:
      node = self._graph.get_node(AS.AbstractState(state))
      if (node is not None and node.active() and not self._explorer.active()):
        # This is happening in a separate process, so this shared
        # graph doesn't get updated
        self._graph_updates.append(Visit(node))
        node.visit()
        self._explorer.activate(node)

      if self._explorer.active():
        action, s = self._explorer.act(state)
        return action, Justification([], self._graph, s)
      elif test:  # No resetting on test episodes!
        action = DefaultAction(random.randint(0, self._num_actions - 1))
        justification = Justification([], self._graph, "test random")
        return action, justification
      else:
        return EndEpisode(), Justification([], self._graph, "reset")
    elif (self._enable_teleporting and not test and not self._teleported and
          len(self._plan) > 0 and self._plan[-1].start.teleport is not None):
      self._teleported = True
      self._plan = self._plan[-1:]
      s = "teleport to: {}".format(self._plan[-1].start.uid)
      justification = Justification(self._plan, self._graph, s)
      return self._plan[-1].start.teleport, justification

    next_edge = self._plan[0]
    self._allow_setting_teleport = \
            self._allow_setting_teleport and not next_edge.training()
    action = DefaultAction(
        self._worker.act(state, next_edge, len(self._worker_rewards),
                         sum(self._worker_rewards)))
    s = "{} -> {} step={} [{:.2f}], train={}, [{:.2f}]".format(
        next_edge.start.uid, next_edge.end.uid, len(self._worker_rewards),
        sum(self._worker_rewards), next_edge.train_count,
        next_edge.success_rate)
    justification = Justification(copy.copy(self._plan), self._graph, s)
    return action, justification

  def observe(self, state, action, reward, next_state, done):
    """Updates episode state based on observations from environment.

        Args: state (State) action (Action) reward (float) next_state (State)
        done (bool)
    """
    if isinstance(action, Teleport) or isinstance(action, EndEpisode):
      return

    experience = Experience(state, action.action_num, reward, next_state, done)
    self._episode.append(experience)

    if len(self._plan) > 0:
      assert not self._explorer.active()
      curr_edge = self._plan[0]
      self._edge_trajectories[curr_edge].append(
          (experience, len(self._worker_rewards), sum(self._worker_rewards)))
      worker_reward = self._worker.reward(next_state, curr_edge, reward, done)
      self._worker_rewards.append(worker_reward)
      success = worker_reward == 1 and curr_edge.reliable()
      success = success or sum(
          self._worker_rewards) >= self._worker_reward_thresh
      failure = done or reward < 0 or \
          len(self._worker_rewards) >= self._worker.max_steps(curr_edge)

      if success:
        # Grab the first time you hit the goal state for teleport
        index = self._worker_rewards.index(1.)
        teleport_exp, _, _ = self._edge_trajectories[curr_edge][index]
        teleport = teleport_exp.next_state.teleport
        if not self._allow_setting_teleport:
          teleport = None
        self._plan.pop(0)
        self._worker_rewards = []
        self._graph_updates.append(Traverse(curr_edge, True, teleport))
        self._edge_trajectories[curr_edge].set_success(True)
        self._allow_setting_teleport = True
      elif failure:
        if curr_edge.reliable():
          logging.error("Failed reliable edge: {}".format(curr_edge))
        self._worker_rewards = []
        self._plan = []
        self._graph_updates.append(Traverse(curr_edge, False))
        self._edge_trajectories[curr_edge].set_success(False)

  @property
  def episode(self):
    return self._episode

  @property
  def graph_updates(self):
    """The graph updates to make from the rolled out episode."""
    return self._graph_updates

  @property
  def edge_trajectories(self):
    """Returns dict DirectedEdge --> Trajectory on the attempted edge.

        NOTE: The reward for the Experience is the extrinsic environment
        reward, not the intrinsic edge reward!
        """
    return self._edge_trajectories

  @property
  def path(self):
    """The path this was constructed around."""
    return self._saved_path


class Trajectory(object):
  """Represents a single worker episode."""

  def __init__(self):
    self._trajectory = []
    self._success = False

  def __getitem__(self, index):
    return self._trajectory[index]

  def append(self, item):
    self._trajectory.append(item)

  @property
  def trajectory(self):
    """Returns list of experiences and number of timesteps the worker was

        active at the beginning of the Experience.

        Returns:
            list[(Experience, int)]
        """
    return self._trajectory

  @property
  def success(self):
    return self._success

  def set_success(self, success):
    self._success = success
