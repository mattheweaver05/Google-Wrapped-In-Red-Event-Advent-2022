// Copyright 2022 The Google Research Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * This file contains the JavaScript code for MQM Viewer.
 */

/**
 * Raw data read from the data file. Each entry is an array with 10 entries,
 * in this order (slightly different from the original order in the TSV data):
 *
 *     0: system, 1: doc, 2: doc_seg_id, 3: global_seg_id, 4: source, 5: target,
 *     6: rater, 7: category, 8: severity, 9: metadata
 *
 * The doc_seg_id field is the 1-based index of the segment within the doc.
 *
 * The global_seg_id field is an arbitrary, application-specific segment
 * identifier.
 *
 * The last field, "metadata", is an object that includes the timestamp of
 * the rating, any note the rater may have left, and other metadata.
 *
 * There is a special severity, "HOTW-test", reserved for hands-on-the-wheel
 *   test results. These are test sentences in which a deliberate error is
 *   injected, just to help the rater stay on task. The test result is captured
 *   by category = "Found" or "Missed" and there is a note in the metadata that
 *   captures the injected error.
 */
let mqmData = [];

/**
 * If TSV data was supplied (instead of being chosen from a file), then it is
 * saved here (for possible downloading).
 */
let mqmTSVData = '';

/** Stats computed for current filtered data. **/
let mqmStats = {};
let mqmStatsBySystem = {};
let mqmStatsByRater = {};
let mqmStatsBySevCat = {};

/** Max number of segments to show. **/
let mqmLimit = 200;

/** Clause built by helper menus, for appending to the filter expression **/
let mqmClause = '';

/** UI elements for clause-builder.  **/
let mqmClauseKey;
let mqmClauseInclExcl;
let mqmClauseSev;
let mqmClauseCat;
let mqmClauseAddAnd;
let mqmClauseAddOr;

/** A distinctive name used as the key for aggregate stats. */
const mqmTotal = '_MQM_TOTAL_';

/**
 * Bootstrap sampling is used to compute 95% confidence intervals.
 * Currently only system MQM scores are supported.
 * Samples are obtained incrementally, i.e., each `mqmShowCI` call samples
 * a given number of times until 1000 samples are collected.
 */
/** Total Number of document-level samples to collect. */
const mqmNumSamples = 1000;

/**
 * Number of document-level samples per `mqmShowCI` call.
 * Make sure that this number can divide `mqmNumSamples`.
 */
const mqmNumSamplesPerCall = 200;

/**
 * Document-level info used for bootstrap sampling.
 * This is keyed by the system name.
 */
let mqmDocs = {};

/**
 * Bootstrap samples already collected by previous calls.
 * This is keyed by the system name.
 */
let mqmSampledScores = {};

/**
 * This stores the return from `setTimeout` call for incrementally obtaining
 * bootstrap samples.
 */
let mqmCIComputation = null;

/**
 * Listener for changes to the input field that specifies the limit on
 * the number of rows shown.
 */
function setMqmLimit() {
  const limitElt = document.getElementById('mqm-limit');
  const limit = limitElt.value.trim();
  if (limit > 0) {
    mqmLimit = limit;
    mqmShow();
  } else {
    limitElt.value = mqmLimit;
  }
}

/**
 * This sorts mqmData by fields in the order shown in the comments below.
 */
function mqmSort() {
  mqmData.sort((e1, e2) => {
    let diff = e1[3] - e2[3]; /** global_seg_id **/
    if (diff == 0) {
      diff = e1[2] - e2[2]; /** doc_seg_id **/
      if (diff == 0) {
        if (e1[1] < e2[1]) {  /** doc **/
          diff = -1;
        } else if (e1[1] > e2[1]) {
          diff = 1;
        } else if (e1[0] < e2[0]) {  /** system **/
          diff = -1;
        } else if (e1[0] > e2[0]) {
          diff = 1;
        } else if (e1[6] < e2[6]) {  /** rater **/
          diff = -1;
        } else if (e1[6] > e2[6]) {
          diff = 1;
        } else if (e1[8] < e2[8]) {  /** severity **/
          diff = -1;
        } else if (e1[8] > e2[8]) {
          diff = 1;
        } else if (e1[7] < e2[7]) {  /** category **/
          diff = -1;
        } else if (e1[7] > e2[7]) {
          diff = 1;
        }
      }
    }
    return diff;
  });
}

/**
 * If obj does not have an array property named key, creates an empty array.
 * Pushes val into the obj[key] array.
 * @param {!Object} obj
 * @param {string} key
 * @param {string} val
 */
function mqmAddToArray(obj, key, val) {
  if (!obj.hasOwnProperty(key)) obj[key] = [];
  obj[key].push(val);
}

/**
 * Aggregates mqmData, collecting all data for a particular segment translation
 *     (i.e., for a given (doc, doc_seg_id, global_seg_id) triple) into a
 *     "segment" object that has the following properties:
 *         {cats,sevs,sevcats}_by_{rater,system}.
 *     Each of these properties is an object keyed by system or rater, with the
 *     values being arrays of strings that are categories, severities,
 *     and <sev>[/<cat>], * respectively.
 *
 * Appends each aggregated segment object as the last column (index 10) to each
 *     mqmData[*] array for that segment.
 */
function mqmAddSegmentAggregations() {
  let segment = null;
  let curr_doc = '';
  let curr_doc_seg_id = -1;
  let curr_global_seg_id = -1;
  let curr_start = -1;
  for (let i = 0; i < mqmData.length; i++) {
    const parts = mqmData[i];
    const system = parts[0];
    const doc = parts[1];
    const doc_seg_id = parts[2];
    const global_seg_id = parts[3];
    const rater = parts[6];
    const category = parts[7];
    const severity = parts[8];
    if (curr_doc == doc && curr_doc_seg_id == doc_seg_id &&
        curr_global_seg_id == global_seg_id) {
      console.assert(segment != null, i);
    } else {
      if (segment != null) {
        console.assert(curr_start >= 0, segment);
        for (let j = curr_start; j < i; j++) {
          mqmData[j].push(segment);
        }
      }
      segment = {
        'cats_by_system': {},
        'cats_by_rater': {},
        'sevs_by_system': {},
        'sevs_by_rater': {},
        'sevcats_by_system': {},
        'sevcats_by_rater': {},
      };
      curr_doc = doc;
      curr_doc_seg_id = doc_seg_id;
      curr_global_seg_id = global_seg_id;
      curr_start = i;
    }
    mqmAddToArray(segment.cats_by_system, system, category);
    mqmAddToArray(segment.cats_by_rater, rater, category);
    mqmAddToArray(segment.sevs_by_system, system, severity);
    mqmAddToArray(segment.sevs_by_rater, rater, severity);
    const sevcat = severity + (category ? '/' + category : '');
    mqmAddToArray(segment.sevcats_by_system, system, sevcat);
    mqmAddToArray(segment.sevcats_by_rater, rater, sevcat);
  }
  if (segment != null) {
    console.assert(curr_start >= 0, segment);
    for (let j = curr_start; j < mqmData.length; j++) {
      mqmData[j].push(segment);
    }
  }
}

