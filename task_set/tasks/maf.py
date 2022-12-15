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

# python3
"""Masked autoregressive flows models.

Paper: https://arxiv.org/abs/1705.07057
"""

from typing import Any, Dict, Optional, Text, Iterable, Callable
import numpy as np
import sonnet as snt

from task_set import registry
from task_set.tasks import base
from task_set.tasks import utils

import tensorflow.compat.v1 as tf
import tensorflow_probability as tfp

tfb = tfp.bijectors


def dist_with_maf_bijectors(
    data,
    num_bijectors,
    layers,
    activation = tf.nn.relu,
    w_init = None):
  """Creates the MAF model.

  Args:
    data: Data to transform.
    num_bijectors: Number of bijectors to use.
    layers: Hidden layers used per bijector.
    activation: Activation function used by bijectors.
    w_init: Initializer used for the bijector network.

  Returns:
    Learnable distribution representing the generative model.
  """
  batch_size = data.shape[0]
  channels = int(np.prod(data.shape.as_list()[1:]))

  ndim = int(np.prod(data.shape[1:4]))

  bijectors = []
  for i in range(num_bijectors):
    bijectors.append(
        tfb.MaskedAutoregressiveFlow(
            shift_and_log_scale_fn=tfb.masked_autoregressive_default_template(
                hidden_layers=layers,
                name="maf_%d" % i,
                kernel_initializer=w_init,
                activation=activation,
            )))
    permute_matrix = np.random.permutation(ndim).astype("int32")
    # store as the permutation as a tf variable to ensure consistent values
    # across graph creation.
    var_permute_matrix = tf.get_variable(
        "permutation_%d" % i,
        initializer=permute_matrix,
        trainable=False,
        dtype=permute_matrix.dtype)
    bijectors.append(tfb.Permute(permutation=var_permute_matrix))

  flow_bijector = tfb.Chain(list(reversed(bijectors[:-1])))

  base_dist = tfp.distributions.MultivariateNormalDiag(
      loc=tf.zeros([batch_size, channels]),
      scale_diag=tf.ones([batch_size, channels]))
  return tfp.distributions.TransformedDistribution(
      distribution=base_dist, bijector=flow_bijector)


def neg_log_p(distribution,
              image):
  """Computes a mean over pixel log probability density of the image.

  Because we are working in continuous space, this function also adds a bit of
  noise to prevent unbounded densities.

  Args:
    distribution: Distribution to compute log prob over.
    image: Image to compute log p of.

  Returns:
    The negative log probability density.
  """
  batch_size = image.shape[0]
  inp = tf.reshape(image, [batch_size, -1])
  # add a little bit of noise to prevent unbounded log prob density.
  log_p = distribution.log_prob(inp +
                                tf.random_normal(shape=inp.shape, stddev=0.01))
  mean_log_p = tf.reduce_mean(log_p)
  return -mean_log_p


MafConfig = Dict[Text, Any]


@registry.task_registry.register_sampler("maf_family")
def sample_maf_family_cfg(seed):
  """Sample a task config for a MAF model on image datasets.

  These configs are nested python structures that provide enough information
  to create an instance of the problem.

  Args:
    seed: int Random seed to generate task from.

  Returns:
    A nested dictionary containing a configuration.
  """
  # random offset to seed to prevent clashing with other problems.
  rng = np.random.RandomState(seed + 6123348)
  cfg = {}

  cfg["activation"] = utils.sample_activation(rng)
  cfg["w_init"] = utils.sample_initializer(rng)
  cfg["dataset"] = utils.sample_image_dataset(rng)

  n_layers = int(rng.choice([1, 2]))
  cfg["hidden_units"] = [
      utils.sample_log_int(rng, 16, 128) for _ in range(n_layers)
  ]
  cfg["num_bijectors"] = int(rng.choice([1, 2, 3, 4]))
  return cfg


@registry.task_registry.register_getter("maf_family")
def get_maf_family(cfg):
  """Get a task for the given cfg.

  Args:
    cfg: config specifying the model generated by `sample_maf_family_cfg`.

  Returns:
    base.BaseTask for the given config.
  """
  datasets = utils.get_image_dataset(cfg["dataset"])
  act_fn = utils.get_activation(cfg["activation"])
  w_init = utils.get_initializer(cfg["w_init"])

  def _build(batch):
    dist = dist_with_maf_bijectors(
        batch["image"],
        num_bijectors=cfg["num_bijectors"],
        layers=cfg["hidden_units"],
        activation=act_fn,
        w_init=w_init)
    return neg_log_p(dist, batch["image"])

  base_model_fn = lambda: snt.Module(_build)

  return base.DatasetModelTask(base_model_fn, datasets)
