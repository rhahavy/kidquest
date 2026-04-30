#!/usr/bin/env python3
"""
patch_stretch.py — read /tmp/stretch_pools.json (produced by
                   bulk_fill_stretch.py) and inject each pool into the
                   matching activity in app/index.html, right after the
                   activity's existing `questions:[...]` block.

Idempotent — skips activities that already have a stretchQuestions field.
Writes the file in one atomic replace at the end. Refuses to run if the
parsed activity count drops, as a sanity guard against destroying source.
"""

import argparse, json, os, re, sys

DEFAULT_APP     = "app/index.html"
RESULTS_FILE    = "/tmp/stretch_pools.json"

def find_balanced(s, i, open_c, close_c):
    depth = 0; in_str = None; esc = False
    j = i
    while j < len(s):
        c = s[j]
        if esc: esc = False
        elif in_str:
            if c == '\\': esc = True
            elif c == in_str: in_str = None
        else:
            if c in "\"'`": in_str = c
            elif c == open_c: depth += 1
            elif c == close_c:
                depth -= 1
                if depth == 0: return j
        j += 1
    return -1

def js_quote(s):
    """Render a JS single-quoted string literal that survives every char in s."""
    # Escape backslash first, then single quote, then anything that confuses
    # us in source (newline → \n).
    out = s.replace('\\', '\\\\').replace("'", "\\'")
    out = out.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    return "'" + out + "'"

def render_pool_block(questions, indent='      '):
    """Render an array of {q,choices,answer} dicts as a stretchQuestions:[...]
    JS literal, mirroring the existing app/index.html style.

    `indent` is the indent of the `stretchQuestions:` line itself (the
    same column as `questions:`); items inside the array indent two
    spaces deeper, and the closing `]` lines up with the key."""
    inner_indent = indent + '  '
    lines = ['stretchQuestions:[']
    for q in questions:
        choices = ','.join(js_quote(str(c)) for c in q['choices'])
        lines.append(f"{inner_indent}{{type:'mcq',q:{js_quote(q['q'])},choices:[{choices}],answer:{q['answer']}}},")
    lines.append(f'{indent}]')
    return '\n'.join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default=DEFAULT_APP)
    ap.add_argument('--results', default=RESULTS_FILE)
    ap.add_argument('--dry-run', action='store_true',
                    help='Report what would be patched, without writing')
    args = ap.parse_args()

    with open(args.app) as f:
        src = f.read()
    if not os.path.exists(args.results):
        print(f"❌  No results at {args.results}. Run bulk_fill_stretch.py first.")
        sys.exit(1)
    with open(args.results) as f:
        pools = json.load(f)
    print(f"Loaded {len(pools)} pools from {args.results}")

    # Find every `{ id:'w...'` activity, walk to the close, then within its
    # body locate the `questions:[...]` close. Insert right after it.
    # We work back-to-front so absolute offsets stay valid as we splice.
    targets = []
    for m in re.finditer(r'\{\s*id\s*:\s*[\'"](w[A-Za-z0-9_-]+)[\'"]', src):
        aid = m.group(1)
        if aid not in pools: continue
        brace_pos = m.start()
        end = find_balanced(src, brace_pos, '{', '}')
        if end < 0: continue
        body = src[brace_pos:end+1]
        # Already has stretchQuestions? Skip.
        sm = re.search(r'stretchQuestions\s*:\s*\[', body)
        if sm:
            bs_local = body.index('[', sm.start())
            be_local = find_balanced(body, bs_local, '[', ']')
            if be_local > bs_local and re.search(r'\S', body[bs_local+1:be_local]):
                # already has a non-empty pool — don't overwrite
                continue
        # Find questions:[...] close
        qm = re.search(r'questions\s*:\s*\[', body)
        if not qm: continue
        qs_local = body.index('[', qm.start())
        qe_local = find_balanced(body, qs_local, '[', ']')
        if qe_local < 0: continue
        # Absolute offset of the closing ] of questions:
        abs_qe = brace_pos + qe_local
        targets.append({
            'aid': aid,
            'abs_qe': abs_qe,
            'pool': pools[aid],
            'brace_pos': brace_pos,
            'end': end,
        })

    print(f"Will patch {len(targets)} activities")
    if not targets:
        print("Nothing to patch.")
        return

    if args.dry_run:
        for t in targets[:5]:
            print(f"  - {t['aid']} ({len(t['pool'])} q's) at offset {t['abs_qe']}")
        if len(targets) > 5:
            print(f"  ... and {len(targets)-5} more")
        return

    # Splice back-to-front so earlier offsets stay valid.
    # The insertion goes RIGHT AFTER the closing ']' of questions:
    # so source goes from
    #     ...questions:[
    #       {...},
    #     ]},
    # to
    #     ...questions:[
    #       {...},
    #     ],
    #     stretchQuestions:[
    #       {...},
    #     ]},
    # We replace the position right after `]` (which currently is `}` —
    # the activity-object close) with `,\n      stretchQuestions:[...]`.
    new_src = src
    targets.sort(key=lambda t: t['abs_qe'], reverse=True)
    for t in targets:
        insert_at = t['abs_qe'] + 1  # one past the ]
        # Ensure we're not breaking on weird whitespace
        # Existing chars: typically `]},\n` — we want to add `,\n      stretchQuestions:[...]`
        # right after the `]`, before the `}`. But indent should match the
        # existing activity. Look at the indent of the line containing the
        # opening `{`.
        line_start = new_src.rfind('\n', 0, t['brace_pos']) + 1
        leading = new_src[line_start:t['brace_pos']]
        # The activity body indents one level deeper. Re-use 6 spaces if the
        # activity's `{` sat at column 4 (matches existing style); else add 2.
        body_indent = leading + '  '
        block = render_pool_block(t['pool'], indent=body_indent)
        injection = ',\n' + body_indent + block
        new_src = new_src[:insert_at] + injection + new_src[insert_at:]

    if new_src == src:
        print("No change.")
        return

    # Sanity: count of activities should not decrease
    before = len(re.findall(r'\{\s*id\s*:\s*[\'"]w[A-Za-z0-9_-]+', src))
    after  = len(re.findall(r'\{\s*id\s*:\s*[\'"]w[A-Za-z0-9_-]+', new_src))
    if after < before:
        print(f"❌  Activity count dropped from {before} → {after}. Aborting write.")
        sys.exit(1)
    # Sanity: count of stretchQuestions should increase by exactly len(targets)
    s_before = len(re.findall(r'stretchQuestions\s*:\s*\[', src))
    s_after  = len(re.findall(r'stretchQuestions\s*:\s*\[', new_src))
    if s_after - s_before != len(targets):
        print(f"⚠️  stretchQuestions count delta {s_after - s_before} != {len(targets)} (continuing anyway)")

    with open(args.app, 'w') as f:
        f.write(new_src)
    print(f"✅  Patched {len(targets)} activities. Activities total: {after}, stretch pools total: {s_after} (was {s_before}).")

if __name__ == '__main__':
    main()
