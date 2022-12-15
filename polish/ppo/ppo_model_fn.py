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

"""Input function hook for PPO TF estimator.

For the PPO algorithm, see https://arxiv.org/abs/1707.06347.
"""
from absl import logging
import gin
import numpy as np
import tensorflow.compat.v1 as tf
from polish.ppo import ppo_loss
from polish.utils import distributions
from polish.utils import host_call_fn
from polish.utils import tf_layers
from tensorflow.contrib import tpu as contrib_tpu

logging.set_verbosity(logging.INFO)


@gin.configurable
class PpoModelFn(object):
  """Main class for model function used in tf.estimator.

  Attributes:
    policy_loss: Proximal Policy Optimization (PPO) policy loss.
    value_loss: PPO value loss.
    entropy_loss: PPO entropy loss.
    imitation_kl_divergence: The KL-divergence of action distributions between
      the policy and MCTS.
    total_loss: PPO total loss.
    clipfrac: Fraction of examples in a batch clipped by PPO.
    approxkl: `Approximate` KL divergence between new policy and old policy.
      This is an estimate (approximate) of the KL divergence, since we compute
      the KL divergence using the samples drawn from the new and
      old distributions.
    total_params: Total trainable parameters.
    train_op: Training operation.
    mean_new: Mean of new policy distribution.
    logstd_new: Log standard deviation of new policy distribution.
    mean_old: Mean of old policy distribution.
    logstd_old: Log standard deviation of old policy distribution.
    value_new: state-value from the latest trained state-value network.
    kl_divergence: Kullback-Leibler divergence between new and old policy.
    entropy: Entropy of the new policy.
    global_step: Global step of training.
    policy_ratio: the ratio between new policy and old policy.
    last_iteration_mcts_enable: Track the sampling type (PPO sampling/MCTS) in
      the process of training. That is, whether in the last iteration of
      training, we use PPO sampling (False) or MCTS sampling (True).
    mcts_sampling_enable: If True, it means that the current batch
        is generated by MCTS.
    mean_mse_loss: Mean squared error between the mean value of
      policy distribuiton and the mean value returned by MCTS given a state.
    logstd_mse_loss: Mean squared error between log of standard deviation value
      of the policy distribuiton and the log of standard deviation value
      returned by MCTS given a state.
  """

  def __init__(
      self,
      env_action_space=2,
      iterations_per_loop=320,
      num_timesteps=1000000,
      max_horizon=2048,
      learning_rate=3e-4,
      use_tpu=False,
      ppo2_enable=True,
      policy_coeff=1.0,
      value_coeff=0.5,
      entropy_coeff=0.0,
      tpu_num_shards=8,
      mse_loss_coeff=0.0,
      warmstart_file=None,
      policy_hidden_layer_size=64,
      value_hidden_layer_size=64):
    """Creates a model function for PPO algorithm.

    The default values for all the parameters are from PPO paper.

    Args:
      env_action_space: The size of environment action space.
      iterations_per_loop: Number of steps to run on TPU before outfeeding
        metrics to the CPU. If the number of iterations in the loop would exceed
        the number of train steps, the loop will exit before reaching
        --iterations_per_loop. The larger this value is, the higher the
        utilization on the TPU.
      num_timesteps: Total number of timesteps. Defines the total number of
        samples taken from the environment during the whole process of training.
      max_horizon: Maximum number of samples taken from the environment before
        starting training.
      learning_rate: Initial learning rate value. Note that the actual learning
        rate is linearly decayed.
      use_tpu: If True, training occurs on TPU.
      ppo2_enable: If True, we use the next version of PPO algorithm, known as
        PPO2. In this version, not only does the probability ratio get clipped,
        but also the clipping is performed on the value loss.
        For more information:
        https://github.com/openai/baselines/tree/master/baselines/ppo2.
      policy_coeff: Policy loss coefficient in the total loss calculation.
      value_coeff: Value loss coefficient in the total loss calculation.
      entropy_coeff: Entropy loss coefficient in the total loss calculation.
      tpu_num_shards: Number of TPU shards.
      mse_loss_coeff: The coefficient for Mean Squared Error (MSE) loss.
      warmstart_file: If not None, we restore the weights for the parameters in
        `newpolicy` scope from this file. `newpolicy` scope contains both
        policy and value network.
      policy_hidden_layer_size: The size of hidden layer in policy network.
        Currently, this value is used for both of the hidden layers.
      value_hidden_layer_size: The size of hidden layer in value network.
        Currently, this value is used for both of the hidden layers.
    """
    self.policy_loss = 0
    self.value_loss = 0
    self.entropy_loss = 0
    self.total_loss = 0
    self.clipfrac = 0
    self.approxkl = 0
    self.policy_ratio = 0

    self.total_params = None
    self.train_op = None

    self.mean_new = None
    self.logstd_new = None
    self.mean_old = None
    self.logstd_old = None
    self.value_new = None

    self.kl_divergence = None
    self.entropy = 0

    self.global_step = None

    self._decayed_learning_rate = None

    self._env_action_space = env_action_space
    self._iterations_per_loop = iterations_per_loop
    self._num_timesteps = num_timesteps
    self._max_horizon = max_horizon
    self._learning_rate = learning_rate
    self._use_tpu = use_tpu
    self._ppo2_enable = ppo2_enable
    self._policy_coeff = policy_coeff
    self._value_coeff = value_coeff
    self._entropy_coeff = entropy_coeff
    self._tpu_num_shards = tpu_num_shards

    self._mse_loss_coeff = mse_loss_coeff
    self._warmstart_file = warmstart_file

    self.last_iteration_mcts_enable = False
    self._mcts_global_step = 0

    self._policy_hidden_layer_size = policy_hidden_layer_size
    self._value_hidden_layer_size = value_hidden_layer_size

  def __call__(self, features, labels, mode, params):
    return self.model_fn(features, labels, mode, params)

  def model_inference_fn_ppo(self, features, prefix):
    """Builds just the inference part of the model graph.

    Args:
      features: input features tensor.
      prefix: prefix to be added to the network.

    Returns:
      (value, var, mean) tuple of tensors.
    """
    # Policy Network
    features = tf.layers.flatten(features)
    with tf.variable_scope(prefix + 'policy', reuse=tf.AUTO_REUSE):
      policy_1 = tf.tanh(
          tf_layers.fc(
              tensor_in=features,
              num_hidden=self._policy_hidden_layer_size,
              scope_name='/policy_1',
              init_scale=np.sqrt(2)))
      policy_2 = tf.tanh(
          tf_layers.fc(
              tensor_in=policy_1,
              num_hidden=self._policy_hidden_layer_size,
              scope_name='/policy_2',
              init_scale=np.sqrt(2)))
      mean = tf_layers.fc(
          tensor_in=policy_2,
          num_hidden=self._env_action_space,
          scope_name='/mean',
          init_scale=0.01,
          init_bias=0.0)
      logstd_var = tf.get_variable(
          name=prefix + '_logstd',
          shape=[1, self._env_action_space],
          initializer=tf.zeros_initializer())
      # Evaluate logstd_var and broadcast to have a same shape as mean
      logstd = tf.multiply(logstd_var, 1.0)

      value_1 = tf.tanh(
          tf_layers.fc(
              tensor_in=features,
              num_hidden=self._value_hidden_layer_size,
              scope_name='/value_1',
              init_scale=np.sqrt(2)))
      value_2 = tf.tanh(
          tf_layers.fc(
              tensor_in=value_1,
              num_hidden=self._value_hidden_layer_size,
              scope_name='/value_2',
              init_scale=np.sqrt(2)))
      value = tf_layers.fc(
          tensor_in=value_2, num_hidden=1, scope_name='/value')[:, 0]

    return value, logstd, mean

  def learning_rate_update_true_fn(self):
    """The function which is performed if the predicate is true.

    The predicate that calls this function is defined in 'update_learning_rate'.

    Returns:
      The current global step.
    """
    return tf.train.get_global_step()

  def learning_rate_update_false_fn(self):
    """The function which is performed if the predicate is false.

    The predicate that calls this function is defined in 'update_learning_rate'.

    Returns:
      A type-casted value of `_mcts_global_step` to int64.
      `_mcts_global_step` is the global step at which MCTS algorithm starts.
      The type casting is necessary as the type of returned tensor in `true_fn`
      is an int.64.
    """
    return tf.cast(self._mcts_global_step, tf.int64)

  def update_learning_rate(self):
    """Update the learning rate with a decaying factor.
    """
    self._current_global_step = tf.cond(
        tf.equal(self.mcts_sampling_enable,
                 True), lambda: self._mcts_global_step, lambda: 0)

    self._current_global_step = tf.cast(self._current_global_step, tf.int64)

    update = (tf.train.get_global_step() -
              self._current_global_step) // self._iterations_per_loop + 1
    current_frac = self._num_timesteps // self._max_horizon
    update = tf.cast(update, tf.float32)
    current_frac = tf.cast(current_frac, tf.float32)
    frac = 1.0 - (update - 1.0) / current_frac
    self._decayed_learning_rate = self._learning_rate * frac
    self._mcts_global_step = tf.cond(
        tf.not_equal(self.mcts_sampling_enable,
                     self.last_iteration_mcts_enable),
        self.learning_rate_update_true_fn, self.learning_rate_update_false_fn)
    self.last_iteration_mcts_enable = self.mcts_sampling_enable

  def build_training_op(self, loss):
    """Get training operation.

    Args:
      loss: a loss function for training.

    Define the optimization operation and perform gradient calculation for both
      TPU/Non-TPU training.

    Returns:
      Computed gradient.
    """
    adam_optimizer = tf.train.AdamOptimizer(
        learning_rate=self._decayed_learning_rate, epsilon=1e-5)
    if self._use_tpu:
      # If we use TPUs, reduce_mean runs on each chip separately and by default
      # only the loss of the first chip is reported.
      #
      # You can either:
      # - execute this if, which synchronizes the losses
      #   across the chips to obtain the full loss on all samples.
      # - or remove this section, gaining some performance and getting the
      #   loss only from the first chip.
      # compute gradients perform averaging of the loss
      adam_optimizer = tf.tpu.CrossShardOptimizer(adam_optimizer)

      tpu_sum_loss = contrib_tpu.cross_replica_sum(loss / self._tpu_num_shards)

      grads_and_vars = adam_optimizer.compute_gradients(tpu_sum_loss,
                                                        self.total_params)
      grads, var = zip(*grads_and_vars)
      sum_grads = []
      sum_vars = []
      for (grad, var) in grads_and_vars:
        if grad is None:
          sum_grads.append(grad)
          sum_vars.append(var)
        else:
          sum_grads.append(
              contrib_tpu.cross_replica_sum(grad) / self._tpu_num_shards)
          sum_vars.append(var)
      # calculate sum of grads
      norm_grads, _ = tf.clip_by_global_norm(sum_grads, 0.5)
      grads_and_vars = list(zip(norm_grads, sum_vars))
    else:
      grads_and_vars = adam_optimizer.compute_gradients(loss,
                                                        self.total_params)
      grads, var = zip(*grads_and_vars)
      norm_grads, _ = tf.clip_by_global_norm(grads, 0.5)
      grads_and_vars = list(zip(norm_grads, var))

    return adam_optimizer.apply_gradients(
        grads_and_vars, global_step=tf.train.get_global_step())

  def calc_normalized_advantage(self, return_tensor, value_tensor):
    """Compute General Advantage Estimation (GAE) and normalize it.

    Note that, the advantage calculation-normalization is performed for a batch
      of data.

    Args:
      return_tensor: The discounted accumulated reward (return) calculated
        for the given rollout trajectory.
      value_tensor: The value for each state for the given rollout trajectory.

    Returns:
      Returns the normalized General Advantage Estimation (GAE).
    """
    batch_advantage = return_tensor - value_tensor
    batch_advantage_std = tf.keras.backend.std(batch_advantage)
    batch_advantage_mean = tf.reduce_mean(batch_advantage)
    batch_advantage_norm = (batch_advantage - batch_advantage_mean) / (
        batch_advantage_std + 1e-8)
    return batch_advantage_norm

  def create_host_call_fn(self, params):
    """Create host call function.

    `host_call` function is later called by TPU estimator to
      send some metrics to host for logging.

    Args:
      params: A dictionary of hyperparameters passed to the tf.estimator.

    Returns:
      A host call function that generates a set of tf summaries.
    """
    names_and_tensors = [
        ('Batch_Params/mean_mse_loss', self.mean_mse_loss),
        ('Batch_Params/logstd_mse_loss', self.logstd_mse_loss),
        ('Batch_Params/policy_loss', self.policy_loss),
        ('Batch_Params/mcts_enable', self.mcts_sampling_enable),
        ('Batch_Params/value_loss', self.value_loss),
        ('Batch_Params/policy_entropy', self.entropy_loss),
        ('Batch_Params/imitation_kl_divergence', self.imitation_kl_divergence),
        ('Batch_Params/clip_fraction', self.clipfrac),
        ('Batch_Params/max_ratio', tf.reduce_max(self.policy_ratio)),
        ('Batch_Params/min_ratio', tf.reduce_min(self.policy_ratio)),
        ('Batch_Params/mean_ratio', tf.reduce_mean(self.policy_ratio)),
        ('Batch_Params/approx_kl', self.approxkl),
        ('Learning_Rate/learning_rate', self._decayed_learning_rate),
        ('Learning_Rate/global_step', tf.train.get_global_step())
    ]

    return host_call_fn.build_host_call_fn_every_n_global_steps(
        params=params,
        names_and_tensors=names_and_tensors,
        n=self._iterations_per_loop)

  def compute_total_loss(self, pd_new, pd_old, value_tensor, return_tensor,
                         batch_advantage_norm,
                         policy_old_neg_logprob_tensor,
                         policy_action_tensor):
    """Defines the total loss function.

    Args:
      pd_new: The current policy distribution
        (a multivariate normal distribution). This policy distribution gets
        updated in the course of training.
      pd_old: The old policy distribution that we use during sampling the
        trajectory (a multivariate normal distribution).
      value_tensor: The values associated to the rollout trajectory.
      return_tensor: The return values computed for the rollout trajectory.
      batch_advantage_norm: The normalized advantage tensor computed for a
        batch of data. For advantage calculation, we use generalized
        advantage estimation (GAE) formula.
      policy_old_neg_logprob_tensor: The negative log probabilities from the
        policy rollouts.
      policy_action_tensor: The actions from the policy rollouts.
    """
    # Policy loss
    ppo_policy_loss_out = ppo_loss.ppo_policy_loss(
        neg_logprobs_old=policy_old_neg_logprob_tensor,
        actions=policy_action_tensor,
        advantages=batch_advantage_norm,
        dist_new=pd_new,
        mcts_sampling=self.mcts_sampling_enable)

    (self.policy_loss, self.approxkl, self.clipfrac,
     self.policy_ratio) = ppo_policy_loss_out

    # Value Loss
    if self._ppo2_enable:
      self.value_loss = ppo_loss.ppo2_value_loss(
          value_old=value_tensor,
          pred_value=self.value_new,
          returns=return_tensor)
    else:
      self.value_loss = ppo_loss.ppo1_value_loss(
          pred_value=self.value_new, returns=return_tensor)

    # MSE loss between mean and standard deviations
    self.mean_mse_loss, self.logstd_mse_loss = ppo_loss.l2_norm_policy_loss(
        policy_mean=self.mean_new,
        policy_logstd=self.logstd_new,
        mcts_mean=self.mean_old,
        mcts_logstd=self.logstd_old)

    mcts_dist = distributions.MultiVariateNormalDiag(
        mean=self.mean_old, logstd=self.logstd_old)
    policy_dist = distributions.MultiVariateNormalDiag(
        mean=self.mean_new, logstd=self.logstd_new)
    self.imitation_kl_divergence = tf.reduce_mean(
        policy_dist.kl_divergence(mcts_dist))
    # Calculate KL divergence and entropy of new distribution
    self.kl_divergence = tf.reduce_mean(pd_new.kl_divergence(pd_old))
    self.entropy = pd_new.entropy()

    # Calculate entropy loss
    self.entropy_loss = tf.reduce_mean(self.entropy)

    # Calulate total loss
    total_loss_ppo = (self._policy_coeff * self.policy_loss) + (
        self._value_coeff * self.value_loss) - (
            self._entropy_coeff * self.entropy_loss)

    total_loss_mcts = (self._value_coeff * self.value_loss) + (
        self._mse_loss_coeff *
        (self.imitation_kl_divergence + self.entropy_loss))

    self.total_loss = tf.cond(
        tf.equal(self.mcts_sampling_enable,
                 True), lambda: total_loss_mcts, lambda: total_loss_ppo)

  def model_fn(self, features, labels, mode, params):
    """The implementation of PPO algorithm.

    Args:
      features: dict from string to tensor with shape
            'state_tensor': [BATCH_SIZE, env.state_space]
      labels: dict from string to tensor with shape
              'action_tensor': [BATCH_SIZE, self._env_action_space]
              'advantage_tensor': [BATCH_SIZE]
              'returns_tensor': [BATCH_SIZE]
      mode: a tf.estimator.ModeKeys (batchnorm params update for TRAIN only).
      params: (Ignored; needed for compat with TPUEstimator).

    Returns:
      tf.estimator.EstimatorSpec with props.
      mode: same as mode arg.
      predictions: dict of tensors
              'mean': [BATCH_SIZE, self._env_action_space]
              'logstd': [BATCH_SIZE, self._env_action_space]
              'value': [BATCH_SIZE]
              'action': [BATCH_SIZE, self._env_action_space]
              'neg_logprob': [BATCH_SIZE, self._env_action_space]
      loss: a single value tensor.
      train_op: train op eval_metric_ops return dict of tensors.
    """

    # Policy network
    network_out = self.model_inference_fn_ppo(features['mcts_features'], 'new')
    self.value_new = network_out[0]
    self.logstd_new = network_out[1]
    self.mean_new = network_out[2]

    self.global_step = tf.train.get_or_create_global_step()
    # Sample an action
    pd_new = distributions.MultiVariateNormalDiag(
        mean=self.mean_new, logstd=self.logstd_new)
    action_sample = pd_new.sample()
    action_sample_neg_logprob = pd_new.negative_log_prob(action_sample)

    # Used during TF estimator prediction
    if mode == tf.estimator.ModeKeys.PREDICT:
      predictions = {
          'mean': self.mean_new,
          'logstd': self.logstd_new,
          'value': self.value_new,
          'action': action_sample,
          'neg_logprob': action_sample_neg_logprob
      }
      pred_estimator = tf.estimator.tpu.TPUEstimatorSpec(
          mode,
          predictions=predictions,
          export_outputs={
              'ppo_inference':
                  tf.estimator.export.PredictOutput({
                      'mean': self.mean_new,
                      'logstd': self.logstd_new,
                      'value': self.value_new,
                      'action': action_sample,
                      'neg_logprob': action_sample_neg_logprob
                  })
          })
      return pred_estimator.as_estimator_spec()

    # Placeholder
    self.mcts_sampling_enable = tf.reduce_all(labels['mcts_enable_tensor'])

    self.mean_old = labels['mean_tensor']
    self.logstd_old = labels['logstd_tensor']
    pd_old = distributions.MultiVariateNormalDiag(
        mean=self.mean_old, logstd=self.logstd_old)

    batch_advantage_norm = self.calc_normalized_advantage(
        return_tensor=labels['policy_return_tensor'],
        value_tensor=labels['policy_value_tensor'])

    self.compute_total_loss(pd_new, pd_old, labels['value_tensor'],
                            labels['return_tensor'], batch_advantage_norm,
                            labels['policy_old_neg_logprob_tensor'],
                            labels['policy_action_tensor'])
    # Update learning rate
    self.update_learning_rate()

    # Build training operation
    self.total_params = tf.trainable_variables(scope='newpolicy')

    train_ops = self.build_training_op(self.total_loss)

    host_call = self.create_host_call_fn(params)

    if mode != tf.estimator.ModeKeys.TRAIN:
      raise ValueError('Estimator mode should be train at this point.')

    if mode == tf.estimator.ModeKeys.TRAIN:
      # Setup fine tune scaffold
      # The scaffold here is used to restore the weights from _warmstart_file.
      # If _warmstart_file is None, the training starts from the beginning.
      if self._warmstart_file:
        logging.info('Warmstart')
        def tpu_scaffold():
          # restore all the variables
          tf.init_from_checkpoint(self._warmstart_file,
                                  {'newpolicy/': 'newpolicy/'})
          return tf.train.Scaffold()
        scaffold_fn = tpu_scaffold
      else:
        scaffold_fn = None

      tpu_estimator_spec = tf.estimator.tpu.TPUEstimatorSpec(
          mode=mode,
          loss=self.total_loss,
          train_op=train_ops,
          host_call=host_call,
          scaffold_fn=scaffold_fn)
      if self._use_tpu:
        return tpu_estimator_spec
      else:
        return tpu_estimator_spec.as_estimator_spec()
