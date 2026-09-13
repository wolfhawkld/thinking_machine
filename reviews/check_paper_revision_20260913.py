"""Offline editorial assertions; never calls providers or alters experiment artifacts."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    read = lambda name: (ROOT / name).read_text()
    main_text = read('paper-manuscript-en-20260913.md')
    supplement = read('paper-supplement-en-20260913.md')
    analysis = json.loads(read('artifacts/microloop-revised-live-20260911/analysis.json'))
    conditions = ('active', 'random', 'repeat')
    for ordinal in range(12):
        values = ['/'.join(str(s['test_correct']) for s in
                  analysis['conditions'][c]['world_rows'][ordinal]['stages']) for c in conditions]
        expected = '| ' + str(ordinal) + ' | ' + ' | '.join(values) + ' |'
        assert expected in supplement, expected
    for condition in conditions:
        metrics = analysis['conditions'][condition]['stage_metrics']
        for metric in metrics:
            assert f"{100 * metric['mean_test_accuracy']:.2f}%" in main_text
            assert metric['valid'] == metric['visible_consistent'] == 12
            assert metric['full_domain_exact'] == 0
    finals = [analysis['conditions'][c]['stage_metrics'][2]['mean_test_accuracy'] for c in conditions]
    assert f'{100 * (finals[1] - finals[0]):.2f}' == '7.94'
    assert f'{100 * (finals[0] - finals[2]):.2f}' == '15.23'
    assert f'{100 * (finals[1] - finals[2]):.2f}' == '23.18'
    rows = analysis['conditions']['active']['world_rows']
    assert [sum(r['gain'] > 0 for r in rows), sum(r['gain'] < 0 for r in rows),
            sum(r['gain'] == 0 for r in rows)] == [4, 7, 1]
    baseline = json.loads(read('artifacts/microloop-baselines-20260910/summary.json'))
    for candidate in baseline['candidates']:
        values = [f"{100 * baseline['conditions'][c]['stages']['2'][candidate]['mean_test_accuracy']:.2f}%"
                  for c in ('balanced', 'random', 'repeat')]
        assert ' | '.join(values) in supplement, (candidate, values)
    complete = json.loads(read('artifacts/microloop-revised-live-20260911/complete.json'))
    assert complete['physical_attempts'] == complete['formal_slots'] == 108
    assert complete['known_input_tokens'] + complete['known_output_tokens'] == 1081946
    old_main = read('paper-manuscript-en-20260910.md')
    for line in old_main.splitlines():
        if line.startswith('|'):
            assert line in main_text, ('Historical main table changed', line)
    old_supp = read('paper-supplement-en-20260910.md')
    for line in old_supp.splitlines():
        if line.startswith('|'):
            assert line in supplement, ('Historical supplement table changed', line)
    reference = lambda text: text.split('## References\n', 1)[1].split('## Supplement and Availability')[0]
    assert reference(main_text) == reference(old_main)
    files = ['paper-manuscript-en-20260913.md', 'paper-supplement-en-20260913.md',
             'paper-claim-evidence-status-20260913.md', 'paper-claims-and-structure-20260913.md']
    link_count = 0
    for name in files:
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', read(name)):
            if target.startswith(('https:', 'http:', '#')):
                continue
            assert (ROOT / target.split('#')[0]).exists(), (name, target)
            link_count += 1
    print(json.dumps({'world_rows_checked': 12, 'stage_scores_checked': 108,
        'stage_means_checked': 9, 'baseline_cells_checked': 15,
        'historical_tables_and_references_preserved': True,
        'existing_local_links_checked': link_count, 'provider_calls': 0}, indent=2))


if __name__ == '__main__':
    main()