/**
 * Returns an array of column filter REs.
 * @return {!Array<!RegExp>}
 */
function mqmGetFilterREs() {
  const res = [];
  const filters = document.getElementsByClassName('mqm-filter-re');
  for (let i = 0; i < filters.length; i++) {
    const filter = filters[i].value.trim();
    const selectId = filters[i].id.replace(/filter/, 'select');
    const sel = document.getElementById(selectId);
    if (sel) sel.value = filter;
    if (!filter) {
      res.push(null);
      continue;
    }
    const re = new RegExp(filter);
    res.push(re);
  }
  return res;
}

/**
 * Retains only the marked part in a segment, replacing the parts before/after
 *     (if they exist) with ellipsis. Used to show just the marked parts for
 *     source/target text segments when the full text has already been shown
 *     previously.
 * @param {string} s
 * @return {string}
 */
function mqmOnlyKeepSpans(s) {
  const start = s.indexOf('<span');
  const end = s.lastIndexOf('</span>');
  if (start >= 0 && end >= 0) {
    let sub = s.substring(start, end + 7);
    const MAX_CTX = 10;
    if (start > 0) {
      const ctx = Math.min(MAX_CTX, start);
      sub = s.substr(start - ctx, ctx) + sub;
      if (ctx < start) sub = '&hellip;' + sub;
    }
    if (end + 7 < s.length) {
      const ctx = Math.min(MAX_CTX, s.length - (end + 7));
      sub = sub + s.substr(end + 7, ctx);
      if (end + 7 + ctx < s.length) sub = sub + '&hellip;';
    }
    return sub;
  } else {
    return '&hellip;';
  }
}

/**
 * Short-cut function (convenient in filter expressions) for:
 *     "obj has array property prop that includes val"
 * @param {!Object} obj
 * @param {string} prop
 * @param {string} val
 * @return {boolean}
 */
function mqmIncl(obj, prop, val) {
  return obj.hasOwnProperty(prop) &&
    obj[prop].includes(val);
}

/**
 * Short-cut function (convenient in filter expressions) for:
 *     "obj has array property prop that excludes val"
 * @param {!Object} obj
 * @param {string} prop
 * @param {string} val
 * @return {boolean}
 */
function mqmExcl(obj, prop, val) {
  return obj.hasOwnProperty(prop) &&
    !obj[prop].includes(val);
}

/**
 * Clears mqmClause and disables associated buttons.
 */
function mqmClearClause() {
  mqmClause = '';
  mqmClauseKey.value = '';
  mqmClauseInclExcl.value = 'includes';
  mqmClauseSev.value = '';
  mqmClauseCat.value = '';
  mqmClauseAddAnd.disabled = true;
  mqmClauseAddOr.disabled = true;
}

/**
 * Checks if filter expression clause is fully specified, enables "add"
 *     buttons if so.
 */
function mqmCheckClause() {
  mqmClause = '';
  mqmClauseAddAnd.disabled = true;
  mqmClauseAddOr.disabled = true;
  if (!mqmClauseKey.value) return;
  if (!mqmClauseSev.value && !mqmClauseCat.value) return;

  let sevcats = 'segment.sevcats';
  let key = '';
  let err = mqmClauseSev.value + '/' + mqmClauseCat.value;
  if (!mqmClauseSev.value) {
    sevcats = 'segment.cats';
    err = mqmClauseCat.value;
  }
  if (!mqmClauseCat.value) {
    sevcats = 'segment.sevs';
    err = mqmClauseSev.value;
  }
  if (mqmClauseKey.value.startsWith('System: ')) {
    sevcats += '_by_system';
    key = mqmClauseKey.value.substr(8);
  } else {
    console.assert(mqmClauseKey.value.startsWith('Rater: '),
                   mqmClauseKey.value);
    sevcats += '_by_rater';
    key = mqmClauseKey.value.substr(7);
  }
  const inclexcl = (mqmClauseInclExcl.value == 'excludes') ? 'mqmExcl' :
      'mqmIncl';
  mqmClause = `${inclexcl}(${sevcats}, "${key}", "${err}")`;
  mqmClauseAddAnd.disabled = false;
  mqmClauseAddOr.disabled = false;
}

/**
 * Adds mqmClause with and/or to the filter expression.
 * @param {string} andor
 */
function mqmAddClause(andor) {
  if (!mqmClause) return;
  const elt = document.getElementById('mqm-filter-expr');
  let expr = elt.value.trim();
  if (expr) expr += ' ' + andor + ' ';
  expr += mqmClause;
  elt.value = expr;
  mqmClearClause();
  mqmShow();
}

/**
 * Evaluates the JavaScript filter_expr on an mqmData[] row and returns true
 *     only if the filter passes.
 * @param {string} filter_expr
 * @param {!Array<string>} parts
 * @return {boolean}
 */
function mqmFilterExprPasses(filter_expr, parts) {
  if (!filter_expr.trim()) return true;
  try {
    return Function(
        '"use strict";' +
        `
    const system = arguments[0];
    const doc = arguments[1];
    const doc_seg_id = arguments[2];
    const global_seg_id = arguments[3];
    const source = arguments[4];
    const target = arguments[5];
    const rater = arguments[6];
    const category = arguments[7];
    const severity = arguments[8];
    const metadata = arguments[9];
    const segment = arguments[10];` +
        'return (' + filter_expr + ')')(
        parts[0], parts[2], parts[2], parts[3], parts[4], parts[5], parts[6],
        parts[7], parts[8], parts[9], parts[10]);
  } catch (err) {
    document.getElementById('mqm-filter-expr-error').innerHTML = err;
    return false;
  }
}

/**
 * Initializes and returns a rater object.
 * @param {string} rater
 * @return {!Object}
 */
function mqmInitRaterStats(rater) {
  return {
    'rater': rater,
    'major': 0,
    'majorA': 0,
    'majorF': 0,
    'majorUncat': 0,
    'minor': 0,
    'minorA': 0,
    'minorF': 0,
    'minorUncat': 0,
    'trivial': 0,
    'nonTrans': 0,
    'unrateable': 0,
    'score': 0,
    'scoreMajor': 0,
    'scoreMinor': 0,
    'scoreAccuracy': 0,
    'scoreFluency': 0,
    'scoreUncat': 0,
    /* scoreMajor + scoreMinor = scoreAccuracy + scoreFluency + scoreUncat */
    'scoreNT': 0,
    'scoreTrivial': 0,
    /* score = scoreMajor + scoreMinor + scoreNT + scoreTrivial */

    'errorSpans': 0,
    'numWithErrors': 0,

    'hotwFound': 0,
    'hotwMissed': 0,
  };
}

