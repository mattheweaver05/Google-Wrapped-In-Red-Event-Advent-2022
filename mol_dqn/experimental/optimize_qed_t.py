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

"""Optimize QED with number of steps left as a feature."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import functools
import json
import os
import time
from absl import app
from absl import flags
from absl import logging
from baselines.common import schedules
from baselines.deepq import replay_buffer
import numpy as np
from rdkit import Chem
from rdkit.Chem import QED
from six.moves import range
import tensorflow.compat.v1 as tf
from tensorflow.compat.v1 import gfile
from mol_dqn.chemgraph.mcts import deep_q_networks
from mol_dqn.chemgraph.mcts import molecules as molecules_mdp
from mol_dqn.chemgraph.tensorflow import core


flags.DEFINE_float('gamma', 0.999, 'discount')
FLAGS = flags.FLAGS


class Molecule(molecules_mdp.Molecule):
  """QED reward Molecule."""

  def _reward(self):
    molecule = Chem.MolFromSmiles(self._state)
    if molecule is None:
      return 0.0
    try:
      qed = QED.qed(molecule)
    except ValueError:
      qed = 0
    return qed * FLAGS.gamma**(self.max_steps - self._counter)


def run_training(hparams, environment, dqn):
  """Runs the training procedure.

  Briefly, the agent runs the action network to get an action to take in
  the environment. The state transition and reward are stored in the memory.
  Periodically the agent samples a batch of samples from the memory to
  update(train) its Q network. Note that the Q network and the action network
  share the same set of parameters, so the action network is also updated by
  the samples of (state, action, next_state, reward) batches.


  Args:
    hparams: tf.HParams. The hyper parameters of the model.
    environment: molecules.Molecule. The environment to run on.
    dqn: An instance of the DeepQNetwork class.

  Returns:
    None
  """
  summary_writer = tf.summary.FileWriter(FLAGS.model_dir)
  tf.reset_default_graph()
  with tf.Session() as sess:
    dqn.build()
    model_saver = tf.Saver(max_to_keep=hparams.max_num_checkpoints)
    # The schedule for the epsilon in epsilon greedy policy.
    exploration = schedules.PiecewiseSchedule(
        [(0, 1.0), (int(hparams.num_episodes / 2), 0.1),
         (hparams.num_episodes, 0.01)],
        outside_value=0.01)
    if hparams.prioritized:
      memory = replay_buffer.PrioritizedReplayBuffer(hparams.replay_buffer_size,
                                                     hparams.prioritized_alpha)
      beta_schedule = schedules.LinearSchedule(
          hparams.num_episodes, initial_p=hparams.prioritized_beta, final_p=0)
    else:
      memory = replay_buffer.ReplayBuffer(hparams.replay_buffer_size)
      beta_schedule = None
    sess.run(tf.global_variables_initializer())
    sess.run(dqn.update_op)
    global_step = 0
    for episode in range(hparams.num_episodes):
      global_step = _episode(
          environment=environment,
          dqn=dqn,
          memory=memory,
          episode=episode,
          global_step=global_step,
          hparams=hparams,
          summary_writer=summary_writer,
          exploration=exploration,
          beta_schedule=beta_schedule)
      if (episode + 1) % hparams.update_frequency == 0:
        sess.run(dqn.update_op)
      if (episode + 1) % hparams.save_frequency == 0:
        model_saver.save(
            sess,
            os.path.join(FLAGS.model_dir, 'ckpt'),
            global_step=global_step)


def _episode(environment, dqn, memory, episode, global_step, hparams,
             summary_writer, exploration, beta_schedule):
  """Runs a single episode.

  Args:
    environment: molecules.Molecule; the environment to run on.
    dqn: DeepQNetwork used for estimating rewards.
    memory: ReplayBuffer used to store observations and rewards.
    episode: Integer episode number.
    global_step: Integer global step; the total number of steps across all
      episodes.
    hparams: HParams.
    summary_writer: FileWriter used for writing Summary protos.
    exploration: Schedule used for exploration in the environment.
    beta_schedule: Schedule used for prioritized replay buffers.

  Returns:
    Updated global_step.
  """
  episode_start_time = time.time()
  environment.initialize()
  if hparams.num_bootstrap_heads:
    head = np.random.randint(hparams.num_bootstrap_heads)
  else:
    head = 0
  for step in range(hparams.max_steps_per_episode):
    result = _step(
        environment=environment,
        dqn=dqn,
        memory=memory,
        episode=episode,
        hparams=hparams,
        exploration=exploration,
        head=head)
    if step == hparams.max_steps_per_episode - 1:
      episode_summary = dqn.log_result(result.state, result.reward)
      summary_writer.add_summary(episode_summary, global_step)
      logging.info('Episode %d/%d took %gs', episode + 1, hparams.num_episodes,
                   time.time() - episode_start_time)
      logging.info('SMILES: %s\n', result.state)
      # Use %s since reward can be a tuple or a float number.
      logging.info('The reward is: %s', str(result.reward))
    if (episode > min(50, hparams.num_episodes / 10)) and (
        global_step % hparams.learning_frequency == 0):
      if hparams.prioritized:
        (state_t, _, reward_t, state_tp1, done_mask, weight,
         indices) = memory.sample(
             hparams.batch_size, beta=beta_schedule.value(episode))
      else:
        (state_t, _, reward_t, state_tp1,
         done_mask) = memory.sample(hparams.batch_size)
        weight = np.ones([reward_t.shape[0]])
      # np.atleast_2d cannot be used here because a new dimension will
      # be always added in the front and there is no way of changing this.
      if reward_t.ndim == 1:
        reward_t = np.expand_dims(reward_t, axis=1)
      td_error, error_summary, _ = dqn.train(
          states=state_t,
          rewards=reward_t,
          next_states=state_tp1,
          done=np.expand_dims(done_mask, axis=1),
          weight=np.expand_dims(weight, axis=1))
      summary_writer.add_summary(error_summary, global_step)
      logging.info('Current TD error: %.4f', np.mean(np.abs(td_error)))
      if hparams.prioritized:
        memory.update_priorities(
            indices,
            np.abs(np.squeeze(td_error) + hparams.prioritized_epsilon).tolist())
    global_step += 1
  return global_step


def _step(environment, dqn, memory, episode, hparams, exploration, head):
  """Runs a single step within an episode.

  Args:
    environment: molecules.Molecule; the environment to run on.
    dqn: DeepQNetwork used for estimating rewards.
    memory: ReplayBuffer used to store observations and rewards.
    episode: Integer episode number.
    hparams: HParams.
    exploration: Schedule used for exploration in the environment.
    head: Integer index of the DeepQNetwork head to use.

  Returns:
    molecules.Result object containing the result of the step.
  """
  # Compute the encoding for each valid action from the current state.
  valid_actions = list(environment.get_valid_actions())
  observations = np.vstack([
      np.append(
          deep_q_networks.get_fingerprint(act, hparams),
          environment.num_steps_taken) for act in valid_actions
  ])
  # Select the next action to take.
  action = valid_actions[dqn.get_action(
      observations, head=head, update_epsilon=exploration.value(episode))]
  result = environment.step(action)
  # Compute the encoding for each valid action from the new state.
  action_fingerprints = np.vstack([
      np.append(
          deep_q_networks.get_fingerprint(act, hparams),
          environment.num_steps_taken)
      for act in environment.get_valid_actions()
  ])
  # we store the fingerprint of the action in obs_t so action
  # does not matter here.
  memory.add(
      obs_t=np.append(
          deep_q_networks.get_fingerprint(action, hparams),
          environment.num_steps_taken),
      action=0,
      reward=result.reward,
      obs_tp1=action_fingerprints,
      done=float(result.terminated))
  return result


def main(argv):
  del argv
  if FLAGS.hparams is not None:
    with gfile.Open(FLAGS.hparams, 'r') as f:
      hparams = deep_q_networks.get_hparams(**json.load(f))
  else:
    hparams = deep_q_networks.get_hparams()

  environment = Molecule(
      atom_types=set(hparams.atom_types),
      init_mol=None,
      allow_removal=hparams.allow_removal,
      allow_no_modification=hparams.allow_no_modification,
      max_steps=hparams.max_steps_per_episode)

  dqn = deep_q_networks.DeepQNetwork(
      input_shape=(hparams.batch_size, hparams.fingerprint_length + 1),
      q_fn=functools.partial(
          deep_q_networks.multi_layer_model, hparams=hparams),
      optimizer=hparams.optimizer,
      grad_clipping=hparams.grad_clipping,
      num_bootstrap_heads=hparams.num_bootstrap_heads,
      gamma=hparams.gamma,
      epsilon=1.0)

  run_training(
      hparams=hparams,
      environment=environment,
      dqn=dqn,
  )

  core.write_hparams(hparams, os.path.join(FLAGS.model_dir, 'config.json'))


if __name__ == '__main__':
  app.run(main)
