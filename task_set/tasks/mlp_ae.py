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
"""MLP autoencoding task family."""

from typing import Dict, Text, Any
import numpy as np
import sonnet as snt

from task_set import registry
from task_set.tasks import base
from task_set.tasks import utils
import tensorflow.compat.v1 as tf

MLPAEConfig = Dict[Text, Any]


@registry.task_registry.register_sampler("mlp_ae_family")
def sample_mlp_ae_family_cfg(seed):
  """Sample a task config for a mlp autoencoder on image data.

  These configs are nested python structures that provide enough information
  to create an instance of the problem.

  Args:
    seed: int Random seed to generate task from.

  Returns:
    A nested dictionary containing a configuration.
  """
  rng = np.random.RandomState(seed)
  cfg = {}
  n_layers = rng.choice([1, 2, 3, 4, 5, 6])
  cfg["hidden_units"] = [
      utils.sample_log_int(rng, 32, 128) for _ in range(n_layers)
  ]

  cfg["activation"] = utils.sample_activation(rng)
  cfg["w_init"] = utils.sample_initializer(rng)

  cfg["dataset"] = utils.sample_image_dataset(rng)
  # Give relu double weight as this is what is often used in practice.
  cfg["output_type"] = rng.choice(
      ["tanh", "tanh", "sigmoid", "sigmoid", "linear_center", "linear"])

  # Give l2 double weight as this is often used
  cfg["loss_type"] = rng.choice(["l2", "l2", "l1"])

  cfg["reduction_type"] = rng.choice(["reduce_mean", "reduce_sum"])

  return cfg


@registry.task_registry.register_getter("mlp_ae_family")
def get_mlp_ae_family(cfg):
  """Get a task for the given cfg.

  Args:
    cfg: config specifying the model generated by `sample_mlp_ae_family_cfg`.

  Returns:
    base.BaseTask for the given config.
  """
  act_fn = utils.get_activation(cfg["activation"])
  w_init = utils.get_initializer(cfg["w_init"])
  init = {"w": w_init}

  datasets = utils.get_image_dataset(cfg["dataset"])

  def _build(batch):
    """Builds the sonnet module."""
    flat_img = snt.BatchFlatten()(batch["image"])

    if cfg["output_type"] in ["tanh", "linear_center"]:
      flat_img = flat_img * 2.0 - 1.0

    hidden_units = cfg["hidden_units"] + [flat_img.shape.as_list()[1]]
    mod = snt.nets.MLP(hidden_units, activation=act_fn, initializers=init)
    outputs = mod(flat_img)

    if cfg["output_type"] == "sigmoid":
      outputs = tf.nn.sigmoid(outputs)
    elif cfg["output_type"] == "tanh":
      outputs = tf.tanh(outputs)
    elif cfg["output_type"] in ["linear", "linear_center"]:
      # nothing to be done to the outputs
      pass
    else:
      raise ValueError("Invalid output_type [%s]." % cfg["output_type"])

    reduce_fn = getattr(tf, cfg["reduction_type"])
    if cfg["loss_type"] == "l2":
      loss_vec = reduce_fn(tf.square(outputs - flat_img), axis=1)
    elif cfg["loss_type"] == "l1":
      loss_vec = reduce_fn(tf.abs(outputs - flat_img), axis=1)
    else:
      raise ValueError("Unsupported loss_type [%s]." % cfg["reduction_type"])

    return tf.reduce_mean(loss_vec)

  return base.DatasetModelTask(lambda: snt.Module(_build), datasets)
