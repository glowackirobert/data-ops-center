"""AI_PLATFORM_PLAN.md Track 4 — evals for the /api/ask agent's SQL
generation. Answers "Opus-low or Haiku", "does the geographic rule
actually prevent the 2.5 s bbox scan", "did adding explain_sql change
anything" — with numbers, not impressions.

Three deterministic graders per question, none needing an LLM judge:
result equality (the agent's own SQL, executed, matched against a golden
query's rows), index hit (scan ratio from the same broker stats the
explain panel shows), and plan shape (EXPLAIN PLAN FOR the agent's SQL
contains no full scan).

Run by hand against the live cluster with a real Anthropic API key and
Pinot reachable (e.g. the debug-ports override locally). Never run in
CI — this file doesn't match tests/'s test*.py discovery pattern; the
pure grading logic it calls is unit-tested in
tests/test_evals_text_to_sql.py instead.

    cd cluster-setup/web-app
    python -m tests.evals.text_to_sql
    python -m tests.evals.text_to_sql --model claude-opus-5 --date 2026-09-10
    python -m tests.evals.text_to_sql --limit 3          # smoke-test the harness itself
"""
import argparse
import datetime
import json
import os
import statistics
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app import agent, pinot  # noqa: E402  (after the sys.path fix above)

_DEFAULT_SET = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'text_to_sql.jsonl')
_GOLDEN_TIMEOUT_S = 30
_EXPLAIN_TIMEOUT_S = 15


def load_cases(path, date):
    """Read the JSONL set, substituting {date} in `question`/`golden_sql` —
    a literal date rather than "yesterday" in English, since agent.py's
    system prompt carries no notion of "today" for the model to resolve a
    relative date against.
    """
    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            case['question'] = case['question'].replace('{date}', date)
            if case.get('golden_sql'):
                case['golden_sql'] = case['golden_sql'].replace('{date}', date)
            cases.append(case)
    return cases


def _canon_value(v):
    """Numeric values are rounded to the nearest whole unit before
    comparing — the agent's own ROUNDDECIMAL precision won't always match
    the golden query's, and this eval cares whether the answer is right,
    not how many decimals it was printed with.
    """
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(v)
    return v


def canon_rows(rows):
    """An order-independent, alias-independent row set: sorts the values
    within each row (the agent's column aliases won't match the golden
    query's, and Pinot doesn't guarantee column or row order without
    ORDER BY) and sorts the resulting rows. Trades a small, accepted risk
    of a coincidental cross-column value collision for not having to
    predict the agent's exact SELECT list — the standard tradeoff
    execution-accuracy graders make.
    """
    return sorted(tuple(sorted((_canon_value(v) for v in r.values()), key=str)) for r in rows)


def rows_match(agent_rows, golden_rows):
    return canon_rows(agent_rows) == canon_rows(golden_rows)


def is_full_scan(explain_rows):
    """True if any value in any column of EXPLAIN PLAN FOR's output
    contains FULL_SCAN. Checks every column rather than a named one
    (e.g. "Operator") since that exact shape wasn't checked against a
    live 1.5.1 controller while writing this — see CLAUDE.md's
    cluster_health note for the same caveat on a different endpoint.
    """
    return any('FULL_SCAN' in str(v) for row in explain_rows for v in row.values())


def grade(case, agent_result, golden_rows, golden_error, explain_rows):
    """Pure grading — no network calls, so this is what
    tests/test_evals_text_to_sql.py actually exercises. run_case (below)
    is the only part of this module that talks to Pinot/Anthropic.
    """
    stats = agent_result.get('stats') or {}
    total_docs = stats.get('totalDocs') or 0
    docs_scanned = stats.get('docsScanned', 0)
    scan_ratio = (docs_scanned / total_docs) if total_docs else None
    max_ratio = case.get('max_scan_ratio', 0.01)

    result_ok = None
    if case.get('check_result', True) and golden_error is None:
        result_ok = rows_match(agent_result.get('rows', []), golden_rows)

    return {
        'id': case['id'],
        'question': case['question'],
        'sql': agent_result.get('sql', ''),
        'result_ok': result_ok,
        'scan_ratio': scan_ratio,
        'index_hit': scan_ratio is not None and scan_ratio <= max_ratio,
        'full_scan': is_full_scan(explain_rows),
        'time_used_ms': agent_result.get('ms'),
        'golden_error': golden_error,
    }