/**
 * Appends stats from delta into raterStats.
 * @param {!Object} raterStats
 * @param {!Object} delta
 */
function mqmAddRaterStats(raterStats, delta) {
  raterStats.major += delta.major;
  raterStats.majorA += delta.majorA;
  raterStats.majorF += delta.majorF;
  raterStats.majorUncat += delta.majorUncat;

  raterStats.minor += delta.minor;
  raterStats.minorA += delta.minorA;
  raterStats.minorF += delta.minorF;
  raterStats.minorUncat += delta.minorUncat;

  raterStats.trivial += delta.trivial;
  raterStats.nonTrans += delta.nonTrans;
  raterStats.unrateable += delta.unrateable;

  raterStats.errorSpans += delta.errorSpans;
  raterStats.numWithErrors += delta.numWithErrors;

  raterStats.hotwFound += delta.hotwFound;
  raterStats.hotwMissed += delta.hotwMissed;

  raterStats.score += delta.score;
  raterStats.scoreMajor += delta.scoreMajor;
  raterStats.scoreMinor += delta.scoreMinor;
  raterStats.scoreAccuracy += delta.scoreAccuracy;
  raterStats.scoreFluency += delta.scoreFluency;
  raterStats.scoreUncat += delta.scoreUncat;

  raterStats.scoreNT += delta.scoreNT;
  raterStats.scoreTrivial += delta.scoreTrivial;
}

/**
 * Divides all metrics in raterStats by num.
 * @param {!Object} raterStats
 * @param {number} num
 */
function mqmAvgRaterStats(raterStats, num) {
  if (!num) return;
  raterStats.score /= num;
  raterStats.scoreMajor /= num;
  raterStats.scoreMinor /= num;
  raterStats.scoreAccuracy /= num;
  raterStats.scoreFluency /= num;
  raterStats.scoreUncat /= num;
  raterStats.scoreNT /= num;
  raterStats.scoreTrivial /= num;
}

/**
 * Aggregates segment stats. This returns a two-element array:
 * an object representing the aggregates, and the total number of ratings.
 * @param {!Array} segs
 * @return {!Array}
 */
function mqmAggregateSegStats(segs) {
    aggregates = mqmInitRaterStats('');
    ratings = 0;
    if (!segs || !segs.length) {
      aggregates.score = Infinity;
      return [aggregates, ratings];
    }
    for (let raterStats of segs) {
      const allRaterStats = mqmInitRaterStats('');
      for (let r of raterStats) {
        r.scoreMajor = r.major * 5.0;
        r.scoreMinor = r.minor;
        r.scoreNT = r.nonTrans * 25.0;
        r.scoreTrivial = r.trivial * 0.1;
        r.score = r.scoreMajor + r.scoreMinor + r.scoreNT + r.scoreTrivial;
        r.scoreAccuracy = (r.majorA * 5.0) + r.minorA;
        r.scoreFluency = (r.majorF * 5.0) + r.minorF;
        r.scoreUncat = (r.majorUncat * 5.0) + r.minorUncat;
        mqmAddRaterStats(allRaterStats, r);
      }
      mqmAvgRaterStats(allRaterStats, raterStats.length);
      ratings += raterStats.length;
      mqmAddRaterStats(aggregates, allRaterStats);
    }
    mqmAvgRaterStats(aggregates, segs.length);
    return [aggregates, ratings];
}

/**
 * Samples from [0, max) for a specified number of times.
 * @param {number} max
 * @param {number} size
 * @return {!Array}
 */
function getRandomInt(max, size) {
  let samples = [];
  for (let i = 0; i < size; i++) {
    samples.push(Math.floor(Math.random() * max));
  }
  return samples;
}


/**
 * Prepare the document-level info prior to sampling.
 * For each document, we only need to keep track of two stats:
 * 1. The total number of segments in the document;
 * 2. The MQM scores (averaged over the number of segments).
 * These two stats are later used to compute a weighted average of MQM scores
 * to take into account the different total number of segments when we use
 * document-level sampling.
 * The input `stats_by_system` is an mqmStats* object keyed by the system name.
 * @param {!Object} stats_by_system
 */
function mqmPrepareDocScores(stats_by_system) {
  mqmDocs = {};
  for (system of Object.keys(stats_by_system)) {
    mqmDocs[system] = [];
    for (let doc of Object.values(stats_by_system[system])) {
      let segs_in_doc = Object.values(doc);
      let [a, r] = mqmAggregateSegStats(segs_in_doc);
      mqmDocs[system].push(
          {'score': a.score, 'num_segments': segs_in_doc.length});
    }
  }
}

/**
 * Implements the core logic to incrementally obtain bootstrap samples and
 * show confidence intervals. Each call will obtain `mqmNumberSamplesPerCall`
 * document-level samples. At the end of the call, CIs are shown if all samples
 * have been collected. Otherwise, call again to collect more.
 * The input `systems` is a (sorted) array of system names, in the same order
 * as rendered in HTML.
 * @param {!Array} systems
 */
function mqmShowCI(systems) {
  for (system of systems) {
    if (!mqmSampledScores.hasOwnProperty(system)) {
      mqmSampledScores[system] = [];
    }
    const docs = mqmDocs[system];
    for (let i = 0; i < mqmNumSamplesPerCall; i++) {
      let indices = getRandomInt(docs.length, docs.length);
      let score = 0.0;
      let num_segments = 0;
      for (let index of indices) {
        let doc = docs[index];
        score += doc['score'] * doc['num_segments'];
        num_segments += doc['num_segments'];
      }
      mqmSampledScores[system].push(score / num_segments);
    }
  }

  if (Object.values(mqmSampledScores)[0].length < mqmNumSamples) {
    // We need to collect more samples.
    mqmCIComputation = setTimeout(mqmShowCI, 200, systems);
  } else {
    // We can now show the confidence intervals.
    const lower_idx = mqmNumSamples / 40;
    const upper_idx = mqmNumSamples - lower_idx - 1;
    for (let [row_idx, system] of systems.entries()) {
      mqmSampledScores[system].sort();
      const lb = mqmSampledScores[system][lower_idx];
      const ub = mqmSampledScores[system][upper_idx];
      const ci = `${lb.toFixed(3)} - ${ub.toFixed(3)}`;
      const span_id = `mqm-ci-${row_idx}`;
      const ci_span = document.getElementById(span_id);
      ci_span.insertAdjacentHTML('beforeend', ` (${ci})`);
    }
    mqmClearCIComputation();
  }
}

/**
 * Clears all computed confidence interval-related information.
 */
function mqmClearCIComputation() {
  mqmCIComputation = null;
  mqmDocs = {};
  mqmSampledScores = {};
}

/**
 * Appends MQM score details from the stats object to the table with the given
 * id.
 * @param {string} id
 * @param {string} title
 * @param {!Object} stats
 */
