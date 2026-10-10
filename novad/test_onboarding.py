"""Isolated P1-01A onboarding state vectors; no live state is touched."""
from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import patch

import onboarding as ob


def base(**changes):
    values = dict(
        prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'AVAILABLE', 'OBSERVED'),
        inventory=ob.InventorySnapshot('KNOWN', ('model-a',)),
        selection=ob.SelectionSnapshot('VALID', 'model-a', 'INSTALLED_EXACT', True),
        candidates=(), recommendation=ob.RecommendationSnapshot(
            'NO_DEFENSIBLE_RECOMMENDATION'), operations=(),
    )
    values.update(changes)
    return ob.OnboardingSnapshot(**values)


def candidate(name='catalog-a', status='ELIGIBLE', artifact='NOT_INSTALLED',
              installed_model_id=None, blockers=()):
    return ob.CandidateSnapshot(name, status, artifact, installed_model_id, blockers)


class OnboardingTests(unittest.TestCase):
    def test_runtime_unavailable_precedes_stale_backend_success(self):
        result = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('UNAVAILABLE', 'AVAILABLE', 'OBSERVED'),
            operations=(ob.OperationSnapshot('ACTIVATION', 'SUCCEEDED', 'model-a'),)))
        self.assertEqual(result.primary_state, 'NEEDS_USER_SETUP')
        self.assertNotIn('SEND_FIRST_MESSAGE', result.allowed_actions)

    def test_runtime_and_provider_unknown_remain_checking(self):
        runtime = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('UNKNOWN', 'AVAILABLE', 'OBSERVED')))
        provider = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'UNKNOWN', 'OBSERVED')))
        self.assertEqual(runtime.primary_state, 'CHECKING')
        self.assertEqual(provider.primary_state, 'CHECKING')

    def test_unknown_hardware_is_a_visible_limit_for_coherent_returning_user(self):
        result = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'AVAILABLE', 'UNKNOWN')))
        self.assertEqual(result.primary_state, 'READY')
        self.assertEqual(result.allowed_actions, ('SEND_FIRST_MESSAGE',))
        self.assertIn('HARDWARE_PROFILE_UNKNOWN', result.limitations)

    def test_unknown_hardware_does_not_turn_residency_alone_into_readiness(self):
        result = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'AVAILABLE', 'UNKNOWN'),
            selection=ob.SelectionSnapshot('MISSING', None, 'UNKNOWN', True)))
        self.assertEqual(result.primary_state, 'CHECKING')
        self.assertNotIn('SEND_FIRST_MESSAGE', result.allowed_actions)

    def test_unavailable_hardware_still_blocks_coherent_returning_user(self):
        result = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'AVAILABLE', 'UNAVAILABLE')))
        self.assertEqual(result.primary_state, 'NEEDS_USER_SETUP')
        self.assertNotIn('SEND_FIRST_MESSAGE', result.allowed_actions)

    def test_provider_unavailable_needs_user_setup(self):
        result = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'UNAVAILABLE', 'PARTIAL')))
        self.assertEqual(result.primary_state, 'NEEDS_USER_SETUP')
        self.assertIn('PROVIDER_UNAVAILABLE', result.explanation_codes)
        self.assertIn('HARDWARE_PROFILE_PARTIAL', result.limitations)

    def test_outdated_provider_needs_user_setup(self):
        result = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'OUTDATED', 'OBSERVED')))
        self.assertEqual(result.primary_state, 'NEEDS_USER_SETUP')
        self.assertEqual(result.explanation_codes, ('PROVIDER_OUTDATED',))
        self.assertIn('OPEN_PROVIDER_SETUP', result.allowed_actions)

    def test_empty_inventory_with_one_eligible_candidate(self):
        result = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN'),
            selection=ob.SelectionSnapshot('MISSING'),
            candidates=(candidate(),)))
        self.assertEqual(result.primary_state, 'NEEDS_MODEL')
        self.assertEqual(result.allowed_actions, ('PROPOSE_INSTALL',))
        self.assertEqual(result.target_id, 'catalog-a')
        self.assertNotIn('RECOMMENDED', ' '.join(result.explanation_codes))

    def test_empty_inventory_without_eligible_candidate_explains_limit(self):
        result = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN'),
            selection=ob.SelectionSnapshot('MISSING'),
            candidates=(candidate(status='BLOCKED', blockers=('INSUFFICIENT_STORAGE',)),)))
        self.assertEqual(result.primary_state, 'NEEDS_MODEL')
        self.assertIn('NO_ELIGIBLE_CATALOG_CANDIDATE', result.explanation_codes)
        self.assertNotIn('PROPOSE_INSTALL', result.allowed_actions)
        self.assertIn('PREFLIGHT:catalog-a:INSUFFICIENT_STORAGE', result.limitations)

    def test_multiple_unordered_candidates_require_choice(self):
        result = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN'),
            selection=ob.SelectionSnapshot('MISSING'),
            candidates=(candidate('catalog-z'), candidate('catalog-a'))))
        self.assertEqual(result.candidate_catalog_ids, ('catalog-a', 'catalog-z'))
        self.assertEqual(result.allowed_actions, ('CHOOSE_INSTALL_CANDIDATE',))
        self.assertIn('ELIGIBLE_CANDIDATES_UNORDERED', result.limitations)

    def test_existing_recommendation_is_used_only_when_install_eligible(self):
        result = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN'),
            selection=ob.SelectionSnapshot('MISSING'),
            candidates=(candidate('catalog-a'), candidate('catalog-b')),
            recommendation=ob.RecommendationSnapshot('RECOMMENDATION_AVAILABLE', 'catalog-b')))
        self.assertEqual(result.preferred_catalog_id, 'catalog-b')
        self.assertEqual(result.allowed_actions, ('PROPOSE_INSTALL',))

    def test_missing_and_invalid_configuration_need_explicit_activation(self):
        for state in ('MISSING', 'INVALID', 'MALFORMED', 'UNSUPPORTED'):
            with self.subTest(state=state):
                result = ob.classify(base(selection=ob.SelectionSnapshot(
                    state, 'fallback-model', 'INSTALLED_EXACT', False)))
                self.assertEqual(result.primary_state, 'NEEDS_ACTIVATION')
                self.assertEqual(result.allowed_actions, ('CHOOSE_INSTALLED_MODEL',))
                self.assertIn('SERVER_DEFAULT_FALLBACK_IS_NOT_EXPLICIT_SELECTION', result.limitations)

    def test_selected_but_unloaded_model_is_ready_and_loads_with_the_first_message(self):
        # Konstantin, 2026-10-09: the 10-minute auto-unload must not block chat. An explicitly
        # selected, installed model out of GPU memory is ready; sending loads it.
        result = ob.classify(base(selection=ob.SelectionSnapshot(
            'VALID', 'model-a', 'INSTALLED_EXACT', False)))
        self.assertEqual(result.primary_state, 'READY')
        self.assertEqual(result.allowed_actions, ('SEND_FIRST_MESSAGE', 'LOAD_AND_VERIFY'))
        self.assertIn('SELECTED_MODEL_NOT_LOADED', result.explanation_codes)
        self.assertNotIn('SELECTED_MODEL_READY', result.explanation_codes)
        self.assertIn('LOADS_ON_FIRST_MESSAGE', result.limitations)
        self.assertEqual(result.target_id, 'model-a')

    def test_unknown_readiness_never_becomes_ready(self):
        result = ob.classify(base(selection=ob.SelectionSnapshot(
            'VALID', 'model-a', 'INSTALLED_EXACT', None)))
        self.assertEqual(result.primary_state, 'CHECKING')
        self.assertIn('RUNTIME_READINESS_UNKNOWN', result.explanation_codes)

    def test_returning_user_requires_all_explicit_ready_facts(self):
        result = ob.classify(base())
        self.assertEqual(result.primary_state, 'READY')
        self.assertEqual(result.allowed_actions, ('SEND_FIRST_MESSAGE',))

    def test_hardware_unknown_is_nonblocking_only_for_coherent_ready_returner(self):
        unknown = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'AVAILABLE', 'UNKNOWN')))
        unavailable = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'AVAILABLE', 'UNAVAILABLE')))
        self.assertEqual(unknown.primary_state, 'READY')
        self.assertEqual(unavailable.primary_state, 'NEEDS_USER_SETUP')
        self.assertIn('HARDWARE_PROFILE_UNKNOWN', unknown.limitations)

    def test_known_partial_hardware_is_preserved_as_a_ready_limitation(self):
        result = ob.classify(base(
            prerequisites=ob.PrerequisiteSnapshot('AVAILABLE', 'AVAILABLE', 'PARTIAL')))
        self.assertEqual(result.primary_state, 'READY')
        self.assertIn('HARDWARE_PROFILE_PARTIAL', result.limitations)

    def test_returning_ready_user_does_not_receive_irrelevant_recommendation_limit(self):
        result = ob.classify(base())
        self.assertFalse(any(value.startswith('QUALITY_RECOMMENDATION_')
                             for value in result.limitations))

    def test_inventory_unknown_and_selected_conflicts_are_conservative(self):
        unknown = ob.classify(base(inventory=ob.InventorySnapshot('UNKNOWN')))
        conflict = ob.classify(base(inventory=ob.InventorySnapshot('KNOWN')))
        self.assertEqual(unknown.primary_state, 'CHECKING')
        self.assertEqual(conflict.primary_state, 'CHECKING')
        self.assertIn('CONFLICTING_READINESS_SNAPSHOT', conflict.explanation_codes)

    def test_stale_and_explicitly_conflicting_snapshots_block_actions(self):
        for state in ('STALE', 'UNKNOWN', 'CONFLICTING'):
            with self.subTest(state=state):
                result = ob.classify(base(snapshot_consistency=state))
                self.assertEqual(result.primary_state, 'CHECKING')
                self.assertEqual(result.allowed_actions, ('RETRY_CHECK',))
                self.assertIn('SNAPSHOT_' + state, result.explanation_codes)

    def test_catalog_inventory_conflict_is_not_treated_as_installed(self):
        result = ob.classify(base(candidates=(
            candidate('catalog-other', 'NO_OP_ALREADY_INSTALLED',
                      'INSTALLED_EXACT', 'missing-model'),)))
        self.assertEqual(result.primary_state, 'CHECKING')
        self.assertIn('CONFLICTING_CATALOG_INVENTORY', result.explanation_codes)

    def test_selected_missing_or_revision_mismatch_never_falls_back_silently(self):
        for state in ('NOT_INSTALLED', 'INSTALLED_DIFFERENT_REVISION'):
            with self.subTest(state=state):
                result = ob.classify(base(selection=ob.SelectionSnapshot(
                    'VALID', 'missing-model', state, False)))
                self.assertEqual(result.primary_state, 'NEEDS_ACTIVATION')
                self.assertIn('CHOOSE_INSTALLED_MODEL', result.allowed_actions)

    def test_acquisition_states_reuse_existing_lifecycle_meanings(self):
        cases = {
            'PROPOSED': 'ACQUISITION_PROPOSED',
            'DOWNLOADING': 'ACQUISITION_IN_PROGRESS',
            'VERIFYING': 'ACQUISITION_IN_PROGRESS',
            'FAILED': 'INSTALLATION_FAILED',
            'RECOVERY_REQUIRED': 'INSTALLATION_RECOVERY_REQUIRED',
        }
        for state, expected in cases.items():
            with self.subTest(state=state):
                result = ob.classify(base(operations=(
                    ob.OperationSnapshot('ACQUISITION', state, 'catalog-a'),)))
                self.assertEqual(result.primary_state, expected)
                self.assertNotIn('SEND_FIRST_MESSAGE', result.allowed_actions)

    def test_verified_install_with_no_selection_advances_to_activation(self):
        result = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN', ('model-new',)),
            selection=ob.SelectionSnapshot('MISSING'),
            candidates=(candidate('catalog-new', 'NO_OP_ALREADY_INSTALLED',
                                  'INSTALLED_EXACT', 'model-new'),),
            operations=(ob.OperationSnapshot('ACQUISITION', 'INSTALLED', 'catalog-new'),)))
        self.assertEqual(result.primary_state, 'NEEDS_ACTIVATION')
        self.assertIn('ACQUISITION_INSTALLED', result.explanation_codes)
        self.assertEqual(result.target_id, 'model-new')
        self.assertEqual(result.allowed_actions, ('ACTIVATE_MODEL',))

    def test_verified_install_without_identity_mapping_requires_choice(self):
        result = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN', ('model-new',)),
            selection=ob.SelectionSnapshot('MISSING'),
            operations=(ob.OperationSnapshot('ACQUISITION', 'INSTALLED', 'catalog-new'),)))
        self.assertEqual(result.allowed_actions, ('CHOOSE_INSTALLED_MODEL',))
        self.assertEqual(result.target_id, 'catalog-new')

    def test_activation_in_progress_and_failure_precede_readiness(self):
        active = ob.classify(base(operations=(
            ob.OperationSnapshot('ACTIVATION', 'ACTIVATING', 'model-b'),)))
        failed = ob.classify(base(operations=(
            ob.OperationSnapshot('ACTIVATION', 'FAILED', 'model-b'),)))
        self.assertEqual(active.primary_state, 'ACTIVATING')
        self.assertEqual(failed.primary_state, 'ACTIVATION_FAILED')
        self.assertEqual(failed.allowed_actions, ('ACTIVATE_MODEL',))

    def test_unknown_or_conflicting_operations_block_actions(self):
        unknown = ob.classify(base(operations=(
            ob.OperationSnapshot('ACQUISITION', 'UNKNOWN'),)))
        conflict = ob.classify(base(operations=(
            ob.OperationSnapshot('ACQUISITION', 'DOWNLOADING', 'catalog-a'),
            ob.OperationSnapshot('ACTIVATION', 'ACTIVATING', 'model-a'))))
        self.assertEqual(unknown.primary_state, 'CHECKING')
        self.assertEqual(conflict.primary_state, 'CHECKING')
        self.assertEqual(conflict.allowed_actions, ('RETRY_CHECK',))

    def test_ordering_is_deterministic(self):
        first = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN'), selection=ob.SelectionSnapshot('MISSING'),
            candidates=(candidate('z'), candidate('a'))))
        second = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN'), selection=ob.SelectionSnapshot('MISSING'),
            candidates=(candidate('a'), candidate('z'))))
        self.assertEqual(first, second)

    def test_unknown_preflight_does_not_create_install_action(self):
        result = ob.classify(base(
            inventory=ob.InventorySnapshot('KNOWN'), selection=ob.SelectionSnapshot('MISSING'),
            candidates=(candidate(status='UNKNOWN'),)))
        self.assertNotIn('PROPOSE_INSTALL', result.allowed_actions)
        self.assertEqual(result.candidate_catalog_ids, ())

    def test_invalid_normalized_input_is_rejected(self):
        with self.assertRaises(ob.InputError):
            ob.classify(base(selection=ob.SelectionSnapshot('VALID')))
        with self.assertRaises(ob.InputError):
            ob.classify(base(candidates=(candidate(), candidate())))
        with self.assertRaises(ob.InputError):
            ob.classify(base(prerequisites=ob.PrerequisiteSnapshot(
                'AVAILABLE', 'AVAILABLE', 'MAGIC')))

    def test_result_is_json_compatible_and_input_is_not_mutated(self):
        snapshot = base()
        before = deepcopy(snapshot)
        value = ob.classify(snapshot).as_dict()
        self.assertEqual(snapshot, before)
        self.assertIsInstance(value['allowed_actions'], list)
        self.assertEqual(value['primary_state'], 'READY')

    def test_classifier_performs_no_io_or_runtime_mutation(self):
        snapshot = base()
        with ExitStack() as stack:
            for name in ('builtins.open', 'socket.socket', 'socket.create_connection',
                         'urllib.request.urlopen', 'subprocess.Popen', 'os.system'):
                stack.enter_context(patch(name, side_effect=AssertionError('I/O prohibited')))
            result = ob.classify(snapshot)
        self.assertEqual(result.primary_state, 'READY')


if __name__ == '__main__':
    unittest.main()
