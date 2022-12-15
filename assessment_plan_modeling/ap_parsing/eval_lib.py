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

"""Evaluation of AP problems and action items annotation compared with labels."""

import dataclasses
import functools
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import sklearn.metrics

from assessment_plan_modeling.ap_parsing import ap_parsing_lib
from assessment_plan_modeling.ap_parsing import ap_parsing_utils
from assessment_plan_modeling.ap_parsing import tokenizer_lib

_MetricsDictType = Dict[str, Union[float, int]]


@dataclasses.dataclass
class TokenOverlap:
  overlap: int
  matched_pred_index: Optional[int] = None


def token_overlap_span(
    first_span,
    second_span):
  return (max(first_span.start_token, second_span.start_token),
          min(first_span.end_token, second_span.end_token))


def calc_metrics(tp, total_true, total_pred,
                 key_prefix):
  """Calculates the metrics.

  Calculates precision, recall, f1, jaccard
  and reports the base meassures TP, total true, total pred.

  Divide by zero silently returns np.nan.

  Args:
    tp: total number of correct hits
    total_true: total number of ground truth positives
    total_pred: total number of predicted positives
    key_prefix: prefix for dictionary key

  Returns:
    Metrics dict for the aforementioned metrics.
      Keys are given by prefix and metric name.
  """

  def _safe_div(nom, denom):
    return nom / denom if denom != 0 else np.nan

  precision_ = _safe_div(tp, total_pred)
  recall_ = _safe_div(tp, total_true)
  jaccard_ = _safe_div(tp, total_true + total_pred - tp)
  return {
      f"{key_prefix}/precision":
          precision_,
      f"{key_prefix}/recall":
          recall_,
      f"{key_prefix}/f1":
          _safe_div(2 * np.nan_to_num(precision_ * recall_),
                    np.nan_to_num(precision_) + np.nan_to_num(recall_)),
      f"{key_prefix}/jaccard":
          jaccard_,
      f"{key_prefix}/tp":
          tp,
      f"{key_prefix}/total_true":
          total_true,
      f"{key_prefix}/total_pred":
          total_pred,
  }


def _calculate_overlaps(
    truth_labeled_token_spans,
    predicted_labeled_token_spans,
    tokens):
  """Calculates the overlap between ground truth and predicted token spans.

  Args:
    truth_labeled_token_spans: Token spans of ground truth spans.
    predicted_labeled_token_spans: Token spans of predicted spans.
    tokens: List of token objects.

  Returns:
    list of TokenOverlap indexed by ground truth.
  """
  truth_token_overlaps = [
      TokenOverlap(0, False) for _ in truth_labeled_token_spans
  ]
  i_truth = 0
  i_pred = 0
  while i_truth < len(truth_labeled_token_spans) and i_pred < len(
      predicted_labeled_token_spans):
    # Current truth is beyond predicted, overlap of 0 (default), advance.
    if truth_labeled_token_spans[
        i_truth].start_token >= predicted_labeled_token_spans[i_pred].end_token:
      i_pred += 1
    # Current predicted is beyond truth, overlap of 0 (default), advance.
    elif predicted_labeled_token_spans[
        i_pred].start_token >= truth_labeled_token_spans[i_truth].end_token:
      i_truth += 1
    # Some overlap.
    else:
      overlap = ap_parsing_utils.token_span_size_nonspaces(
          token_overlap_span(truth_labeled_token_spans[i_truth],
                             predicted_labeled_token_spans[i_pred]),
          tokens=tokens)

      # When multiple predicted spans overlap a single truth span,
      # The hit is considered the biggest one.
      if overlap > truth_token_overlaps[i_truth].overlap:
        truth_token_overlaps[i_truth].overlap = overlap
        truth_token_overlaps[i_truth].matched_pred_index = i_pred

      i_pred += 1
  return truth_token_overlaps


def sorted_labeled_token_spans_by_type(
    labeled_token_spans,
    span_type
):
  return sorted([x for x in labeled_token_spans if x.span_type == span_type],
                key=lambda x: x.start_token)


def span_level_metrics(
    truth_token_overlaps, truth_token_span_sizes,
    predicted_token_span_sizes,
    span_type):
  """Calculates metrics for relaxed spans (some overlap).

  Args:
    truth_token_overlaps: List of TokenOverlap generated by _calculate_overlaps.
    truth_token_span_sizes: List of sizes of ground truth spans.
    predicted_token_span_sizes: List of sizes of predicted spans.
    span_type: Current LabeledSpanType.

  Returns:
    Metrics dict from precision_recall_f1_support.
  """
  # Span relaxed overlap - hit if overlap>0.
  return calc_metrics(
      tp=len([x for x in truth_token_overlaps if x.overlap > 0]),
      total_true=len(truth_token_span_sizes),
      total_pred=len(predicted_token_span_sizes),
      key_prefix=f"span_relaxed/{span_type.name}")


def token_level_metrics(
    truth_token_labels,
    pred_token_labels,
    token_mask,
    span_type,
):
  """Calculates metrics for token level.

  Args:
    truth_token_labels: Token level labels for ground truth.
    pred_token_labels: Token level labels for prediction.
    token_mask: True for tokens to include in the calculation (non-spaces)
    span_type: Current LabeledSpanType.

  Returns:
    Metrics dict from precision_recall_f1_support.
  """

  # Token level metrics
  return calc_metrics(
      tp=((truth_token_labels == pred_token_labels) &
          (truth_token_labels == span_type.value) & token_mask).sum(),
      total_true=((truth_token_labels == span_type.value) & token_mask).sum(),
      total_pred=((pred_token_labels == span_type.value) & token_mask).sum(),
      key_prefix=f"token_relaxed/{span_type.name}")