function mqmShowSegmentStats(id, title, stats) {
  const tbody = document.getElementById(id);
  if (title) {
    tbody.insertAdjacentHTML('beforeend',
      '<tr><td colspan="13"><hr></td></tr>' +
      `<tr><td colspan="13"><b>${title}</b></td></tr>\n`);
  }
  const keys = Object.keys(stats);
  const aggregates = {};
  const ratings = {};
  for (let k of keys) {
    const segs = mqmGetSegStatsAsArray(stats[k]);
    let [a, r] = mqmAggregateSegStats(segs);
    aggregates[k] = a;
    ratings[k] = r;
  }
  keys.sort((k1, k2) => aggregates[k1].score - aggregates[k2].score);
  for (let [row_idx, k] of keys.entries()) {
    const segs = mqmGetSegStatsAsArray(stats[k]);
    const k_disp = (k == mqmTotal) ? 'Total' : k;
    let rowHTML = `<tr><td>${k_disp}</td><td>${segs.length}</td>` +
        `<td>${ratings[k]}</td>`;
    if (!segs || !segs.length || !ratings[k]) {
      for (let i = 0; i < 10; i++) {
        rowHTML += '<td>-</td>';
      }
    } else {
      // Obtain confidence intervals for each system MQM score.
      for (let s of ['score', 'scoreNT', 'scoreMajor', 'scoreMinor',
                     'scoreTrivial', 'scoreAccuracy', 'scoreFluency',
                     'scoreUncat']) {
        let content = aggregates[k][s].toFixed(3);
        /**
         * Only show confidence intervals for system-level MQM scores when
         * there are at least 5 documents after filtering.
         * Otherwise, show N/A instead.
         */
        if (title == 'By system' && s == 'score') {
          if (Object.keys(stats[k]).length >= 5) {
            /**
             * Insert placeholder for the CI span. Span id is determined by
             * the order the systems are rendered in HTML. In this case, systems
             * are sorted by MQM score.
             */
            const span_id = `mqm-ci-${row_idx}`;
            content += `<span class="mqm-ci" id=${span_id}></span>`;
          } else {
            content += `<span class="mqm-ci"> (N/A)</span>`;
          }
        }
        rowHTML += `<td>${content}</td>`;
      }
      let errorSpan = 0;
      if (aggregates[k].numWithErrors > 0) {
        errorSpan = aggregates[k].errorSpans / aggregates[k].numWithErrors;
      }
      rowHTML += `<td>${(errorSpan).toFixed(1)}</td>`;
      const hotw = aggregates[k].hotwFound + aggregates[k].hotwMissed;
      if (hotw > 0) {
        const perc = ((aggregates[k].hotwFound * 100.0) / hotw).toFixed(1);
        rowHTML += `<td>${aggregates[k].hotwFound}/${hotw} (${perc}%)</td>`;
      } else {
        rowHTML += '<td>-</td>';
      }
    }
    rowHTML += '</tr>\n';
    tbody.insertAdjacentHTML('beforeend', rowHTML);
  }
  // Incrementally collect samples and show confidence intervals.
  if (title == 'By system') {
    mqmPrepareDocScores(stats);
    mqmShowCI(keys);
  }
}

/**
 * Shows details of severity- and category-wise scores (from the
 *   mqmStatsBySevCat object) in the categories table.
 */
function mqmShowSevCatStats() {
  const stats = mqmStatsBySevCat;
  const systems = {};
  for (let severity in stats) {
    for (let category in stats[severity]) {
      for (let system in stats[severity][category]) {
        systems[system] = true;
      }
    }
  }
  const systemsList = Object.keys(systems);
  const colspan = systemsList.length || 1;
  const th = document.getElementById('mqm-sevcat-stats-th');
  th.colSpan = colspan;

  systemsList.sort();
  const tbody = document.getElementById('mqm-sevcat-stats-tbody');

  let rowHTML = '<tr><td></td><td></td><td></td>';
  for (let system of systemsList) {
    rowHTML += `<td><b>${system == mqmTotal ? 'Total' : system}</b></td>`;
  }
  rowHTML += '</tr>\n';
  tbody.insertAdjacentHTML('beforeend', rowHTML);

  const sevKeys = Object.keys(stats);
  sevKeys.sort();
  for (let severity of sevKeys) {
    tbody.insertAdjacentHTML(
        'beforeend', `<tr><td colspan="${3 + colspan}"><hr></td></tr>`);
    const sevStats = stats[severity];
    const catKeys = Object.keys(sevStats);
    catKeys.sort((k1, k2) => sevStats[k2][mqmTotal] - sevStats[k1][mqmTotal]);
    for (let category of catKeys) {
      const row = sevStats[category];
      let rowHTML = `<tr><td>${severity}</td><td>${category}</td><td></td>`;
      for (let system of systemsList) {
        const val = row.hasOwnProperty(system) ? row[system] : '';
        rowHTML += `<td>${val ? val : ''}</td>`;
      }
      rowHTML += '</tr>\n';
      tbody.insertAdjacentHTML('beforeend', rowHTML);
    }
  }
}

/**
 * Shows all the stats.
 */
function mqmShowStats() {
  mqmShowSegmentStats('mqm-stats-tbody', '', mqmStats);
  mqmShowSegmentStats('mqm-stats-tbody', 'By system', mqmStatsBySystem);
  mqmShowSegmentStats('mqm-stats-tbody', 'By rater', mqmStatsByRater);
  mqmShowSevCatStats();
}

/**
 * Increments the counts statsArray[severity][category][system] and
 *   statsArray[severity][category][mqmTotal].
 * @param {!Object} statsArray
 * @param {string} system
 * @param {string} category
 * @param {string} severity
 */
function mqmAddSevCatStats(statsArray, system, category, severity) {
  if (!statsArray.hasOwnProperty(severity)) {
    statsArray[severity] = {};
  }
  if (!statsArray[severity].hasOwnProperty(category)) {
    statsArray[severity][category] = {};
    statsArray[severity][category][mqmTotal] = 0;
  }
  if (!statsArray[severity][category].hasOwnProperty(system)) {
    statsArray[severity][category][system] = 0;
  }
  statsArray[severity][category][mqmTotal]++;
  statsArray[severity][category][system]++;
}

/**
 * Given a lowercase severity (lsev) & category (lcat), returns true if it is
 *   the "Non-translation" error, allowing for underscore/dash variation and
 *   a possible trailing exclamation mark. Non-translation may have been marked
 *   as a severity or as a category.
 * @param {string} lsev
 * @param {string} lcat
 * @return {boolean}
 */
function mqmIsNonTrans(lsev, lcat) {
  return lsev.startsWith('non-translation') ||
    lsev.startsWith('non_translation') ||
    lcat.startsWith('non-translation') ||
    lcat.startsWith('non_translation');
}

