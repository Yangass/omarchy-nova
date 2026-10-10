import unittest
import catalog

def clean():
    return {'catalog_id': 'test-model',
            'task_tags': ['general', 'coding', 'vision', 'reasoning'],
            'capabilities': {'chat': True, 'tools': None, 'thinking': True, 'coding': None,
                             'vision': True, 'instruction_following': None},
            'metadata': {'release_date': '2026-01-01', 'license': 'MIT',
                         'checked_at': '2026-01-01T00:00:00Z',
                         'provenance': {'method': 'curated', 'references': ['a cited guide'],
                                        'notes': None}}}

class CatalogTagRuleTests(unittest.TestCase):
    def test_shipped_catalog_has_no_violations(self):
        loaded = catalog.load_catalog()
        self.assertEqual(loaded.status, 'ready')
        for record in loaded.records:
            self.assertEqual(catalog.tag_rule_violations(record), [])

    def test_clean_record_all_four_tags(self):
        self.assertEqual(catalog.tag_rule_violations(clean()), [])

    def test_unknown_tags_sorted_best_first(self):
        record = clean()
        record['task_tags'] = ['general', 'fast', 'best']
        record['capabilities']['vision'] = None
        record['capabilities']['thinking'] = None
        self.assertEqual(catalog.tag_rule_violations(record),
                         ['test-model: unknown task tag \'best\'',
                          'test-model: unknown task tag \'fast\''])

    def test_general_without_reference(self):
        record = clean()
        record['task_tags'] = ['general']
        record['capabilities']['vision'] = None
        record['capabilities']['thinking'] = None
        record['metadata']['provenance']['references'] = []
        self.assertEqual(catalog.tag_rule_violations(record),
                         ["test-model: 'general' needs a cited reference"])

    def test_coding_without_reference(self):
        record = clean()
        record['task_tags'] = ['coding']
        record['capabilities']['vision'] = None
        record['capabilities']['thinking'] = None
        record['metadata']['provenance']['references'] = []
        self.assertEqual(catalog.tag_rule_violations(record),
                         ["test-model: 'coding' needs a cited reference"])

    def test_general_with_references_missing(self):
        record = clean()
        record['task_tags'] = ['general']
        record['capabilities']['vision'] = None
        record['capabilities']['thinking'] = None
        del record['metadata']['provenance']['references']
        self.assertEqual(catalog.tag_rule_violations(record),
                         ["test-model: 'general' needs a cited reference"])

    def test_vision_tag_with_capability_none(self):
        record = clean()
        record['task_tags'] = ['vision']
        record['capabilities']['vision'] = None
        record['capabilities']['thinking'] = None
        self.assertEqual(catalog.tag_rule_violations(record),
                         ["test-model: 'vision' must mirror capabilities.vision"])

    def test_vision_capability_true_without_tag(self):
        record = clean()
        record['task_tags'] = []
        record['capabilities']['vision'] = True
        record['capabilities']['thinking'] = None
        self.assertEqual(catalog.tag_rule_violations(record),
                         ["test-model: 'vision' must mirror capabilities.vision"])

    def test_reasoning_tag_with_thinking_none(self):
        record = clean()
        record['task_tags'] = ['reasoning']
        record['capabilities']['thinking'] = None
        record['capabilities']['vision'] = None
        self.assertEqual(catalog.tag_rule_violations(record),
                         ["test-model: 'reasoning' must mirror capabilities.thinking"])

    def test_thinking_true_without_reasoning_tag(self):
        record = clean()
        record['task_tags'] = []
        record['capabilities']['thinking'] = True
        record['capabilities']['vision'] = None
        self.assertEqual(catalog.tag_rule_violations(record),
                         ["test-model: 'reasoning' must mirror capabilities.thinking"])

    def test_malformed_records_never_raise(self):
        self.assertEqual(catalog.tag_rule_violations({}), [])
        self.assertEqual(catalog.tag_rule_violations({'task_tags': None}), [])
        self.assertEqual(catalog.tag_rule_violations({'capabilities': 'x'}), [])

if __name__ == '__main__':
    unittest.main()