def _get_action_item_metrics(
    truth_token_overlaps,
    truth_labeled_token_spans,
    predicted_labeled_token_spans
):
  """Calculate metrics for action item type.

  Considered action item relaxed span matches with equal types as hits.

  Args:
    truth_token_overlaps: List of TokenOverlap generated by _calculate_overlaps.
    truth_labeled_token_spans: List of ground truth token spans.
    predicted_labeled_token_spans: List of predicted token spans.

  Returns:
    A dictionary with the metrics.
  """
  truth_action_item_labels = []
  pred_action_item_labels = []
  ai_metrics = {}
  for truth_labeled_token_span, overlap in zip(truth_labeled_token_spans,
                                               truth_token_overlaps):
    if overlap.overlap:
      truth_action_item_labels.append(truth_labeled_token_span.action_item_type)
      pred_action_item_labels.append(predicted_labeled_token_spans[
          overlap.matched_pred_index].action_item_type)

  pred_action_item_labels = np.array(pred_action_item_labels)
  truth_action_item_labels = np.array(truth_action_item_labels)
  tp = pred_action_item_labels == truth_action_item_labels

  # Calculate for every action item type (excluding UNSET):
  for action_item_type in list(ap_parsing_lib.ActionItemType)[1:]:
    ai_metrics.update(
        calc_metrics(
            tp=(tp & (pred_action_item_labels == action_item_type)).sum(),
            total_true=sum(x.action_item_type == action_item_type
                           for x in truth_labeled_token_spans),
            total_pred=sum(x.action_item_type == action_item_type
                           for x in predicted_labeled_token_spans),
            key_prefix=f"action_item_type/{action_item_type.name}"))

  ai_metrics.update({
      "action_item_type/ALL/confusion_matrix":
          sklearn.metrics.confusion_matrix(
              truth_action_item_labels,
              pred_action_item_labels,
              labels=np.arange(len(ap_parsing_lib.ActionItemType)))
  })
  return ai_metrics


def evaluate_from_labeled_token_spans(
    truth_labeled_token_spans,
    predicted_labeled_token_spans,
    tokens):
  """Evaluate predicted spans against ground truth by span type.

  Args:
    truth_labeled_token_spans: List of LabeledTokenSpan for ground truth spans.
    predicted_labeled_token_spans: List of LabeledTokenSpan for predicted spans.
    tokens: List of token objects.

  Returns:
    Metrics dict by span type and evaluation view.
  """
  metrics = {}

  def labeled_token_span_size_nonspaces(
      labeled_token_span):
    return ap_parsing_utils.token_span_size_nonspaces(
        (labeled_token_span.start_token, labeled_token_span.end_token),
        tokens=tokens)

  # Calculate token labels:
  truth_token_labels = np.zeros(len(tokens), dtype=np.int64)
  pred_token_labels = np.zeros(len(tokens), dtype=np.int64)
  token_mask = np.array(
      [token.token_type != tokenizer_lib.TokenType.SPACE for token in tokens])

  for spans, labels in [(truth_labeled_token_spans, truth_token_labels),
                        (predicted_labeled_token_spans, pred_token_labels)]:
    for span in spans:
      labels[span.start_token:span.end_token] = span.span_type.value

  # Calculate for every span type (excluding UNKNOWN):
  for span_type in list(ap_parsing_lib.LabeledSpanType)[1:]:
    metrics.update(
        token_level_metrics(truth_token_labels, pred_token_labels, token_mask,
                            span_type))

    # Calculate span level metrics:
    cur_truth_labeled_token_spans = sorted_labeled_token_spans_by_type(
        truth_labeled_token_spans, span_type=span_type)
    cur_predicted_labeled_token_spans = sorted_labeled_token_spans_by_type(
        predicted_labeled_token_spans, span_type=span_type)

    truth_token_overlaps = _calculate_overlaps(
        cur_truth_labeled_token_spans, cur_predicted_labeled_token_spans,
        tokens)
    truth_token_span_sizes = list(
        map(labeled_token_span_size_nonspaces, cur_truth_labeled_token_spans))
    predicted_token_span_sizes = list(
        map(labeled_token_span_size_nonspaces,
            cur_predicted_labeled_token_spans))

    metrics.update(
        span_level_metrics(truth_token_overlaps, truth_token_span_sizes,
                           predicted_token_span_sizes, span_type))

    # Calculate action item type metrics:
    if span_type == ap_parsing_lib.LabeledSpanType.ACTION_ITEM:
      metrics.update(
          _get_action_item_metrics(truth_token_overlaps,
                                   cur_truth_labeled_token_spans,
                                   cur_predicted_labeled_token_spans))
  return metrics


def evaluate_from_labeled_char_spans(
    truth_labeled_char_spans,
    predicted_labeled_char_spans,
    tokens):
  """Evaluate predicted spans against ground truth by span type.

  Args:
    truth_labeled_char_spans: List of LabeledSpans for ground truth spans.
    predicted_labeled_char_spans: List of LabeledSpans for predicted spans.
    tokens: List of token objects.

  Returns:
    Metrics dict by span type and evaluation view.
  """

  converter = functools.partial(
      ap_parsing_utils.labeled_char_span_to_labeled_token_span, tokens=tokens)
  truth_labeled_token_spans = sorted(
      map(converter, truth_labeled_char_spans),
      key=lambda token_span: token_span.start_token)
  predicted_labeled_token_spans = sorted(
      map(converter, predicted_labeled_char_spans),
      key=lambda token_span: token_span.start_token)

  return evaluate_from_labeled_token_spans(truth_labeled_token_spans,
                                           predicted_labeled_token_spans,
                                           tokens)
