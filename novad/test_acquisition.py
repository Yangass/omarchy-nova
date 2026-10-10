"""Pure v0.12B acquisition preflight vectors; no provider operation is exercised."""
from contextlib import ExitStack
from copy import deepcopy
import socket
import subprocess
import unittest
from unittest.mock import patch

import acquisition as acq
import catalog

G = acq.GIB


def descriptor(**changes):
    values = dict(catalog_id='opaque-catalog-id', catalog_revision='catalog-v1',
                  provider='ollama', source_trust_state='TRUSTED',
                  artifact_id='opaque-provider-artifact', exact_tag='v1',
                  expected_revision='a' * 64, expected_artifact_size=8 * G,
                  provenance_summary=('catalog-maintainer', 'trusted-ollama-registry'))
    values.update(changes)
    return acq.AcquisitionDescriptor(**values)


def inputs(**changes):
    values = dict(
        descriptor=descriptor(), inventory=acq.InventorySnapshot('NOT_INSTALLED'),
        fit=acq.FitSnapshot('GOOD'), disk=acq.DiskSnapshot(128 * G, 64 * G),
        provider=acq.ProviderPreflight(True, True, True),
        request=acq.PreflightRequest('catalog-v1'))
    values.update(changes)
    return values


class AcquisitionPreflightTests(unittest.TestCase):
    def evaluate(self, **changes):
        return acq.evaluate_preflight(**inputs(**changes))

    def test_A_trusted_good_fit_enough_disk_is_eligible(self):
        result = self.evaluate()
        self.assertTrue(result['eligible'])
        self.assertEqual(result['status'], 'ELIGIBLE')
        self.assertEqual(result['disk']['state'], 'PASS')

    def test_B_untrusted_source_is_blocked_without_url_interpretation(self):
        for trust in ('UNVERIFIED', 'UNSUPPORTED', 'UNKNOWN'):
            with self.subTest(trust=trust):
                result = self.evaluate(descriptor=descriptor(source_trust_state=trust,
                                                             artifact_id='https://untrusted.invalid/model'))
                self.assertFalse(result['eligible'])
                self.assertIn('SOURCE_' + trust, result['blockers'])

    def test_C_exact_install_is_a_no_op(self):
        result = self.evaluate(inventory=acq.InventorySnapshot('INSTALLED_EXACT', 'a' * 64))
        self.assertEqual(result['status'], 'NO_OP_ALREADY_INSTALLED')
        self.assertTrue(result['no_op'])
        self.assertFalse(result['eligible'])

    def test_D_different_revision_blocks_replacement(self):
        result = self.evaluate(inventory=acq.InventorySnapshot('INSTALLED_DIFFERENT_REVISION', 'b' * 64))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('REVISION_MISMATCH', result['blockers'])

    def test_D2_update_replaces_only_an_installed_older_build(self):
        update = acq.PreflightRequest('catalog-v1', allow_update=True)
        result = self.evaluate(inventory=acq.InventorySnapshot('INSTALLED_DIFFERENT_REVISION', 'b' * 64), request=update)
        self.assertTrue(result['eligible'])
        self.assertIn('REPLACES_INSTALLED_REVISION', result['warnings'])
        # Without the observed older revision there is nothing exact to replace.
        result = self.evaluate(inventory=acq.InventorySnapshot('INSTALLED_DIFFERENT_REVISION'), request=update)
        self.assertIn('REVISION_MISMATCH', result['blockers'])
        self.assertIn('NOTHING_TO_UPDATE', self.evaluate(request=update)['blockers'])
        self.assertTrue(self.evaluate(inventory=acq.InventorySnapshot('INSTALLED_EXACT', 'a' * 64), request=update)['no_op'])
        with self.assertRaises(acq.InputError):
            self.evaluate(request=acq.PreflightRequest('catalog-v1', allow_update='yes'))

    def test_E_insufficient_disk_is_blocked(self):
        result = self.evaluate(disk=acq.DiskSnapshot(128 * G, 1 * G))
        self.assertEqual(result['disk']['state'], 'FAIL')
        self.assertIn('INSUFFICIENT_STORAGE', result['blockers'])

    def test_F_stale_catalog_descriptor_blocks(self):
        result = self.evaluate(request=acq.PreflightRequest('catalog-v2'))
        self.assertEqual(result['catalog_revision_state'], 'STALE')
        self.assertIn('STALE_DESCRIPTOR', result['blockers'])

    def test_G_duplicate_acquisition_blocks(self):
        result = self.evaluate(provider=acq.ProviderPreflight(True, True, True, acquisition_in_progress=True))
        self.assertIn('ACQUISITION_CONFLICT', result['blockers'])

    def test_H_provider_unavailable_blocks(self):
        result = self.evaluate(provider=acq.ProviderPreflight(True, False, True))
        self.assertIn('PROVIDER_UNAVAILABLE', result['blockers'])

    def test_I_provider_not_writable_blocks(self):
        result = self.evaluate(provider=acq.ProviderPreflight(True, True, False))
        self.assertIn('PROVIDER_NOT_READY', result['blockers'])

    def test_J_K_tight_requires_explicit_opt_in(self):
        blocked = self.evaluate(fit=acq.FitSnapshot('TIGHT'))
        eligible = self.evaluate(fit=acq.FitSnapshot('TIGHT'), request=acq.PreflightRequest('catalog-v1', True))
        self.assertIn('TIGHT_OPT_IN_REQUIRED', blocked['blockers'])
        self.assertEqual(eligible['status'], 'ELIGIBLE')
        self.assertIn('TIGHT_FIT_EXPLICITLY_ALLOWED', eligible['warnings'])

    def test_L_poor_and_unsupported_fit_block(self):
        for state in ('POOR', 'UNSUPPORTED'):
            with self.subTest(state=state):
                self.assertIn('FIT_' + state, self.evaluate(fit=acq.FitSnapshot(state))['blockers'])

    def test_M_unknown_size_is_never_guessed(self):
        result = self.evaluate(descriptor=descriptor(expected_artifact_size=None))
        self.assertEqual(result['disk']['state'], 'UNKNOWN')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIn('STORAGE_UNKNOWN', result['blockers'])
        self.assertIn('DESCRIPTOR_INCOMPLETE', result['blockers'])

    def test_N_unknown_fit_needs_qualifying_evidence(self):
        blocked = self.evaluate(fit=acq.FitSnapshot('UNKNOWN'))
        qualified = self.evaluate(fit=acq.FitSnapshot('UNKNOWN', True))
        self.assertIn('FIT_UNRESOLVED', blocked['blockers'])
        self.assertEqual(qualified['status'], 'ELIGIBLE')
        self.assertIn('UNKNOWN_FIT_QUALIFIED_BY_SEPARATE_POLICY_EVIDENCE', qualified['warnings'])

    def test_disk_formula_is_exact_integer_policy(self):
        report = acq.disk_requirement(acq.DiskSnapshot(128 * G, 64 * G), 8 * G)
        self.assertEqual(report['transient_reserve'], 2 * G)
        self.assertEqual(report['safety_reserve'], 128 * G * 5 // 100)
        self.assertEqual(report['total_required'], 8 * G + 2 * G + 128 * G * 5 // 100)
        self.assertEqual(report['remaining_after_install'], 64 * G - report['total_required'])

    def test_unknown_inventory_and_revision_are_conservative(self):
        result = self.evaluate(inventory=acq.InventorySnapshot('AMBIGUOUS'),
                               request=acq.PreflightRequest(None))
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIn('INVENTORY_AMBIGUOUS', result['blockers'])
        self.assertIn('CATALOG_REVISION_UNKNOWN', result['blockers'])

    def test_current_catalog_seed_maps_to_unverified_without_name_inference(self):
        loaded = catalog.load_catalog()
        for record in loaded.records:
            mapped = acq.descriptor_from_catalog(record, loaded.revision)
            self.assertEqual(mapped.source_trust_state, 'UNVERIFIED')
            self.assertEqual(mapped.artifact_id, record['source']['upstream_id'])
            self.assertEqual(mapped.expected_artifact_size, record['properties']['weight_size_bytes'])

    def test_input_order_unknowns_and_results_are_deterministic(self):
        a = descriptor(provenance_summary=('z', 'a'))
        b = descriptor(provenance_summary=('a', 'z'))
        result_a = self.evaluate(descriptor=a)
        result_b = self.evaluate(descriptor=b)
        self.assertEqual(result_a['status'], result_b['status'])
        self.assertEqual(result_a['blockers'], result_b['blockers'])
        self.assertIsNone(acq.disk_requirement(acq.DiskSnapshot(None, 1), None)['total_required'])

    def test_malformed_normalized_inputs_are_rejected(self):
        with self.assertRaises(acq.InputError):
            self.evaluate(descriptor=descriptor(expected_artifact_size=True))
        with self.assertRaises(acq.InputError):
            self.evaluate(provider=acq.ProviderPreflight(True, True, True, acquisition_in_progress='yes'))
        with self.assertRaises(acq.InputError):
            self.evaluate(fit=acq.FitSnapshot('MAGIC'))

    def test_provider_minimum_version_blocks_too_old_and_warns_on_unknown(self):
        too_old = self.evaluate(provider=acq.ProviderPreflight(True, True, True, version=(0, 29, 1),
                                                                minimum_version=(0, 30, 0)))
        self.assertEqual(too_old['status'], 'BLOCKED')
        self.assertIn('PROVIDER_TOO_OLD', too_old['blockers'])
        self.assertNotIn('PROVIDER_VERSION_UNKNOWN', too_old['warnings'])
        equal = self.evaluate(provider=acq.ProviderPreflight(True, True, True, version=(0, 30, 0),
                                                              minimum_version=(0, 30, 0)))
        self.assertNotIn('PROVIDER_TOO_OLD', equal['blockers'])
        self.assertNotIn('PROVIDER_VERSION_UNKNOWN', equal['warnings'])
        # Triple compare, not string compare: as strings "0.32.9" > "0.32.12".
        patch = self.evaluate(provider=acq.ProviderPreflight(True, True, True, version=(0, 32, 11),
                                                             minimum_version=(0, 32, 12)))
        self.assertIn('PROVIDER_TOO_OLD', patch['blockers'])
        unknown = self.evaluate(provider=acq.ProviderPreflight(True, True, True, version=None,
                                                               minimum_version=(0, 30, 0)))
        self.assertNotIn('PROVIDER_TOO_OLD', unknown['blockers'])
        self.assertIn('PROVIDER_VERSION_UNKNOWN', unknown['warnings'])
        baseline = self.evaluate()
        no_minimum = self.evaluate(provider=acq.ProviderPreflight(True, True, True, minimum_version=None))
        self.assertEqual((no_minimum['blockers'], no_minimum['warnings']),
                         (baseline['blockers'], baseline['warnings']))
        for bad in ('0.30.0', (0, 30), (-1, 0, 0), (True, 0, 0), (0, 30, 'x')):
            with self.subTest(value=bad):
                with self.assertRaises(acq.InputError):
                    self.evaluate(provider=acq.ProviderPreflight(True, True, True, version=bad))
                with self.assertRaises(acq.InputError):
                    self.evaluate(provider=acq.ProviderPreflight(True, True, True, minimum_version=bad))

    def test_pure_no_io_or_mutation(self):
        values = inputs()
        before = deepcopy(values)
        with ExitStack() as stack:
            for name in ('builtins.open', 'socket.socket', 'socket.create_connection',
                         'urllib.request.urlopen', 'subprocess.Popen', 'os.system'):
                stack.enter_context(patch(name, side_effect=AssertionError('I/O prohibited')))
            result = acq.evaluate_preflight(**values)
        self.assertEqual(values, before)
        self.assertEqual(result['status'], 'ELIGIBLE')


if __name__ == '__main__':
    unittest.main()
