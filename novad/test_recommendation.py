"""Symbolic policy vectors, adversarial boundaries and pure evaluator checks."""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
import itertools
import json
import unittest
from unittest.mock import patch

import recommendation as r


def proof(*candidates, profile='GENERAL', kind='QUALITY_JUDGMENT', **changes):
    value = r.Evidence('fixture://evidence', 'fixture-protocol-v1',
                       'REPRODUCIBLE_BENCHMARK', kind,
                       tuple(c.artifact for c in candidates), profile, 'workload-v1',
                       'HIGH', True, '2026-09-08T12:00:00Z', True,
                       environment='environment-v1')
    return replace(value, **changes)


def candidate(identifier, rating='GOOD', context=32768, **changes):
    artifact = r.Artifact('local', identifier, 'revision-' + identifier)
    c = r.Candidate(identifier, artifact,
                    r.Fit(rating, 'MEDIUM', True, artifact.revision,
                          'workload-v1', 'environment-v1', 131072, context,
                          6 * 1024**3, policy_revision='fit-v1'),
                    r.Capabilities(chat=True), True, True, True, 100)
    tasks = tuple(r.TaskEvidence(proof(c, profile=p), True) for p in r.PROFILES)
    return replace(replace(c, tasks=tasks), **changes)


def compare(a, b, dimension='TASK', outcome='LEFT', profile='GENERAL', **changes):
    e = proof(a, b, profile=profile, kind='FACT' if dimension == 'PERFORMANCE' else 'QUALITY_JUDGMENT')
    value = r.Comparison(a.catalog_id, b.catalog_id, dimension, outcome, e,
                         'Fixture evidence supports ' + outcome + ' on ' + dimension)
    return replace(value, **changes)


def request(**changes):
    return r.Request('workload-v1', 'environment-v1', **changes)


def run(candidates, comparisons=(), **changes):
    return r.evaluate(tuple(candidates), request(**changes), tuple(comparisons))


def ids(result, tier='recommended'):
    return [c['catalog_id'] for c in result['tiers'][tier]['candidates']]


def calibrated(c):
    conditions = r.Conditions('hardware-v1', 'backend', 'version', 'device0',
                              'full', 'fp16', 'options-v1', 'text', 1)
    budget = r.Budget('fit-v1', 1000, 200, 50)
    trials = tuple(r.Trial('trial-' + str(i), '2026-09-08T12:00:00Z',
                           28672, 4096, 900, 250, True, False, False, False, False)
                   for i in range(3))
    cal = r.Calibration(proof(c, kind='FACT', source='LOCAL_CALIBRATION'),
                        'fixture-protocol', '1', 'all trials pass resource and context bounds',
                        'fixture-memory-sampler', 3, conditions, budget,
                        32768, 32768, 28672, 4096, trials, True)
    return replace(c, calibration=cal), conditions, budget


