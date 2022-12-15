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

# Lint as: python3
# pylint:disable=line-too-long
r"""Beam job to map to tf.Examples of embeddings.

This file has two modes:
1) Map from tf.Examples of audio to tf.Examples of embeddings.
2) Map from TFDS dataseet to tf.Examples of embeddings.

"""
# pylint:enable=line-too-long

from typing import Any, Dict

from absl import app
from absl import flags
from absl import logging
import apache_beam as beam
import tensorflow as tf
from non_semantic_speech_benchmark.data_prep import audio_to_embeddings_beam_utils as utils

flags.DEFINE_string('input_glob', None,
                    'Glob for input dir. XOR with `tfds_data`.')
flags.DEFINE_string(
    'tfds_dataset', None, 'Name of TFDS dataset. '
    'XOR with `input_glob`. Should be of the form ex "cifar".'
    'Exactly one of `sample_rate_key`, `sample_rate`, or '
    '`tfds_dataset` must be not None.')
flags.DEFINE_string(
    'tfds_data_dir', None,
    'An optional directory for the locally downloaded TFDS data. Should only '
    'be non-None when `tfds_dataset` is used. This is essential for data that '
    'needs to be manually downloaded.')
flags.DEFINE_string('output_filename', None, 'Output filename.')
flags.DEFINE_list(
    'embedding_names', None,
    'List of embedding module names. Used for logging, and as '
    'in the features key of the results tf.Example feature list.')
flags.DEFINE_list(
    'embedding_modules', None,
    'List of embedding modules to compute. Should be accepted '
    'by `hub.load`.`')
flags.DEFINE_list(
    'module_output_keys', None,
    'List of module output key. Must be the same length as '
    '`embedding_modules`.')
flags.DEFINE_enum('data_prep_behavior', 'many_models', [
    'many_models', 'many_embeddings_single_model', 'chunked_audio',
    'batched_single_model'
], 'Which metric to compute and report.')
# Extra data prep flags, needed for `many_embeddings_single_model` and
# `chunked_audio`.
flags.DEFINE_integer('chunk_len', None, 'Optional chunk len')
# Extra data prep flags, needed just for `many_embeddings_single_model`.
flags.DEFINE_integer(
    'embedding_length', None,
    'Expected length of the embedding. If present, must be this length.')
# Extra data prep flags, needed just for `chunked_audio`.
flags.DEFINE_bool(
    'compute_embeddings_on_chunked_audio', True,
    'Whether to compute targets on chunked audio or entire clip.')
# Extra data prep flags, needed just for ``.
flags.DEFINE_integer('batch_size', 1,
                     'Number of audio samples to compute embeddings at once.')

flags.DEFINE_string(
    'comma_escape_char', '?',
    'Sometimes we want commas to appear in `embedding_modules`, '
    '`embedding_names`, or `module_output_key`. However, commas get split out '
    'in Googles Python `DEFINE_list`. We compromise by introducing a special '
    'character, which we replace with commas.')
flags.DEFINE_string('audio_key', None, 'Key of audio.')
flags.DEFINE_integer(
    'sample_rate', None, 'Sample rate.'
    'Exactly one of `sample_rate_key`, `sample_rate`, or '
    '`tfds_dataset` must be not None.')
flags.DEFINE_string(
    'sample_rate_key', None, 'Key of sample rate. '
    'Exactly one of `sample_rate_key`, `sample_rate`, or '
    '`tfds_dataset` must be not None.')
flags.DEFINE_string(
    'label_key', None, 'Key for labels. If the feature value is an integer, '
    'convert to bytes.')
flags.DEFINE_string(
    'speaker_id_key', None,
    'Key for speaker_id, or `None`. If this flag is present, '
    'check that the key exists and is of type `bytes`.')
flags.DEFINE_bool('average_over_time', False,
                  'If true, return embeddings that are averaged over time.')
flags.DEFINE_bool(
    'delete_audio_from_output', True,
    'If true, remove audio from the output table. Can be '
    'helpful in keeping output tables small.')
flags.DEFINE_bool(
    'split_embeddings_into_separate_tables', False,
    'If true, write each embedding to a separate table.')
flags.DEFINE_bool('debug', False, 'If True, run in debug model.')
# Do not use `use_frontend_fn` and `model_input_min_length > 0`.
flags.DEFINE_bool(
    'use_frontend_fn', False,
    'If `true`, call frontend fn on audio before passing to the model. Do not '
    'use if `model_input_min_length` is not `None`.')
flags.DEFINE_bool(
    'normalize_to_pm_one', True,
    'Whether to normalize input to +- 1 before passing to model.')
flags.DEFINE_integer(
    'model_input_min_length', None, 'Min length to the model. 0-pad inputs to '
    'this length, if necessary. Note that frontends usually contain their own '
    'length logic, unless the model is in TFLite format. Do not use if '
    '`use_frontend_fn` is `True`.')


FLAGS = flags.FLAGS


def main(_):

  input_filenames_list, output_filenames, beam_params = utils.get_beam_params_from_flags(
  )
  # Check that inputs and flags are formatted correctly.
  utils.validate_inputs(
      input_filenames_list=input_filenames_list,
      output_filenames=output_filenames,
      embedding_modules=beam_params['embedding_modules'],
      embedding_names=beam_params['embedding_names'],
      module_output_keys=beam_params['module_output_keys'])
  logging.info('main: input_filenames_list: %s', input_filenames_list)
  logging.info('main: output_filenames: %s', output_filenames)
  logging.info('main: beam_params: %s', beam_params)

  # If you have custom beam options, add them here.
  beam_options = None

  logging.info('Starting to create flume pipeline...')
  with beam.Pipeline(beam_options) as root:
    for i, (input_filenames_or_glob, output_filename) in enumerate(
        zip(input_filenames_list, output_filenames)):
      utils.data_prep_pipeline(
          root=root,
          input_filenames_or_glob=input_filenames_or_glob,
          output_filename=output_filename,
          data_prep_behavior=FLAGS.data_prep_behavior,
          beam_params=beam_params,
          suffix=str(i))


@flags.multi_flags_validator(
    ['use_frontend_fn', 'model_input_min_length'],
    message='Use only one of `use_frontend_fn` and `model_input_min_length`.'
)
def no_min_input_length_with_frontend_fn(flags_dict):
  return (not flags_dict['use_frontend_fn'] or
          not flags_dict['model_input_min_length'])

if __name__ == '__main__':
  flags.mark_flags_as_required([
      'output_filename', 'embedding_names', 'embedding_modules',
      'module_output_keys', 'audio_key',
  ])
  flags.mark_flags_as_mutual_exclusive(['input_glob', 'tfds_dataset'],
                                       required=True)
  flags.mark_flags_as_mutual_exclusive(
      ['tfds_dataset', 'sample_rate_key', 'sample_rate'], required=True)
  tf.compat.v2.enable_v2_behavior()
  assert tf.executing_eagerly()
  app.run(main)