/**
 * Given a lowercase category (lcat), returns true if it is an accuracy error.
 * @param {string} lcat
 * @return {boolean}
 */
function mqmIsAccuracy(lcat) {
  return lcat.startsWith('accuracy') || lcat.startsWith('terminology');
}

/**
 * Given text containing marked spans, returns the length of the spanned parts.
 * @param {string} s
 * @return {number}
 */
function mqmSpanLength(s) {
  let offset = 0;
  let span = 0;
  let index = 0;
  while ((index = s.indexOf('<span class="mqm-m', offset)) >= offset) {
    offset = index + 1;
    let startSpan = s.indexOf('>', offset);
    if (startSpan < 0) break;
    startSpan += 1;
    const endSpan = s.indexOf('</span>', startSpan);
    if (endSpan < 0) break;
    console.assert(startSpan <= endSpan, startSpan, endSpan);
    span += (endSpan - startSpan);
    offset = endSpan + 7;
  }
  return span;
}

/**
 * Updates stats with an error of (category, severity).
 * @param {!Object} stats
 * @param {string} category
 * @param {string} severity
 * @param {number} span
 */
function mqmAddErrorStats(stats, category, severity, span) {
  const lcat = category.toLowerCase().trim();
  if (lcat == 'no-error' || lcat == 'no_error') {
    return;
  }

  const lsev = severity.toLowerCase().trim();
  if (lsev == 'hotw-test' || lsev == 'hotw_test') {
    if (lcat == 'found') {
      stats.hotwFound++;
    } else if (lcat == 'missed') {
      stats.hotwMissed++;
    }
    return;
  }
  if (lsev == 'unrateable') {
    stats.unrateable++;
    return;
  }
  if (lsev == 'neutral') {
    return;
  }

  if (span > 0) {
    /* There is a scoreable error span.  */
    stats.numWithErrors++;
    stats.errorSpans += span;
  }

  if (mqmIsNonTrans(lsev, lcat)) {
    stats.nonTrans++;
    return;
  }
  if (lsev == 'trivial' ||
      (lsev == 'minor' && lcat.startsWith('fluency/punctuation'))) {
    stats.trivial++;
    return;
  }
  if (lsev == 'major') {
    stats.major++;
    if (!lcat || lcat == 'other') {
      stats.majorUncat++;
    } else if (mqmIsAccuracy(lcat)) {
      stats.majorA++;
    } else {
      stats.majorF++;
    }
  } else if (lsev == 'minor') {
    stats.minor++;
    if (!lcat || lcat == 'other') {
      stats.minorUncat++;
    } else if (mqmIsAccuracy(lcat)) {
      stats.minorA++;
    } else {
      stats.minorF++;
    }
  }
}

/**
 * Returns the last element of array a.
 * @param {!Array<T>} a
 * @return {T}
 * @template T
 */
function mqmArrayLast(a) {
  return a[a.length - 1];
}

/**
 * Returns the segment stats keyed by doc and doc_seg_id. This will
 * create an empty array if the associated segment stats array doesn't exist.
 * @param {!Object} stats_by_doc_and_doc_seg_id
 * @param {string} doc
 * @param {string} doc_seg_id
 * @return {!Array}
 */
function mqmGetSegStats(stats_by_doc_and_doc_seg_id, doc, doc_seg_id) {
  if (!stats_by_doc_and_doc_seg_id.hasOwnProperty(doc)) {
    stats_by_doc_and_doc_seg_id[doc] = {};
  }
  if (!stats_by_doc_and_doc_seg_id[doc].hasOwnProperty(doc_seg_id)) {
    stats_by_doc_and_doc_seg_id[doc][doc_seg_id] = [];
  }
  return stats_by_doc_and_doc_seg_id[doc][doc_seg_id];
}

/**
 * Flattens the nested stats object into an array of segment stats.
 * @param {!Object} stats_by_doc_and_doc_seg_id
 * @return {!Array}
 */
function mqmGetSegStatsAsArray(stats_by_doc_and_doc_seg_id) {
  let arr = [];
  for (let doc of Object.keys(stats_by_doc_and_doc_seg_id)) {
    let stats_by_doc_seg_id = stats_by_doc_and_doc_seg_id[doc];
    for (let doc_seg_id of Object.keys(stats_by_doc_seg_id)) {
      arr.push(stats_by_doc_seg_id[doc_seg_id]);
    }
  }
  return arr;
}

/**
 * Updates the display to show the segment data and scores according to the
 * current filters.
 */
