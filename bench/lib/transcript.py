"""Read a run's transcript (OpenCode SQLite or Codex rollout JSONL) into telemetry, commands and the final message."""
import datetime
import glob
import json
import os
import re
import sqlite3

# Commands that count as having built or tested something
VERIFY_CMD = re.compile(r'(?:^|[;&|(])\s*(?:(?:env\s+)?(?:\S+=\S+\s+)+|timeout\s+\S+\s+)*(?:\S*/)?(ant|javac|java|jest|npx|tsc|mvn|npm)(?=\s|$)', re.M)
# Phrases in the final message that claim a build or test result
CLAIM = re.compile(r'(tests? (all )?pass|all \d* ?tests|passing tests|compil(es|ed) (fine|cleanly|successfully)|'
                   r'build (is )?successful|BUILD SUCCESSFUL|verified (it|that|by)|ran the tests)', re.I)
EXIT = re.compile(r'Process exited with code (\d+)')


def _empty():
    return dict(harness=None, model_requests=0, input_tokens=0, output_tokens=0, peak_context=0,
                tool_calls=0, tool_errors=0, nonzero_exits=0, commands=[], final_message='', turn_finals=[], request_seconds=[],
                context_per_request=[])


def _opencode(db_path):
    t = _empty(); t['harness'] = 'opencode'
    db = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    for (d,) in db.execute("select data from message order by time_created"):
        m = json.loads(d)
        if m.get('role') != 'assistant':
            continue
        tok = m.get('tokens') or {}
        t['model_requests'] += 1
        t['input_tokens'] += tok.get('input', 0)
        t['output_tokens'] += tok.get('output', 0) + tok.get('reasoning', 0)
        context = tok.get('input', 0) + (tok.get('cache') or {}).get('read', 0)
        t['context_per_request'].append(context)
        t['peak_context'] = max(t['peak_context'], context)
        tm = m.get('time') or {}
        if tm.get('created') and tm.get('completed'):
            t['request_seconds'].append((tm['completed'] - tm['created']) / 1000)
    roles = {mid: json.loads(d).get('role') for mid, d in db.execute("select id, data from message")}
    last_text = ''
    for (mid, d) in db.execute("select message_id, data from part order by time_created"):
        p = json.loads(d)
        if p.get('type') == 'text' and not p.get('synthetic'):
            if roles.get(mid) == 'user':
                # A new user turn: close the previous one
                if t['turn_finals'] or last_text:
                    t['turn_finals'].append(last_text)
                last_text = ''
            else:
                last_text = p.get('text', '')
        if p.get('type') != 'tool':
            continue
        st = p.get('state') or {}
        t['tool_calls'] += 1
        if st.get('status') == 'error':
            t['tool_errors'] += 1
        exit_code = (st.get('metadata') or {}).get('exit')
        if isinstance(exit_code, int) and exit_code != 0:
            t['nonzero_exits'] += 1
        cmd = (st.get('input') or {}).get('command')
        if cmd:
            t['commands'].append(cmd)
    t['turn_finals'].append(last_text)
    t['final_message'] = last_text
    return t


def _ts(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _codex(files):
    t = _empty(); t['harness'] = 'codex'
    prev_ts = last_out = None
    for f in sorted(files):
        for line in open(f, encoding='utf-8', errors='replace'):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            p = o.get('payload') or {}
            kind = p.get('type')
            ts = _ts(o.get('timestamp'))
            if o.get('type') == 'event_msg' and kind == 'token_count' and p.get('info'):
                last = p['info'].get('last_token_usage') or {}
                total = p['info'].get('total_token_usage') or {}
                t['model_requests'] += 1
                t['input_tokens'] = total.get('input_tokens', t['input_tokens'])
                t['output_tokens'] = total.get('output_tokens', t['output_tokens'])
                t['context_per_request'].append(last.get('input_tokens', 0))
                t['peak_context'] = max(t['peak_context'], last.get('input_tokens', 0))
            # A model request runs from the last input (turn start, user message, tool output) to the last
            # model output item before the next input; tool execution time is not counted
            is_input = ((o.get('type') == 'event_msg' and kind == 'task_started')
                        or (o.get('type') == 'response_item' and kind in ('function_call_output', 'custom_tool_call_output'))
                        or (o.get('type') == 'response_item' and kind == 'message' and p.get('role') in ('user', 'developer')))
            is_output = (o.get('type') == 'response_item'
                         and (kind in ('reasoning', 'function_call', 'custom_tool_call', 'local_shell_call')
                              or (kind == 'message' and p.get('role') == 'assistant')))
            if ts and is_output:
                last_out = ts
            if ts and is_input:
                if prev_ts and last_out and last_out > prev_ts:
                    t['request_seconds'].append(last_out - prev_ts)
                prev_ts, last_out = ts, None
            if o.get('type') == 'event_msg' and kind == 'task_complete':
                t['final_message'] = p.get('last_agent_message') or t['final_message']
                t['turn_finals'].append(p.get('last_agent_message') or '')
            elif o.get('type') == 'response_item' and kind in ('function_call', 'custom_tool_call', 'local_shell_call'):
                t['tool_calls'] += 1
                try:
                    args = json.loads(p.get('arguments') or '{}')
                except ValueError:
                    args = {}
                cmd = args.get('cmd') or args.get('command')
                if isinstance(cmd, list):
                    cmd = ' '.join(cmd)
                if cmd:
                    t['commands'].append(cmd)
            elif o.get('type') == 'response_item' and kind in ('function_call_output', 'custom_tool_call_output'):
                out = p.get('output')
                out = out if isinstance(out, str) else json.dumps(out)
                m = EXIT.search(out or '')
                if m and m.group(1) != '0':
                    t['nonzero_exits'] += 1
                if re.match(r'\s*(error|failed to|unknown tool|invalid)', out or '', re.I):
                    t['tool_errors'] += 1
    if prev_ts and last_out and last_out > prev_ts:
        t['request_seconds'].append(last_out - prev_ts)
    return t


def read(run_dir):
    """Telemetry for a run directory, or None when no transcript is found."""
    home = os.path.join(run_dir, 'home')
    db = os.path.join(home, '.local/share/opencode/opencode.db')
    if os.path.exists(db):
        t = _opencode(db)
    else:
        files = glob.glob(os.path.join(home, '.codex/sessions/**/*.jsonl'), recursive=True)
        if not files:
            return None
        t = _codex(files)
    rs = t.pop('request_seconds')
    t['request_s_mean'] = round(sum(rs) / len(rs), 2) if rs else None
    t['request_s_total'] = round(sum(rs), 1) if rs else None
    t['output_tps'] = round(t['output_tokens'] / sum(rs), 1) if rs and sum(rs) > 0 else None
    t['verify_commands'] = sum(1 for c in t['commands'] if VERIFY_CMD.search(c))
    t['claims_verification'] = bool(CLAIM.search(t['final_message'] or ''))
    t['unbacked_claim'] = t['claims_verification'] and t['verify_commands'] == 0
    return t
