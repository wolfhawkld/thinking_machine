import copy
from collections import Counter
import unittest

import nonthinking_budget_audit_20260910 as m


class AuditTests(unittest.TestCase):
    def test_selection_ignores_outputs_and_input_order(self):
        pairs = [{'construction_stratum': s, 'pair_anchor_sha256': f'{s}-{i}', 'outcome': 0}
                 for s in m.STRATA for i in range(6)]
        selected = m.select_pairs(pairs)
        changed = copy.deepcopy(pairs[::-1])
        for p in changed: p['outcome'] = 999
        self.assertEqual([p['pair_anchor_sha256'] for p in selected],
                         [p['pair_anchor_sha256'] for p in m.select_pairs(changed)])
        self.assertEqual(Counter(p['construction_stratum'] for p in selected), {s: 2 for s in m.STRATA})
        with self.assertRaises(ValueError): m.select_pairs(pairs[:-1])

    def test_real_bindings_and_draft_only_cap_varies(self):
        audit, public = m.construct()
        self.assertEqual(audit['historical']['output_tokens']['max'], 14)
        self.assertEqual(audit['historical']['finish_reasons'], {'stop': 48})
        self.assertEqual(audit['proposed_maximum_completion_tokens'], 151552)
        rows = public['tasks']
        self.assertEqual(len(rows), 32)
        for left, right in zip(rows[::2], rows[1::2]):
            self.assertEqual({left['max_tokens'], right['max_tokens']}, {256, 8192})
            self.assertEqual({k:v for k,v in left.items() if k != 'max_tokens'},
                             {k:v for k,v in right.items() if k != 'max_tokens'})
        self.assertFalse(audit['live_authorized'])


if __name__ == '__main__': unittest.main()