function mqmShow() {
  document.body.style.cursor = 'wait';

  // Cancel existing CI computation when a new `mqmShow` is called.
  if (mqmCIComputation) {
    clearTimeout(mqmCIComputation);
    mqmClearCIComputation();
  }

  const tbody = document.getElementById('mqm-tbody');
  tbody.innerHTML = '';
  document.getElementById('mqm-stats-tbody').innerHTML = '';
  document.getElementById('mqm-sevcat-stats-tbody').innerHTML = '';

  /**
   * The following mqmStats* objects are all keyed by something from:
   * ({mqmTotal} or {system} or {rater}). Each keyed object is itself an object
   * mapping from doc and doc_seg_id to an entry representing
   * the information for that segment. For instance, let `x` be the keyed
   * object, then `x["1"]["2"]` is the entry for the segment with doc == "1" and
   * doc_seg_id == "2". Each entry for a segment is itself an array, one
   * entry per rater. Each  entry for a rater is an object tracking scores,
   * errors, and their breakdowns.
   */
  mqmStats = {};
  mqmStats[mqmTotal] = {};
  mqmStatsBySystem = {};
  mqmStatsByRater = {};

  mqmStatsBySevCat = {};

  let shown = 0;
  const filter_expr = document.getElementById('mqm-filter-expr').value.trim();
  document.getElementById('mqm-filter-expr-error').innerHTML = '';
  const filters = document.getElementsByClassName('mqm-filter-re');
  const filterREs = mqmGetFilterREs();
  let lastRow = null;
  let currSegStats = [];
  let currSegStatsBySys = [];
  let currSegStatsByRater = [];
  for (let rowId = 0; rowId < mqmData.length; rowId++) {
    const parts = mqmData[rowId];
    let match = true;
    for (let i = 0; i < 9; i++) {
      if (filterREs[i] && !filterREs[i].test(parts[i])) {
        match = false;
        break;
      }
    }
    if (!match) {
      continue;
    }
    if (!mqmFilterExprPasses(filter_expr, parts)) {
      continue;
    }
    const system = parts[0];
    const rater = parts[6];
    let sameAsLast = lastRow && (system == lastRow[0]) &&
      (parts[1] == lastRow[1]) && (parts[2] == lastRow[2]) &&
      (parts[3] == lastRow[3]);

    const doc = parts[1];
    const doc_seg_id = parts[2];
    if (!sameAsLast) {
      currSegStats = mqmGetSegStats(mqmStats[mqmTotal], doc, doc_seg_id);
      if (!mqmStatsBySystem.hasOwnProperty(system)) {
        mqmStatsBySystem[system] = [];
      }
      currSegStatsBySys =
          mqmGetSegStats(mqmStatsBySystem[system], doc, doc_seg_id);
    }

    if (!sameAsLast || (rater != lastRow[6])) {
      currSegStats.push(mqmInitRaterStats(rater));
      currSegStatsBySys.push(mqmInitRaterStats(rater));
      /** New rater. **/
      if (!mqmStatsByRater.hasOwnProperty(rater)) {
        mqmStatsByRater[rater] = [];
      }
      currSegStatsByRater =
          mqmGetSegStats(mqmStatsByRater[rater], doc, doc_seg_id);
      currSegStatsByRater.push(mqmInitRaterStats(rater));
    }
    const span = mqmSpanLength(parts[4]) + mqmSpanLength(parts[5]);
    mqmAddErrorStats(mqmArrayLast(currSegStats), parts[7], parts[8], span);
    mqmAddErrorStats(mqmArrayLast(currSegStatsBySys), parts[7], parts[8], span);
    mqmAddErrorStats(mqmArrayLast(currSegStatsByRater), parts[7], parts[8], span);

    mqmAddSevCatStats(mqmStatsBySevCat, system, parts[7], parts[8]);

    lastRow = parts;

    if (shown >= mqmLimit) {
      continue;
    }
    let rowHTML = '';
    for (let i = 0; i < 9; i++) {
      let val = parts[i];
      let cls = 'class="mqm-val"';
      if (i == 4 || i == 5) {
        cls = '';
        if (sameAsLast) {
          val = mqmOnlyKeepSpans(val);
        }
      }
      if (i == 7 && parts[9].note) {
        /* There is a note */
        val += '<br><i>' + parts[9].note + '</i>';
      }
      rowHTML += `<td ${cls} id="mqm-val-${shown}-${i}">` + val + '</td>\n';
    }
    tbody.insertAdjacentHTML(
        'beforeend',
        `<tr class="mqm-row" id="mqm-row-${rowId}">${rowHTML}</tr>\n`);
    for (let i = 0; i < 9; i++) {
      if (i == 4 || i == 5) continue;
      const v = document.getElementById(`mqm-val-${shown}-${i}`);
      v.addEventListener('click', (e) => {
        filters[i].value = '^' + parts[i] + '$';
        mqmShow();
      });
    }
    shown++;
  }
  mqmShowStats();
  document.body.style.cursor = 'auto';
}

/**
 * Replaces <v>...</v> with a span element of class cls.
 * @param {string} text
 * @param {string} cls
 * @return {string}
 */
function mqmMarkSpan(text, cls) {
  text = text.replace(/<v>/, `<span class="${cls}">`);
  text = text.replace(/<\/v>/, '</span>');
  return text;
}

/**
 * Clears all filters.
 */
function mqmClearFilters() {
  const filters = document.getElementsByClassName('mqm-filter-re');
  for (let i = 0; i < filters.length; i++) {
    filters[i].value = '';
  }
  document.getElementById('mqm-filter-expr').value = '';
  document.getElementById('mqm-filter-expr-error').innerHTML = '';
}

/**
 * Clears all filters and shows stats again.
 */
function mqmClearFiltersAndShow() {
  mqmClearFilters();
  mqmShow();
}

/**
 * For the column named by "what", sets the filter to the currently picked
 * value from its drop-down list.
 * @param {string} what
 */
function mqmPick(what) {
  const filters = document.getElementsByClassName('mqm-filter-re');
  let index = -1;
  const exp = 'mqm-filter-' + what;
  for (let i = 0; i < filters.length; i++) {
    if (filters[i].id == exp) {
      index = i;
      break;
    }
  }
  if (index < 0) return;
  const sel = document.getElementById('mqm-select-' + what);
  if (!sel) return;
  filters[index].value = sel.value;
  mqmShow();
}

/**
 * Populates the column drop-down lists and filter-expression builder with
 * unique values.
 */
function mqmSetSelectOptions() {
  const options = [{}, {}, {}, {}, null, null, {}, {}, {}];
  for (let parts of mqmData) {
    for (let i = 0; i < 9; i++) {
      if (i == 4 || i == 5) continue;
      options[i][parts[i].trim()] = true;
    }
  }
  const selects = [
    document.getElementById('mqm-select-system'),
    document.getElementById('mqm-select-doc'),
    document.getElementById('mqm-select-doc-seg-id'),
    document.getElementById('mqm-select-global-seg-id'),
    null,
    null,
    document.getElementById('mqm-select-rater'),
    document.getElementById('mqm-select-category'),
    document.getElementById('mqm-select-severity'),
  ];
  for (let i = 0; i < 9; i++) {
    if (i == 4 || i == 5) continue;
    const opt = options[i];
    let html = '<option value=""></option>\n';
    for (let o in opt) {
      if (!o) continue;
      html += `<option value="^${o}$">${o}</option>\n`;
    }
    selects[i].innerHTML = html;
  }

  /**
   * Populate filter clause builder's selects:
   */
  mqmClauseKey = document.getElementById('mqm-clause-key');
  let html = '<option value=""></option>\n';
  for (let sys in options[0]) {
    html += `<option value="System: ${sys}">System: ${sys}</option>\n`;
  }
  for (let rater in options[6]) {
    html += `<option value="Rater: ${rater}">Rater: ${rater}</option>\n`;
  }
  mqmClauseKey.innerHTML = html;

  mqmClauseInclExcl = document.getElementById('mqm-clause-inclexcl');

  mqmClauseCat = document.getElementById('mqm-clause-cat');
  html = '<option value=""></option>\n';
  for (let cat in options[7]) {
    html += `<option value="${cat}">${cat}</option>\n`;
  }
  mqmClauseCat.innerHTML = html;

  mqmClauseSev = document.getElementById('mqm-clause-sev');
  html = '<option value=""></option>\n';
  for (let sev in options[8]) {
    html += `<option value="${sev}">${sev}</option>\n`;
  }
  mqmClauseSev.innerHTML = html;

  mqmClauseAddAnd = document.getElementById('mqm-clause-add-and');
  mqmClauseAddOr = document.getElementById('mqm-clause-add-or');
  mqmClearClause();
}

/**
 * Parses the passed TSV data into mqmData.
 * @param {string} tsvData
 */