class PolicyVectors(unittest.TestCase):
    def test_A_good_quality_beats_extra_memory(self):
        a, b = candidate('a'), candidate('b', 'EXCELLENT')
        result = run((a, b), (compare(a, b),))
        self.assertEqual(ids(result), ['a'])
        self.assertEqual(result['confidence'], 'MEDIUM')
        self.assertEqual(ids(result, 'faster'), [])

    def test_B_poor_never_quality_tier(self):
        a, b = candidate('a', 'POOR'), candidate('b')
        result = run((a, b), (compare(a, b),))
        self.assertEqual(ids(result), ['b'])
        self.assertIn('FIT_POOR', result['excluded'][0]['reasons'])
        self.assertEqual(ids(result, 'max_quality'), [])
        self.assertEqual(run((a,))['status'], 'NO_DEFENSIBLE_RECOMMENDATION')

    def test_C_tight_opt_in_and_minimum(self):
        a, b = candidate('a', 'TIGHT', 8192), candidate('b')
        comps = (compare(a, b),)
        self.assertEqual(ids(run((a, b), comps)), ['b'])
        result = run((a, b), comps, allow_tight=True)
        self.assertEqual(ids(result), ['b'])
        self.assertEqual(ids(result, 'max_quality'), ['a'])
        self.assertTrue(result['tiers']['max_quality']['candidates'][0]['trade_offs'])
        self.assertEqual(ids(run((a, b), comps, allow_tight=True, minimum_context=16384), 'max_quality'), [])
        self.assertEqual(ids(run((a,), allow_tight=True)), ['a'])

    def test_D_unknown_qualified_not_rewritten(self):
        a, conditions, budget = calibrated(candidate('a', 'UNKNOWN', None))
        b = candidate('b')
        result = run((a, b), (compare(a, b),), conditions=conditions, budget=budget)
        self.assertEqual(ids(result), ['a'])
        selected = result['tiers']['recommended']['candidates'][0]
        self.assertEqual(selected['fit_state'], 'UNKNOWN')
        self.assertEqual(selected['inputs']['fit']['sustainable_context'], None)
        self.assertEqual(selected['context_source'], 'OBSERVED_QUALIFIED')
        self.assertEqual(selected['usable_context'], 32768)
        self.assertEqual(result['confidence'], 'MEDIUM')

    def test_E_unknown_without_calibration(self):
        a, b = candidate('a', 'UNKNOWN', None), candidate('b')
        self.assertEqual(ids(run((a, b))), ['b'])
        self.assertEqual(ids(run((a,))), [])
        self.assertIn('FIT_UNRESOLVED', run((a,))['excluded'][0]['reasons'])

    def test_F_profile_changes_evidenced_outcome(self):
        a, b = candidate('a'), candidate('b')
        comps = (compare(a, b, profile='CODING'), compare(b, a))
        self.assertEqual(ids(run((a, b), comps, profile='CODING')), ['a'])
        self.assertEqual(ids(run((a, b), comps)), ['b'])

    def test_G_scopes_and_no_installation(self):
        a = candidate('a')
        b = candidate('b', installed=False, acquisition=r.Acquisition(
            'FEASIBLE', 100, 100, 50, 20, 1000, '2026-09-08T12:00:00Z', 'accounting-v1'))
        comps = (compare(b, a),)
        self.assertEqual(ids(run((a, b), comps)), ['a'])
        result = run((a, b), comps, scope='known')
        self.assertEqual(ids(result), ['b'])
        chosen = result['tiers']['recommended']['candidates'][0]
        self.assertIs(chosen['installed'], False)
        self.assertIs(chosen['requires_installation'], True)

    def test_H_declarations_are_not_quality(self):
        a = replace(candidate('a'), tasks=())
        b = replace(candidate('b'), tasks=())
        result = run((a, b))
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
        self.assertEqual(result['confidence'], 'INSUFFICIENT')
        self.assertTrue(all(not ids(result, tier) for tier in result['tiers']))
        self.assertEqual(result['candidates'][0]['eligibility'], 'ELIGIBLE')

    def test_I_storage_blocked_and_unknown(self):
        a = candidate('a')
        b = candidate('b', installed=False, acquisition=r.Acquisition(
            'FEASIBLE', 100, 100, 50, 20, 120, '2026-09-08T12:00:00Z', 'accounting-v1'))
        result = run((a, b), (compare(b, a),), scope='known')
        self.assertEqual(ids(result), ['a'])
        self.assertIn('STORAGE_BLOCKED', result['excluded'][0]['reasons'])
        b = replace(b, acquisition=replace(b.acquisition, staging_bytes=None))
        result = run((a, b), (compare(b, a),), scope='known')
        self.assertIn('STORAGE_UNKNOWN', result['excluded'][0]['reasons'])

    def test_J_equivalence_storage_and_real_ties(self):
        a, b = candidate('a', storage_bytes=80), candidate('b')
        comps = tuple(compare(a, b, d, 'EQUIVALENT') for d in ('TASK', 'GENERAL_QUALITY', 'PERFORMANCE'))
        result = run((a, b), comps)
        self.assertEqual(ids(result), ['a'])
        a = replace(a, storage_bytes=b.storage_bytes)
        self.assertEqual(ids(run((b, a), comps)), ['a', 'b'])
        self.assertEqual(ids(run((a, b))), [])
        self.assertNotIn('"score":', json.dumps(result))


