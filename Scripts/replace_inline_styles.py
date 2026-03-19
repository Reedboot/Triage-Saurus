#!/usr/bin/env python3
"""
Scan templates for repeated inline styles, replace repeated static occurrences (>=2) with generated classes,
append CSS to web/static/app.css, and stage changes.
"""
import os
import re
import hashlib
import subprocess
from pathlib import Path

ROOT = Path.cwd()
EXTS = {'.html', '.htm', '.j2', '.jinja2', '.jinja', '.tpl', '.svg', '.xml'}

STYLE_TAG_RE = re.compile(r'(<[A-Za-z0-9\-:\_]+[^>]*?)\sstyle\s*=\s*(?P<q>["\'])(?P<val>.*?)(?P=q)([^>]*?>)', re.DOTALL)
CLASS_RE = re.compile(r'class\s*=\s*(?P<q>["\'])(?P<val>.*?)(?P=q)', re.DOTALL)


def is_static_style(s):
    # skip if contains Jinja template markers
    return ('{' not in s) and ('}' not in s) and ('{{' not in s) and ('}}' not in s) and ('{%' not in s) and ('%}' not in s)


def collect_styles():
    counts = {}
    locations = {}
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # skip .git
        if '.git' in dirpath.split(os.sep):
            continue
        # limit to web/ and Templates/ and Intake/ and root HTMLs
        rel = os.path.relpath(dirpath, ROOT)
        # Only scan common template dirs for safety
        if not (rel == '.' or rel.startswith('web') or rel.startswith('Templates') or rel.startswith('Intake') or rel.startswith('Templates') or rel.startswith('Output')):
            continue
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() not in EXTS:
                continue
            try:
                txt = p.read_text(encoding='utf-8')
            except Exception:
                continue
            for m in STYLE_TAG_RE.finditer(txt):
                val = m.group('val').strip()
                if not val:
                    continue
                if not is_static_style(val):
                    continue
                counts[val] = counts.get(val, 0) + 1
                locations.setdefault(val, []).append(str(p))
            files.append(p)
    return counts, locations, files


def make_class_name(style_value):
    h = hashlib.md5(style_value.encode('utf-8')).hexdigest()[:6]
    return f's-inline-{h}'


def build_css_rule(class_name, style_value):
    # normalize declarations
    decls = [d.strip() for d in style_value.split(';') if d.strip()]
    decls = [d if d.endswith(';') else d + ';' for d in decls]
    body = ' '.join(decls)
    return f'.{class_name} {{ {body} }}\n'


def process_file(path, style_to_class):
    txt = path.read_text(encoding='utf-8')
    changed = False

    def repl(m):
        val = m.group('val').strip()
        if val not in style_to_class:
            return m.group(0)
        cls = style_to_class[val]
        tag = m.group(0)
        # find class attr in tag
        cm = CLASS_RE.search(tag)
        if cm:
            q = cm.group('q')
            existing = cm.group('val')
            # avoid duplicate
            classes = existing.split()
            if cls in classes:
                new_tag = re.sub(r'\sstyle\s*=\s*(?:"[^"]*"|\'"""""\')', '', tag, count=1)
                return new_tag
            new_classes = existing + ' ' + cls
            new_tag = tag[:cm.start()] + f'class={q}{new_classes}{q}' + tag[cm.end():]
            # remove the style attribute
            new_tag = re.sub(r'\sstyle\s*=\s*(?:".*?"|\'.*?\')', '', new_tag, count=1)
            return new_tag
        else:
            # replace the style attribute with class attribute
            q = m.group('q')
            # remove the style attribute substring and insert a class attribute
            style_attr_pattern = re.compile(r"\sstyle\s*=\s*(?:\".*?\"|'.*?')", re.DOTALL)
            new_tag = style_attr_pattern.sub(' class="' + cls + '"', tag, count=1)
            return new_tag

    new_txt = STYLE_TAG_RE.sub(repl, txt)
    if new_txt != txt:
        path.write_text(new_txt, encoding='utf-8')
        changed = True
    return changed


def main():
    counts, locations, files = collect_styles()
    # pick repeated ones
    repeated = {s: c for s, c in counts.items() if c >= 2}
    if not repeated:
        print('No repeated static inline styles found.')
        return
    style_to_class = {s: make_class_name(s) for s in repeated}

    # prepare CSS to append
    css_path = ROOT / 'web' / 'static' / 'app.css'
    css_path.parent.mkdir(parents=True, exist_ok=True)
    existing_css = ''
    if css_path.exists():
        try:
            existing_css = css_path.read_text(encoding='utf-8')
        except Exception:
            existing_css = ''

    append_rules = []
    for s, cls in style_to_class.items():
        if f'.{cls}' in existing_css:
            continue
        append_rules.append(build_css_rule(cls, s))

    edited_files = []
    for p in files:
        changed = process_file(p, style_to_class)
        if changed:
            edited_files.append(str(p))

    # append CSS
    if append_rules:
        with css_path.open('a', encoding='utf-8') as f:
            f.write('\n/* Generated inline style classes */\n')
            for rule in append_rules:
                f.write(rule)
        edited_files.append(str(css_path))

    # git add edited files
    if edited_files:
        try:
            subprocess.check_call(['git', 'add'] + edited_files)
        except subprocess.CalledProcessError as e:
            print('Failed to git add files:', e)

    # print summary
    print(f'Generated {len(style_to_class)} class(es) for {len(repeated)} repeated style value(s).')
    for s, cls in style_to_class.items():
        cnt = repeated[s]
        print(f'  {cls}: "{s}" ({cnt} occurrences)')
    if edited_files:
        print('\nEdited files:')
        for ef in edited_files:
            print('  ' + ef)
    else:
        print('No files were edited.')

if __name__ == '__main__':
    main()