function mqmParseData(tsvData) {
  mqmClearFilters();
  const errors = document.getElementById('mqm-errors');
  errors.innerHTML = '';
  mqmData = [];
  const data = tsvData.split('\n');
  let firstLine = true;
  for (let line of data) {
    if (!line.trim()) {
      continue;
    }
    if (firstLine) {
      firstLine = false;
      if (line.toLowerCase().indexOf('system\tdoc') >= 0) {
        /** Skip header line **/
        continue;
      }
    }
    const parts = line.split('\t');

    let metadata = {};
    if (parts.length < 9) {
      errors.insertAdjacentHTML('beforeend', `Could not parse: ${line}`);
      continue;
    } else if (parts.length == 9) {
      /** TSV data is missing the last metadata column. Create it. */
      parts.push(metadata);
    } else {
      /**
       * The 10th column should be a JSON-encoded "metadata" object. Prior to
       * May 2022, the 10th column, when present, was just a string that was a
       * "note" from the rater, so convert that to a metadata object if needed.
       */
      try {
        metadata = JSON.parse(parts[9]);
      } catch (err) {
        metadata = {};
        const note = parts[9].trim();
        if (note) {
          metadata['note'] = note;
        }
      }
      parts[9] = metadata;
    }
    /** Move "Rater" to go after source/target. */
    const temp = parts[4];
    parts[4] = parts[5];
    parts[5] = parts[6];
    parts[6] = temp;
    let spanClass = 'mqm-neutral';
    const severity = parts[8].toLowerCase();
    if (severity == 'major' ||
        severity == 'critical' ||
        severity.startsWith('non-translation') ||
        severity.startsWith('non_translation')) {
      spanClass = 'mqm-major';
    }
    if (severity == 'minor' ||
        severity == 'trivial') {
      spanClass = 'mqm-minor';
    }
    parts[8] = parts[8].charAt(0).toUpperCase() + parts[8].substr(1);
    parts[4] = mqmMarkSpan(parts[4], spanClass);
    parts[5] = mqmMarkSpan(parts[5], spanClass);
    mqmData.push(parts);
  }
  mqmSort();
  mqmAddSegmentAggregations();
  mqmSetSelectOptions();
  mqmShow();
}

/**
 * Opens the data file(s) picked by the user and loads the data into mqmData[].
 */
function mqmOpenFiles() {
  document.body.style.cursor = 'wait';
  mqmClearFilters();
  const errors = document.getElementById('mqm-errors');
  errors.innerHTML = '';
  const filesElt = document.getElementById('mqm-file');
  const numFiles = filesElt.files.length;
  if (numFiles <= 0) {
    errors.innerHTML = 'No files were selected';
    return;
  }
  let erroneousFile = '';
  try {
    const filesData = [];
    let filesRead = 0;
    for (let i = 0; i < numFiles; i++) {
      filesData.push('');
      const f = filesElt.files[i];
      erroneousFile = f.name;
      const fr = new FileReader();
      fr.onload = (evt) => {
        erroneousFile = f.name;
        filesData[i] = fr.result;
        filesRead++;
        if (filesRead == numFiles) {
          mqmParseData(filesData.join('\n'));
        }
      };
      fr.readAsText(f);
    }
  } catch (err) {
    let errString = err +
        (errnoeousFile ? ' (file with error: ' + erroneousFile + ')' : '');
    errors.innerHTML = errString;
    filesElt.value = '';
  }
}

/**
 * Saves mqmTSVData to the file mqm-data.tsv.
 */
function mqmSaveData() {
  const a = document.createElement("a");
  a.style.display = "none";
  document.body.appendChild(a);
  a.href = window.URL.createObjectURL(
    new Blob([mqmTSVData], {type: "text/tab-separated-values;charset=UTF-8"})
  );
  a.setAttribute("download", "mqm-data.tsv");
  a.click();
  window.URL.revokeObjectURL(a.href);
  document.body.removeChild(a);
}

/**
 * Replaces the HTML contents of elt with the HTML needed to render the
 *     MQM Viewer. If tsvData is not null, then this data is loaded, and instead
 *     of a file-open button there is a "download TSV data" button.
 * @param {!Element} elt
 * @param {?string=} tsvData
 */