class Boundaries(unittest.TestCase):
    def test_unknowns_preserved_and_names_are_opaque(self):
        a = candidate('super-coder-9999b', tasks=())
        result = run((a,), profile='CODING')
        self.assertEqual(ids(result), [])
        self.assertIsNone(result['candidates'][0]['inputs']['capabilities']['coding'])
        self.assertIsNone(result['candidates'][0]['inputs']['catalog_checked_at'])

    def test_invalid_requests_raise_input_error(self):
        for changes in ({'profile': 'MAGIC'}, {'scope': 'cloud'}, {'minimum_context': 0},
                        {'minimum_context': 40000}, {'allow_tight': 'yes'},
                        {'minimum_context': True}, {'required_capabilities': ('telepathy',)}):
            with self.subTest(changes=changes), self.assertRaises(r.InputError):
                run((candidate('a'),), **changes)

    def test_invalid_nested_inputs(self):
        a = candidate('a')
        for bad in (replace(a, installed=1), replace(a, storage_bytes=-1),
                    replace(a, tasks=[]), replace(a, fit=replace(a.fit, rating='BEST'))):
            with self.subTest(bad=bad), self.assertRaises(r.InputError):
                run((bad,))
        with self.assertRaises(r.InputError):
            run((a, a))

    def test_permutation_and_repeat_determinism(self):
        a, b, c = candidate('a'), candidate('b'), candidate('c')
        comps = (compare(a, b), compare(a, c), compare(b, c))
        expected = run((a, b, c), comps)
        for candidates in itertools.permutations((a, b, c)):
            for comparisons in itertools.permutations(comps):
                self.assertEqual(run(candidates, comparisons), expected)

    def test_pure_no_io_or_mutation(self):
        a, b = candidate('a'), candidate('b')
        args = ((a, b), request(), (compare(a, b),))
        original = deepcopy(args)
        expected = r.evaluate(*args)
        with ExitStack() as stack:
            for name in ('builtins.open', 'io.open', 'socket.socket', 'socket.create_connection',
                         'subprocess.Popen', 'os.system', 'os.stat', 'os.scandir',
                         'os.getenv', 'time.time'):
                stack.enter_context(patch(name, side_effect=AssertionError('I/O prohibited')))
            result = r.evaluate(*args)
        self.assertEqual(result, expected)
        self.assertEqual(args, original)
        result['candidates'][0]['inputs']['fit']['rating'] = 'POOR'
        self.assertEqual(a.fit.rating, 'GOOD')

    def test_unsupported_and_provider_offline(self):
        a, conditions, budget = calibrated(candidate('a', 'UNSUPPORTED'))
        self.assertEqual(ids(run((a,), conditions=conditions, budget=budget)), [])
        a = candidate('a', provider_available=False)
        self.assertEqual(ids(run((a,))), [])
        self.assertEqual(run((a,), scope='known')['confidence'], 'LOW')

    def test_unknown_identity_cannot_be_rescued_by_provider_conditional(self):
        a = candidate('a', provider_available=False, identity_verified=None)
        self.assertEqual(ids(run((a,), scope='known')), [])

    def test_minimum_never_clamped_to_native(self):
        a = candidate('a', context=2048)
        a = replace(a, fit=replace(a.fit, native_context=2048))
        self.assertEqual(ids(run((a,))), [])

    def test_missing_comparison_cannot_skip_to_storage(self):
        a, b = candidate('a', storage_bytes=1), candidate('b')
        self.assertEqual(ids(run((a, b), (compare(a, b, 'PERFORMANCE'),))), [])

    def test_conflicting_comparisons_and_cycles_abstain(self):
        a, b, c = candidate('a'), candidate('b'), candidate('c')
        self.assertEqual(ids(run((a, b), (compare(a, b), compare(b, a)))), [])
        self.assertEqual(ids(run((a, b, c), (compare(a, b), compare(b, c), compare(c, a)))), [])

    def test_low_confidence_winner_and_timestamp_cap(self):
        a, b = candidate('a'), candidate('b')
        comp = compare(a, b)
        comp = replace(comp, evidence=replace(comp.evidence, observed_at=None))
        result = run((a, b), (comp,))
        self.assertEqual(ids(result), ['a'])
        self.assertEqual(result['confidence'], 'LOW')

    def test_high_requires_corroboration_and_fit(self):
        a = candidate('a')
        a = replace(a, fit=replace(a.fit, confidence='HIGH', conditional_offload=False))
        self.assertEqual(run((a,))['confidence'], 'HIGH')
        task = a.tasks[0]
        a = replace(a, tasks=(replace(task, evidence=replace(task.evidence, corroborated=False)),))
        self.assertEqual(run((a,))['confidence'], 'MEDIUM')

    def test_required_capability_and_tool_reliability(self):
        a = candidate('a')
        self.assertEqual(ids(run((a,), required_capabilities=('tools',))), [])
        a = replace(a, capabilities=replace(a.capabilities, tools=True))
        self.assertEqual(ids(run((a,), required_capabilities=('tools',))), [])
        task = replace(a.tasks[0], reliable_capabilities=('tools',))
        a = replace(a, tasks=(task,))
        self.assertEqual(ids(run((a,), required_capabilities=('tools',))), ['a'])

    def test_vision_support_and_workload_fit(self):
        a = candidate('a')
        self.assertEqual(ids(run((a,), profile='VISION')), [])
        a = replace(a, capabilities=replace(a.capabilities, vision=True))
        self.assertEqual(ids(run((a,), profile='VISION')), [])
        a = replace(a, fit=replace(a.fit, modality='vision'))
        self.assertEqual(ids(run((a,), profile='VISION')), ['a'])

    def test_fast_profile_and_faster_tier_need_measurements(self):
        a, b = candidate('a'), candidate('b')
        comps = (compare(a, b), compare(b, a, 'PERFORMANCE'))
        self.assertEqual(ids(run((a, b), comps), 'faster'), ['b'])
        fast = (compare(a, b, outcome='EQUIVALENT', profile='FAST_BACKGROUND'),
                compare(b, a, 'PERFORMANCE', profile='FAST_BACKGROUND'),
                compare(a, b, 'GENERAL_QUALITY', profile='FAST_BACKGROUND'))
        self.assertEqual(ids(run((a, b), fast, profile='FAST_BACKGROUND')), ['b'])

    def test_wrong_revision_and_wrong_workload_evidence(self):
        a, b = candidate('a'), candidate('b')
        comp = compare(a, b)
        comp = replace(comp, evidence=replace(comp.evidence, artifacts=(b.artifact,)))
        self.assertEqual(ids(run((a, b), (comp,))), [])
        a = replace(a, fit=replace(a.fit, revision='old'))
        self.assertEqual(ids(run((a,))), [])
        a = candidate('a')
        a = replace(a, fit=replace(a.fit, workload='other'))
        self.assertEqual(ids(run((a,))), [])

    def test_calibration_rejects_incomplete_failed_or_mismatched_records(self):
        a, conditions, budget = calibrated(candidate('a', 'UNKNOWN', None))
        cal = a.calibration
        bads = (replace(cal, trials=cal.trials[:1]), replace(cal, all_trials_recorded=False),
                replace(cal, conflicts_with_fit=True), replace(cal, usable_context=65536),
                replace(cal, trials=(replace(cal.trials[0], oom=True),) + cal.trials[1:]),
                replace(cal, trials=(replace(cal.trials[0], prompt_tokens=8),) + cal.trials[1:]),
                replace(cal, trials=(replace(cal.trials[0], peak_runtime_bytes=980),) + cal.trials[1:]),
                replace(cal, trials=(replace(cal.trials[0], minimum_system_free_bytes=100),) + cal.trials[1:]),
                replace(cal, conditions=replace(conditions, cache_precision='different')),
                replace(cal, evidence=replace(cal.evidence, source='LOCAL_FACT')))
        for bad in bads:
            with self.subTest(bad=bad):
                self.assertEqual(ids(run((replace(a, calibration=bad),), conditions=conditions, budget=budget)), [])

    def test_empty_and_unsupported_tiers(self):
        result = run(())
        self.assertEqual(result['confidence'], 'INSUFFICIENT')
        self.assertEqual(result['status'], 'NO_DEFENSIBLE_RECOMMENDATION')
        self.assertTrue(all(t['abstention_reason'] for t in result['tiers'].values()))

    def test_false_capability_excludes_even_with_positive_task_evidence(self):
        a = candidate('a')
        a = replace(a, capabilities=r.Capabilities(chat=False))
        self.assertEqual(ids(run((a,))), [])

    def test_direct_evidence_preserves_null_declaration(self):
        a = candidate('a')
        a = replace(a, capabilities=r.Capabilities(),
                    tasks=(replace(a.tasks[0], supported_capabilities=('chat',)),))
        result = run((a,))
        self.assertEqual(ids(result), ['a'])
        self.assertIsNone(result['candidates'][0]['inputs']['capabilities']['chat'])

    def test_metadata_is_not_quality_or_speed_evidence(self):
        a, b = candidate('a'), candidate('b')
        for source in ('LOCAL_FACT', 'UPSTREAM_METADATA', 'CURATED'):
            task = replace(a.tasks[0], evidence=replace(a.tasks[0].evidence, source=source))
            self.assertEqual(ids(run((replace(a, tasks=(task,)),))), [])
        cmp = compare(b, a, 'PERFORMANCE')
        cmp = replace(cmp, evidence=replace(cmp.evidence, environment='other-hardware'))
        self.assertEqual(ids(run((a, b), (compare(a, b), cmp)), 'faster'), [])

    def test_deep_reasoning_profile_and_declared_thinking(self):
        a, b = candidate('a'), candidate('b')
        cmp = compare(b, a, profile='DEEP_REASONING')
        self.assertEqual(ids(run((a, b), (cmp,), profile='DEEP_REASONING')), ['b'])
        a = replace(a, tasks=(), capabilities=replace(a.capabilities, thinking=True))
        self.assertEqual(ids(run((a,), profile='DEEP_REASONING')), [])

    def test_specialist_can_be_supported_without_general_winner(self):
        a, b = candidate('a'), candidate('b')
        cmp = compare(a, b, 'SPECIALIST', specialist_scope='specific-subtask')
        result = run((a, b), (cmp,), specialist_scope='specific-subtask')
        self.assertEqual(ids(result), [])
        self.assertEqual(ids(result, 'specialist'), ['a'])
        self.assertEqual(result['status'], 'SPECIALIST_ONLY')
        self.assertEqual(ids(run((a, b), (cmp,)), 'specialist'), [])

    def test_max_quality_general_dimension_preserves_task_confidence(self):
        a, b = candidate('a', context=8192), candidate('b')
        task = compare(a, b, outcome='EQUIVALENT')
        task = replace(task, evidence=replace(task.evidence, confidence='LOW'))
        result = run((a, b), (task, compare(a, b, 'GENERAL_QUALITY')))
        self.assertEqual(ids(result), ['b'])
        self.assertEqual(ids(result, 'max_quality'), ['a'])
        self.assertEqual(result['tiers']['max_quality']['candidates'][0]['confidence'], 'LOW')

    def test_context_band_beats_quality_and_no_reward_beyond_target(self):
        a, b = candidate('a', context=8192), candidate('b')
        self.assertEqual(ids(run((a, b), (compare(a, b),))), ['b'])
        a = replace(a, fit=replace(a.fit, sustainable_context=65536))
        self.assertEqual(ids(run((a, b), (compare(b, a),))), ['b'])

    def test_calibration_does_not_override_revision_or_budget(self):
        a, conditions, budget = calibrated(candidate('a', 'UNKNOWN', None))
        for bad in (replace(a, artifact=replace(a.artifact, revision='new')),
                    replace(a, fit=replace(a.fit, policy_revision='other'))):
            self.assertEqual(ids(run((bad,), conditions=conditions, budget=budget)), [])
        self.assertEqual(ids(run((a,), conditions=conditions, budget=replace(budget, operating_bytes=900))), [])

    def test_bare_feasible_flag_is_not_acquisition_evidence(self):
        a = candidate('a', installed=False, acquisition=r.Acquisition(status='FEASIBLE'))
        result = run((a,), scope='known')
        self.assertEqual(ids(result), [])
        self.assertEqual(result['excluded'][0]['acquisition_feasibility'], 'UNKNOWN')

    def test_malformed_timestamp_rejected(self):
        a = candidate('a', catalog_checked_at='fresh')
        with self.assertRaises(r.InputError):
            run((a,))

    def test_json_serializable_and_defaults(self):
        result = run((candidate('a'),))
        self.assertEqual(json.loads(json.dumps(result))['profile'], 'GENERAL')
        self.assertEqual((result['minimum_context'], result['target_context']), (4096, 32768))

    def test_current_seed_style_no_quality_does_not_recommend(self):
        a = candidate('opaque-artifact')
        a = replace(a, capabilities=r.Capabilities(chat=True, tools=True, thinking=True), tasks=())
        self.assertEqual(run((a,))['status'], 'NO_DEFENSIBLE_RECOMMENDATION')


if __name__ == '__main__':
    unittest.main()