def run_case(case):
    """The live part: ask the agent, run the golden SQL directly (if the
    case has one), explain whatever SQL the agent actually ran. Never
    called by the test suite — see grade() for the part that is.
    """
    agent_result = agent.ask(case['question'])

    golden_rows, golden_error = [], None
    if case.get('golden_sql'):
        try:
            golden_rows, _ms, _stats = pinot._query(case['golden_sql'], timeout=_GOLDEN_TIMEOUT_S)
        except Exception as e:
            golden_error = str(e)

    explain_rows = []
    if agent_result.get('sql'):
        try:
            explain_rows, _ms, _stats = pinot._query(
                f"EXPLAIN PLAN FOR {agent_result['sql']}", timeout=_EXPLAIN_TIMEOUT_S)
        except Exception:
            pass  # a plan we can't explain still gets graded on the other two axes

    return grade(case, agent_result, golden_rows, golden_error, explain_rows)


def print_report(results, model):
    graded = [r for r in results if 'error' not in r]
    errored = [r for r in results if 'error' in r]
    print(f"\n=== {model} — {len(results)} questions, {len(errored)} errored ===")

    checked = [r for r in graded if r['result_ok'] is not None]
    if checked:
        passed = sum(1 for r in checked if r['result_ok'])
        print(f"{'result equality':18}: {passed}/{len(checked)} ({100 * passed / len(checked):.0f}%)")
    if graded:
        hit = sum(1 for r in graded if r['index_hit'])
        print(f"{'index hit':18}: {hit}/{len(graded)} ({100 * hit / len(graded):.0f}%)")
        no_scan = sum(1 for r in graded if not r['full_scan'])
        print(f"{'no full scan':18}: {no_scan}/{len(graded)} ({100 * no_scan / len(graded):.0f}%)")
    times = [r['time_used_ms'] for r in graded if r.get('time_used_ms') is not None]
    if times:
        print(f"{'median timeUsedMs':18}: {statistics.median(times):.0f} ms")

    failures = [r for r in graded
                if r['result_ok'] is False or not r['index_hit'] or r['full_scan']]
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for r in failures:
            flags = []
            if r['result_ok'] is False:
                flags.append('rows')
            if not r['index_hit']:
                ratio = f"{r['scan_ratio']:.4f}" if r['scan_ratio'] is not None else '?'
                flags.append(f'scan={ratio}')
            if r['full_scan']:
                flags.append('full-scan')
            print(f"  {r['id']} [{', '.join(flags)}]: {r['question']}")
            print(f"    sql: {r['sql']}")
            if r.get('golden_error'):
                print(f"    golden SQL error: {r['golden_error']}")

    if errored:
        print(f"\n{len(errored)} error(s) (agent.ask() itself raised):")
        for r in errored:
            print(f"  {r['id']}: {r['error']}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', help='Override ANTHROPIC_MODEL for this run')
    parser.add_argument('--set', default=_DEFAULT_SET, help='Path to the JSONL eval set')
    parser.add_argument('--limit', type=int, help='Only run the first N cases')
    parser.add_argument(
        '--date', default=(datetime.date.today() - datetime.timedelta(days=2)).isoformat(),
        help='Substituted for {date} in questions/golden SQL — pick a date you know has '
             'been ingested; defaults to 2 days ago')
    args = parser.parse_args()

    if args.model:
        agent.ANTHROPIC_MODEL = args.model

    cases = load_cases(args.set, args.date)
    if args.limit:
        cases = cases[:args.limit]

    results = []
    for case in cases:
        print(f"  {case['id']}...", end=' ', flush=True)
        try:
            r = run_case(case)
        except Exception as e:
            r = {'id': case['id'], 'error': str(e)}
        results.append(r)
        if 'error' in r:
            print('ERROR')
        else:
            ok = r['result_ok'] is not False and r['index_hit'] and not r['full_scan']
            print('pass' if ok else 'FAIL')

    print_report(results, args.model or agent.ANTHROPIC_MODEL)


if __name__ == '__main__':
    main()