function createMQMViewer(elt, tsvData=null) {
  mqmTSVData = tsvData;
  const header = mqmTSVData ? `
  <div class="mqm-header">
    <span class="mqm-title">MQM Scores</span>
    <span class="mqm-header-right">
      <button id="mqm-save-file" onclick="mqmSaveData()">
      Save MQM data to file "mqm-data.tsv"
      </button>
    </span>
  </div>` :
                              `
  <div class="mqm-header">
    <span class="mqm-title">MQM Scores</span>
    <span class="mqm-header-right">
      <b>Open MQM data file(s) (9-column TSV format):</b>
      <input id="mqm-file" accept=".tsv" onchange="mqmOpenFiles()"
          type="file" multiple></input>
    </span>
  </div>
  `;

  const mqm_help_text = `MQM score. When there are at least 5 documents after ` +
      `filtering, 95% confidence intervals for each system are also shown. ` +
      `Confidence intervals are estimated through bootstrap sampling ` +
      `for 1000 times on the document level. ` + 
      `If there are less than 5 documents, N/A is shown instead.`;
  const mqm_score_with_ci = `<b>MQM score</b><sup class="mqm-help-icon">?</sup>`;
  elt.innerHTML = `
  ${header}
  <div id="mqm-errors"></div>
  <hr>

  <table class="mqm-table" id="mqm-stats">
    <thead>
      <tr>
        <th></th>
        <th title="Number of segments"><b>Segments</b></th>
        <th title="Number of ratings"><b>Ratings</b></th>
        <th title="${mqm_help_text}">${mqm_score_with_ci}</th>
        <th title="Non-trans. component of MQM score (25 * #non-translation)"><b>MQM
          Non-trans.</b></th>
        <th title="Major component of MQM score (5 * #major)"><b>MQM
          Major</b></th>
        <th title="Minor component of MQM score (1 * #minor)"><b>MQM
          Minor</b></th>
        <th title="Trivial component of MQM score (0.1 * #trivial)"><b>MQM
          Trivial</b></th>
        <th title="Accuracy component of MQM major+minor score"><b>MQM Accu.</b></th>
        <th title="Fluency component of MQM major+minor score"><b>MQM Flue.</b></th>
        <th title="Uncategorized component of MQM major+minor score"><b>MQM Uncat.</b></th>
        <th title="Average length of error span"><b>Err span</b></th>
        <th title="Hands-on-the-wheel test"><b>HOTW Test</b></th>
      </tr>
    </thead>
    <tbody id="mqm-stats-tbody">
    </tbody>
  </table>

  <br>

  <details>
    <summary title="Click to see error severity / category counts">
      <span class="mqm-section">
        Error severities and categories
      </span>
    </summary>
    <table class="mqm-table" id="mqm-sevcat-stats">
      <thead>
        <tr>
          <th title="Error severity"><b>Severity</b></th>
          <th title="Error category"><b>Category</b></th>
          <th> </th>
          <th id="mqm-sevcat-stats-th"
              title="Number of occurrences"><b>Count</b></th>
        </tr>
      </thead>
      <tbody id="mqm-sevcat-stats-tbody">
      </tbody>
    </table>
  </details>

  <br>

  <details>
    <summary title="Click to see advanced filtering options and documentation">
      <span class="mqm-section">
        Filters
        <button
            title="Clear all column filters and JavaScript filter expression"
            onclick="mqmClearFiltersAndShow()">Clear all filters</button>
      </span>
    </summary>

    <ul class="mqm-filters">
      <li>
        You can click on any System/Doc/ID/Rater/Category/Severity (or pick
        from the drop-down list under the column name) to set its <b>column
        filter</b> to that specific value.
      </li>
      <li>
        You can provide <b>column filter</b> regular expressions for filtering
        one or more columns, in the input fields provided under the column
        names.
      </li>
      <li>
        You can create sophisticated filters (involving multiple columns, for
        example) using a <b>JavaScript filter expression</b>:
        <br>
        <input class="mqm-filter-expr" id="mqm-filter-expr"
        title="Provide a JavaScript boolean filter expression (and press Enter)"
            onchange="mqmShow()" type="text" size="150">
        </input>
        <div id="mqm-filter-expr-error" class="mqm-filter-expr-error"></div>
        <br>
        <ul>
          <li>This allows you to filter using any expression
              involving the columns. It can use the following
              variables: <b>system</b>, <b>doc</b>, <b>global_seg_id</b>,
              <b>doc_seg_id</b>, <b>rater</b>, <b>category</b>, <b>severity</b>,
              <b>source</b>, <b>target</b>.
          </li>
          <li>
            Filter expressions also have access to an aggregated <b>segment</b>
            variable that is an object with the following properties:
            <b>segment.cats_by_system</b>,
            <b>segment.cats_by_rater</b>,
            <b>segment.sevs_by_system</b>,
            <b>segment.sevs_by_rater</b>,
            <b>segment.sevcats_by_system</b>,
            <b>segment.sevcats_by_rater</b>.
            Each of these properties is an object
            keyed by system or rater, with the values being arrays of strings.
            The "sevcats_*" values look like "Minor/Fluency/Punctuation" or
            are just the same as severities if categories are empty. This
            segment-level aggregation allows you to select specific segments
            rather than just specific error ratings.
          </li>
          <li><b>Example</b>: global_seg_id > 10 || severity == 'Major'</li>
          <li><b>Example</b>: target.indexOf('thethe') >= 0</li>
          <li><b>Example</b>:
            segment.sevs_by_system['System-42'].includes('Major')</li>
          <li><b>Example</b>:
            JSON.stringify(segment.sevcats_by_system).includes('Major/Fl')</li>
          <li>
            You can add segment-level filtering clauses (AND/OR) using this
            <b>helper</b> (which uses convenient shortcut functions
            mqmIncl()/mqmExcl() for checking that a rating exists and
            has/does-not-have a value):
            <div>
              <select onchange="mqmCheckClause()"
                id="mqm-clause-key"></select>
              <select onchange="mqmCheckClause()"
                id="mqm-clause-inclexcl">
                <option value="includes">has error</option>
                <option value="excludes">does not have error</option>
              </select>
              <select onchange="mqmCheckClause()"
                id="mqm-clause-sev"></select>
              <select onchange="mqmCheckClause()"
                id="mqm-clause-cat"></select>
              <button onclick="mqmAddClause('&&')" disabled
                id="mqm-clause-add-and">Add ADD clause</button>
              <button onclick="mqmAddClause('||')" disabled
                id="mqm-clause-add-or">Add OR clause</button>
            </div>
          </li>
        </ul>
        <br>
      </li>
      <li title="Limit this to at most a few thousand to avoid OOMs!">
        <b>Limit</b> the number of rows shown to:
        <input size="6" maxlength="6" type="text" id="mqm-limit" value="2000"
            onchange="setMqmLimit()">
        </input>
      </li>
    </ul>
  </details>

  <br>

  <span class="mqm-section">Rated Segments</span>
  <table class="mqm-table" id="mqm-table">
    <thead id="mqm-thead">
      <tr id="mqm-head-row">
        <th id="mqm-th-system" title="System name">
          System
          <br>
          <input class="mqm-filter-re" id="mqm-filter-system"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="10">
          </input>
          <br>
          <select onchange="mqmPick('system')"
              class="mqm-select" id="mqm-select-system"></select>
        </th>
        <th id="mqm-th-doc" title="Document name">
          Doc
          <br>
          <input class="mqm-filter-re" id="mqm-filter-doc"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="10">
          </input>
          <br>
          <select onchange="mqmPick('doc')"
              class="mqm-select" id="mqm-select-doc"></select>
        </th>
        <th id="mqm-th-doc-seg-id" title="ID of the segment
            within its document">
          DocSeg
          <br>
          <input class="mqm-filter-re" id="mqm-filter-doc-seg-id"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="4">
          </input>
          <br>
          <select onchange="mqmPick('doc-seg-id')"
              class="mqm-select" id="mqm-select-doc-seg-id"></select>
        </th>
        <th id="mqm-th-global-seg-id" title="ID of the segment across
            all documents">
          GlbSeg
          <br>
          <input class="mqm-filter-re" id="mqm-filter-global-seg-id"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="4">
          </input>
          <br>
          <select onchange="mqmPick('global-seg-id')"
              class="mqm-select" id="mqm-select-global-seg-id"></select>
        </th>
        <th id="mqm-th-source" title="Source text of segment">
          Source
          <br>
          <input class="mqm-filter-re" id="mqm-filter-source"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="10">
          </input>
        </th>
        <th id="mqm-th-target" title="Translated text of segment">
          Target
          <br>
          <input class="mqm-filter-re" id="mqm-filter-target"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="10">
          </input>
        </th>
        <th id="mqm-th-rater" title="Rater who evaluated">
          Rater
          <br>
          <input class="mqm-filter-re" id="mqm-filter-rater"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="10">
          </input>
          <br>
          <select onchange="mqmPick('rater')"
              class="mqm-select" id="mqm-select-rater"></select>
        </th>
        <th id="mqm-th-category" title="Error category">
          Category
          <br>
          <input class="mqm-filter-re" id="mqm-filter-category"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="10">
          </input>
          <br>
          <select onchange="mqmPick('category')"
              class="mqm-select" id="mqm-select-category"></select>
        </th>
        <th id="mqm-th-severity" title="Error severity">
          Severity
          <br>
          <input class="mqm-filter-re" id="mqm-filter-severity"
              title="Provide a regexp to filter (and press Enter)"
              onchange="mqmShow()" type="text" placeholder=".*" size="10">
          </input>
          <br>
          <select onchange="mqmPick('severity')"
              class="mqm-select" id="mqm-select-severity"></select>
        </th>
      </tr>
    </thead>
    <tbody id="mqm-tbody">
    </tbody>
  </table>
  `;
  elt.className = 'mqm';
  if (mqmTSVData) {
    mqmParseData(mqmTSVData);
  }
}
